"""Restoration engine: "How long could restoration take?"

Monte Carlo over crew schedules. Each run:
  1. job hours = labor hours x quantity x travel x nonproductive x access x noise
  2. jobs can't start until assessed (1-3 h after they're reported)
  3. not-yet-found damage is added at random (fewer as the storm ages)
  4. crews take jobs critical-facility first, then upstream first (step restoration)
  5. this premise is back when every job on its upline path is done
The spread across ~1,000 runs gives P10 / P50 / P90.
"""
from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from .data import DEPTH, Dataset, get_dataset

NOISE_SIGMA = 0.35
DAMAGE_WORDS = {
    "pole_replace": ("broken pole", "broken poles"),
    "crossarm_replace": ("broken crossarm", "broken crossarms"),
    "insulator_replace": ("damaged insulator", "damaged insulators"),
    "conductor_repair": ("downed wire span", "downed wire spans"),
    "tree_removal": ("tree on the line", "trees on the line"),
    "transformer_replace": ("damaged transformer", "damaged transformers"),
    "fuse_replace": ("blown fuse", "blown fuses"),
}
WHERE_WORDS = {"BRK": "on the main line", "REC": "upstream of you", "FUSE": "on your street's branch", "XFMR": "at your transformer"}


@dataclass
class Job:
    device_id: str
    damage_type: str
    quantity: int = 1
    report_h: float = 0.0  # hours after storm start when it was reported
    done: bool = False
    source: str = "reported"  # reported | what_if | hidden
    incident_id: str | None = None


@dataclass
class Scenario:
    crews: int = 3
    access: float = 1.0  # 1.0 normal roads, 1.5 = blocked/flooded roads slow every job
    now_h: float = 0.0
    hidden_lambda: float = 1.0  # expected not-yet-found damage jobs on the circuit
    jobs: list[Job] = field(default_factory=list)


def damage_label(damage_type: str, n: int) -> str:
    one, many = DAMAGE_WORDS.get(damage_type, (damage_type, damage_type))
    return f"{n} {many}" if n > 1 else f"a {one}" if one[0] not in "aeiou" else f"an {one}"


def default_crews(wind_mph: float) -> int:
    """Bigger storms spread crews thinner (challenge assumption)."""
    return max(1, int(round(4 - (wind_mph - 40) / 20)))


def _job_hours(ds: Dataset, j: Job) -> float:
    return ds.labor_hours[j.damage_type] * j.quantity * ds.indirect["travel"] * ds.indirect["nonproductive"]


def _schedule(jobs, crews: int, start_h: float) -> list[float]:
    """jobs: list of (priority_key, avail_h, duration). Returns finish time per job."""
    order = sorted(range(len(jobs)), key=lambda i: jobs[i][0])
    free = [start_h] * crews
    heapq.heapify(free)
    finish = [0.0] * len(jobs)
    for i in order:
        _, avail, dur = jobs[i]
        t = heapq.heappop(free)
        end = max(t, avail) + dur
        finish[i] = end
        heapq.heappush(free, end)
    return finish


def _hidden_job(ds: Dataset, rng, circuit_devices, now_h) -> Job:
    dev = circuit_devices[rng.integers(len(circuit_devices))]
    dtype = rng.choice(["pole_replace", "conductor_repair", "tree_removal", "crossarm_replace"])
    return Job(dev, str(dtype), 1, now_h + float(rng.uniform(0.5, 6)), source="hidden")


def simulate(premise_id: str, sc: Scenario, n_sims: int = 1000, seed: int = 0, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    rng = np.random.default_rng(seed)
    path = ds.path(premise_id)
    path_set = set(path)
    cid = ds.circuit_of(premise_id)
    circuit_devices = ds.devices.loc[ds.devices.circuit_id == cid, "device_id"].tolist()
    prio = ds.priority_devices()
    restore, stage1, any_on_path = [], [], 0
    for _ in range(n_sims):
        # Schedule the whole storm from its start (done jobs included, since they used
        # crew time), so an estimate counts down on its own as hours pass.
        jobs = list(sc.jobs)
        for _ in range(rng.poisson(sc.hidden_lambda)):
            jobs.append(_hidden_job(ds, rng, circuit_devices, sc.now_h))
        rows = []
        for j in jobs:
            depth = DEPTH[ds.device_type(j.device_id)]
            avail = j.report_h + rng.uniform(1, 3)
            dur = _job_hours(ds, j) * sc.access * rng.lognormal(0, NOISE_SIGMA)
            rows.append(((j.device_id not in prio, depth, avail), avail, dur))
        finish = _schedule(rows, max(1, sc.crews), 0.0)
        # An open job the schedule says "should be done" isn't: it's running late.
        finish = [f if j.done or f > sc.now_h else sc.now_h + rng.exponential(1.0) for j, f in zip(jobs, finish)]
        on_path = [f for j, f in zip(jobs, finish) if j.device_id in path_set and not j.done]
        if on_path:
            any_on_path += 1
            restore.append(max(on_path))
            upstream = [f for j, f in zip(jobs, finish) if j.device_id in path_set and not j.done
                        and DEPTH[ds.device_type(j.device_id)] <= 2]
            stage1.append(max(upstream) if upstream else sc.now_h)
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
    relative to our raw guess. Never earlier than 15 minutes from now."""
    mid = max(now_h + h["p50"], 0.25)
    out = {k: round(max(mid * float(np.exp(calib[k])) - now_h, 0.25), 1) for k in ("p10", "p50", "p90")}
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
        "travel_multiplier": ds.indirect["travel"],
        "nonproductive_multiplier": ds.indirect["nonproductive"],
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
                incident_id=r.incident_id) for r in records.itertuples()]


def prestorm_scenario(premise_id: str, storm: dict, ds: Dataset | None = None) -> Scenario:
    """Before the storm: no reported damage yet, so everything is 'not yet found'
    damage, sized from how much damage this circuit took in past storms of similar strength."""
    ds = ds or get_dataset()
    cid = ds.circuit_of(premise_id)
    past = ds.storms.loc[(ds.storms.max_wind_mph - storm["max_wind_mph"]).abs() <= 12, "event_id"]
    dmg = ds.damage.loc[(ds.damage.circuit_id == cid) & ds.damage.event_id.isin(past)]
    per_storm = len(dmg) / max(1, len(past))
    return Scenario(crews=default_crews(storm["max_wind_mph"]), hidden_lambda=max(1.0, per_storm))
