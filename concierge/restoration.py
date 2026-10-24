"""Restoration engine: "How long could restoration take?"

Monte Carlo over repair jobs. Each run:
  1. job hours = (labor hours x quantity + access adders) x indirect multipliers x access x noise
  2. not-yet-found damage is added at random
  3. this premise is back when every job on its upline path is done
The spread across ~1,000 runs gives P10 / P50 / P90.

Two ways to time the jobs:
  - With learned delays (Scenario.delays, from past storms; see delays.py): damage not found yet
    gets a report time drawn from when past storms' damage was reported, and each job waits a
    learned time (assessment, queue, logistics) before its work, scaled by crews vs. the usual crews.
  - Without (scenarios built by hand): jobs are assessed 1-3 h after reporting, unfound damage
    turns up within 6 h, and crews take jobs critical-facility first, then upstream first.
"""
from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from .data import DEPTH, Dataset, get_dataset
from .delays import Delays, learn, size_for_wind

NOISE_SIGMA = 0.35
DAMAGE_WORDS = {
    "pole_replace": ("broken pole", "broken poles"),
    "crossarm_replace": ("broken crossarm", "broken crossarms"),
    "insulator_replace": ("damaged insulator", "damaged insulators"),
    "conductor_repair": ("downed wire span", "downed wire spans"),
    "tree_removal": ("tree on the line", "trees on the line"),
    "transformer_replace": ("damaged transformer", "damaged transformers"),
    "fuse_replace": ("blown fuse", "blown fuses"),
    "secondary_repair": ("damaged service wire", "damaged service wires"),
    "recloser_replace": ("damaged recloser", "damaged reclosers"),
    "switch_repair": ("damaged switch", "damaged switches"),
    "equipment_repair": ("piece of damaged equipment", "pieces of damaged equipment"),
    "light_repair": ("broken streetlight", "broken streetlights"),
}
WHERE_WORDS = {"BRK": "on the main line", "REC": "upstream of you", "SECT": "upstream of you", "STEP": "upstream of you",
               "FUSE": "on your street's branch", "XFMR": "at your transformer"}
HIDDEN_DAMAGE = ["pole_replace", "conductor_repair", "tree_removal", "crossarm_replace"]


@dataclass
class Job:
    device_id: str
    damage_type: str
    quantity: int = 1
    report_h: float = 0.0  # hours after storm start when it was reported
    done: bool = False
    source: str = "reported"  # reported | what_if | hidden
    incident_id: str | None = None
    extra_hours: float = 0.0  # access / special-equipment adders (person-hours)


@dataclass
class Scenario:
    crews: int = 3
    access: float = 1.0  # 1.0 normal roads, 1.5 = blocked/flooded roads slow every job
    now_h: float = 0.0
    hidden_lambda: float = 1.0  # expected not-yet-found damage jobs on the circuit
    jobs: list[Job] = field(default_factory=list)
    delays: Delays | None = None  # learned from past storms; None = the fixed assumptions
    base_crews: int | None = None  # crews the learned waits reflect (what-if crews scale waits against it)


def damage_label(damage_type: str, n: int) -> str:
    one, many = DAMAGE_WORDS.get(damage_type, (damage_type, damage_type))
    return f"{n} {many}" if n > 1 else f"a {one}" if one[0] not in "aeiou" else f"an {one}"


def default_crews(wind_mph: float) -> int:
    """Bigger storms spread crews thinner (challenge assumption)."""
    return max(1, int(round(4 - (wind_mph - 40) / 20)))


def _job_hours(ds: Dataset, j: Job) -> float:
    """Mock data: travel x nonproductive. Real data: indirect labor multiplier x 1/crew size."""
    return (ds.labor_hours[j.damage_type] * j.quantity + j.extra_hours) * float(np.prod(list(ds.indirect.values())))


def _schedule(order, avail, dur, crews: int, start_h: float) -> np.ndarray:
    """Crews take jobs in `order`; each starts when a crew is free and the job is assessed."""
    free = [start_h] * crews
    heapq.heapify(free)
    finish = np.empty(len(avail))
    for i in order:
        end = max(heapq.heappop(free), avail[i]) + dur[i]
        finish[i] = end
        heapq.heappush(free, end)
    return finish


@lru_cache(maxsize=256)
def _circuit_arrays(ds: Dataset, cid: str):
    """Devices a not-yet-found job can land on: depth and critical-facility flag per device."""
    devs = ds.devices.loc[ds.devices.circuit_id == cid, "device_id"].tolist()
    prio = ds.priority_devices()
    return devs, np.array([DEPTH[ds.device_type(d)] for d in devs]), np.array([d in prio for d in devs])


def simulate(premise_id: str, sc: Scenario, n_sims: int = 1000, seed: int = 0, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    rng = np.random.default_rng(seed)
    path_set = set(ds.path(premise_id))
    prio = ds.priority_devices()
    c_devs, c_depth, c_prio = _circuit_arrays(ds, ds.circuit_of(premise_id))
    c_on_path = np.array([d in path_set for d in c_devs])
    hidden_types = [d for d in HIDDEN_DAMAGE if d in ds.labor_hours]
    hidden_hours = np.array([_job_hours(ds, Job("", d)) for d in hidden_types])
    # Reported jobs, as arrays (the same every run)
    jobs = sc.jobs
    r_depth = np.array([DEPTH[ds.device_type(j.device_id)] for j in jobs], dtype=int)
    r_prio = np.array([j.device_id in prio for j in jobs], dtype=bool)
    r_hours = np.array([_job_hours(ds, j) for j in jobs])
    r_report = np.array([j.report_h for j in jobs])
    r_done = np.array([j.done for j in jobs], dtype=bool)
    r_on_path = np.array([j.device_id in path_set for j in jobs], dtype=bool)
    restore, stage1, any_on_path = [], [], 0
    for _ in range(n_sims):
        # Schedule the whole storm from its start (done jobs included, since they used
        # crew time), so an estimate counts down on its own as hours pass.
        k = rng.poisson(sc.hidden_lambda)
        dev_i = rng.integers(len(c_devs), size=k)
        depth = np.concatenate([r_depth, c_depth[dev_i]])
        is_prio = np.concatenate([r_prio, c_prio[dev_i]])
        hours = np.concatenate([r_hours, hidden_hours[rng.integers(len(hidden_types), size=k)]])
        d = sc.delays
        hidden_report = d.sample_lags_after(rng, sc.now_h, k) if d else sc.now_h + rng.uniform(0.5, 6, size=k)
        report = np.concatenate([r_report, hidden_report])
        done = np.concatenate([r_done, np.zeros(k, dtype=bool)])
        on_path = np.concatenate([r_on_path, c_on_path[dev_i]])
        dur = hours * sc.access * rng.lognormal(0, NOISE_SIGMA, size=len(report))
        if d:
            # Learned waits already include queueing behind other jobs, so no crew schedule here;
            # fewer crews than usual stretch the wait, more crews shrink it.
            scale = (sc.base_crews or sc.crews) / max(1, sc.crews)
            finish = report + d.sample_waits(rng, len(report)) * scale + dur
            late = ~done & (finish <= sc.now_h)  # still open, so it's taking longer than drawn
            if late.any():
                waited = sc.now_h - report[late]
                finish[late] = np.maximum(report[late] + d.sample_waits_over(rng, waited / scale) * scale + dur[late],
                                          sc.now_h + 0.25)
        else:
            avail = report + rng.uniform(1, 3, size=len(report))
            order = np.lexsort((avail, depth, ~is_prio))  # critical facility first, then upstream first
            finish = _schedule(order, avail, dur, max(1, sc.crews), 0.0)
            # An open job the schedule says "should be done" isn't: it's running late.
            late = ~done & (finish <= sc.now_h)
            finish[late] = sc.now_h + rng.exponential(1.0, size=int(late.sum()))
        mine = on_path & ~done
        if mine.any():
            any_on_path += 1
            restore.append(finish[mine].max())
            upstream = mine & (depth <= 2)
            stage1.append(finish[upstream].max() if upstream.any() else sc.now_h)
    if not restore:
        return {"status": "no_damage_on_path", "p_affected": 0.0}
    r = np.array(restore) - sc.now_h
    q10, q50, q90 = np.percentile(r, [10, 50, 90])
    return {
        "status": "ok",
        "p_affected": round(any_on_path / n_sims, 2),
        "hours_from_now": {"p10": round(float(q10), 1), "p50": round(float(q50), 1), "p90": round(float(q90), 1)},
        "upstream_fixed_p50": round(float(np.median(np.array(stage1) - sc.now_h)), 1) if stage1 else None,
        "n_sims": n_sims,
    }


def drivers(premise_id: str, sc: Scenario, ds: Dataset | None = None) -> list[str]:
    """Plain-language reasons, biggest first."""
    ds = ds or get_dataset()
    path = set(ds.path(premise_id))
    groups = Counter()
    hours = Counter()
    for j in sc.jobs:
        if j.done or j.device_id not in path:
            continue
        key = (j.damage_type, ds.device_type(j.device_id))
        groups[key] += j.quantity
        hours[key] += _job_hours(ds, j)
    out = [f"{damage_label(dt, n)} {WHERE_WORDS[dev]}" for (dt, dev), n in sorted(groups.items(), key=lambda kv: -hours[kv[0]])]
    other = sum(1 for j in sc.jobs if not j.done and j.device_id not in path)
    if other:
        out.append(f"{sc.crews} crew{'s' if sc.crews != 1 else ''} also fixing {other} other repair{'s' if other != 1 else ''} on your circuit")
    if sc.access > 1.0:
        out.append("blocked or flooded roads are slowing crews")
    cid = ds.circuit_of(premise_id)
    if (ds.priority.circuit_id == cid).any():
        out.append("a critical facility on your circuit is restored first")
    return out


def apply_calibration(h: dict, calib: dict, now_h: float) -> dict:
    """Rescale the raw P10/P50/P90 using offsets learned in the backtest.

    calib holds log-ratio quantiles of (actual / raw P50), both measured as hours
    since the storm started, so the new range is where reality actually landed
    relative to our raw guess. Never earlier than about 20 minutes from now."""
    mid = max(now_h + h["p50"], 0.25)
    out = {k: max(round(mid * float(np.exp(calib[k])) - now_h, 1), 0.3) for k in ("p10", "p50", "p90")}
    out["p10"] = min(out["p10"], out["p50"])
    out["p90"] = max(out["p90"], out["p50"])
    return out


def estimate(premise_id: str, sc: Scenario, n_sims: int = 1000, seed: int = 0, ds: Dataset | None = None,
             calib: dict | None = None) -> dict:
    ds = ds or get_dataset()
    res = simulate(premise_id, sc, n_sims, seed, ds)
    res["now_h"] = sc.now_h
    if calib and res.get("hours_from_now"):
        res["raw_hours_from_now"] = res["hours_from_now"]
        res["hours_from_now"] = apply_calibration(res["hours_from_now"], calib, sc.now_h)
        res["calibrated"] = True
    res["drivers"] = drivers(premise_id, sc, ds)
    res["assumptions"] = {
        "crews": sc.crews,
        "road_access": "normal" if sc.access <= 1.0 else "slowed",
        "labor_multipliers": dict(ds.indirect),
        "expected_unfound_damage": round(sc.hidden_lambda, 1),
    }
    p = res.get("hours_from_now")
    res["step_restoration"] = bool(p and res.get("upstream_fixed_p50") is not None and res["upstream_fixed_p50"] < p["p50"] - 1)
    return res


def explain_change(prev: Scenario, new: Scenario, premise_id: str, ds: Dataset | None = None) -> list[str]:
    """Why did the estimate move? Compares two scenarios' inputs."""
    ds = ds or get_dataset()
    path = set(ds.path(premise_id))
    key = lambda j: (j.incident_id or id(j), j.device_id, j.damage_type)
    prev_open = {key(j): j for j in prev.jobs if not j.done}
    new_open = {key(j): j for j in new.jobs if not j.done}
    reasons = []
    added = [j for k, j in new_open.items() if k not in prev_open]
    fixed = [j for k, j in prev_open.items() if k not in new_open]
    for j in added:
        if j.device_id in path:
            reasons.append(f"crews found {damage_label(j.damage_type, j.quantity)} {WHERE_WORDS[ds.device_type(j.device_id)]}")
    n_other = sum(1 for j in added if j.device_id not in path)
    if n_other:
        reasons.append(f"{n_other} new repair{'s' if n_other > 1 else ''} found elsewhere on your circuit")
    for j in fixed:
        if j.device_id in path:
            reasons.append(f"crews fixed {damage_label(j.damage_type, j.quantity)} {WHERE_WORDS[ds.device_type(j.device_id)]}")
    if new.crews != prev.crews:
        reasons.append(f"{'more' if new.crews > prev.crews else 'fewer'} crews on your circuit ({prev.crews} to {new.crews})")
    if new.access != prev.access:
        reasons.append("road conditions changed")
    return reasons


def jobs_from_records(records) -> list[Job]:
    return [Job(r.device_id, r.damage_type, int(r.quantity), float(r.report_h), bool(getattr(r, "done", False)),
                incident_id=r.incident_id, extra_hours=float(getattr(r, "extra_hours", 0.0))) for r in records.itertuples()]


def prestorm_scenario(premise_id: str, storm: dict, ds: Dataset | None = None, exclude: tuple[str, ...] = ()) -> Scenario:
    """Before the storm: no reported damage yet, so everything is 'not yet found'
    damage, sized from how much damage this circuit took in past storms of similar strength.
    `exclude`: storms to leave out (a storm being backtested must not inform its own forecast)."""
    ds = ds or get_dataset()
    cid = ds.circuit_of(premise_id)
    storms = ds.storms.loc[~ds.storms.event_id.isin(exclude)]
    past = storms.loc[(storms.max_wind_mph - storm["max_wind_mph"]).abs() <= 12, "event_id"]
    dmg = ds.damage.loc[(ds.damage.circuit_id == cid) & ds.damage.event_id.isin(past)]
    per_storm = len(dmg) / max(1, len(past))
    # Crews: what this circuit had in those storms, if recorded; otherwise the wind-based assumption.
    crews = dmg.crew_count.dropna()
    crews = int(round(crews.median())) if len(crews) else default_crews(storm["max_wind_mph"])
    delays = learn(ds, size_for_wind(ds, storm["max_wind_mph"]), tuple(exclude))
    return Scenario(crews=crews, hidden_lambda=max(1.0, per_storm), delays=delays, base_crews=crews)
