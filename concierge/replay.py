"""Storm replay: step through a real past storm hour by hour.

At each step we only use damage that had been reported by then (and mark jobs
done once ARCOS says they were repaired), re-run the restoration engine, and
record what the customer would have been told and why.
"""
from __future__ import annotations

import math

import pandas as pd

from .data import Dataset, get_dataset
from .restoration import Job, Scenario, default_crews, estimate, explain_change, prestorm_scenario
from .rules import should_send_update


def event_records(ds: Dataset, event_id: str, circuit_id: str) -> pd.DataFrame:
    start = ds.storms.loc[ds.storms.event_id == event_id, "start_time"].iloc[0]
    recs = ds.damage.loc[(ds.damage.event_id == event_id) & (ds.damage.circuit_id == circuit_id)].copy()
    recs["report_h"] = (recs.report_time - start).dt.total_seconds() / 3600
    recs["done_h"] = (recs.repair_complete_time - start).dt.total_seconds() / 3600
    return recs


def hidden_lambda(prestorm: float, known: int, t: float) -> float:
    """Expected damage not found yet: shrinks as reports come in and time passes."""
    return max(0.2, prestorm - known) * math.exp(-t / 6)


def scenario_at(ds: Dataset, premise_id: str, event_id: str, t: float, what_if: dict | None = None,
                recs: pd.DataFrame | None = None, base_lambda: float | None = None) -> Scenario:
    what_if = what_if or {}
    cid = ds.circuit_of(premise_id)
    recs = recs if recs is not None else event_records(ds, event_id, cid)
    storm = ds.storms.loc[ds.storms.event_id == event_id].iloc[0]
    known = recs.loc[recs.report_h <= t]
    jobs = [Job(r.device_id, r.damage_type, int(r.quantity), float(r.report_h), bool(r.done_h <= t),
                incident_id=r.incident_id, extra_hours=float(getattr(r, "extra_hours", 0.0))) for r in known.itertuples()]
    found_at = float(what_if.get("extra_poles_at", 3.0))  # what-if poles are "found" at this storm hour
    if what_if.get("extra_poles") and t >= found_at:
        # On the lowest protective device above the premise (a fuse on most real paths).
        fuse = next(d for d in reversed(ds.path(premise_id)) if ds.device_type(d) in ("FUSE", "SECT", "REC", "BRK"))
        jobs.append(Job(fuse, "pole_replace", int(what_if["extra_poles"]), found_at, source="what_if", incident_id="what_if_poles"))
    # The real ARCOS extract has no crew counts, so fall back to the challenge assumption.
    crews = int(known.crew_count.iloc[0]) if len(known) and pd.notna(known.crew_count.iloc[0]) else default_crews(storm.max_wind_mph)
    crews = int(what_if.get("crews", crews))
    if base_lambda is None:
        base_lambda = prestorm_scenario(premise_id, {"max_wind_mph": storm.max_wind_mph}, ds).hidden_lambda
    return Scenario(crews=crews, access=float(what_if.get("access", 1.0)), now_h=t,
                    hidden_lambda=hidden_lambda(base_lambda, len(known), t), jobs=jobs)


def _p50_abs(est: dict) -> float:
    return est.get("now_h", 0.0) + est["hours_from_now"]["p50"]


def actual_restore_h(ds: Dataset, premise_id: str, recs: pd.DataFrame) -> float | None:
    on_path = recs.loc[recs.device_id.isin(ds.path(premise_id))]
    return None if on_path.empty else float(on_path.done_h.max())


def timeline(premise_id: str, event_id: str, customer: dict | None = None, what_if: dict | None = None,
             step_h: float = 1.0, n_sims: int = 600, ds: Dataset | None = None, calib: dict | None = None) -> dict:
    ds = ds or get_dataset()
    customer = customer or {"declared_needs": []}
    cid = ds.circuit_of(premise_id)
    recs = event_records(ds, event_id, cid)
    path = set(ds.path(premise_id))
    on_path = recs.loc[recs.device_id.isin(path)]
    if on_path.empty:
        return {"status": "not_affected", "event_id": event_id, "steps": [], "events": []}
    out_h = float(on_path.report_h.min())
    back_h = actual_restore_h(ds, premise_id, recs)
    storm = ds.storms.loc[ds.storms.event_id == event_id].iloc[0]
    base_lambda = prestorm_scenario(premise_id, {"max_wind_mph": storm.max_wind_mph}, ds).hidden_lambda

    steps, events = [], []
    prev_sc, prev_est = None, None
    t = math.floor(out_h)
    while t < back_h:
        sc = scenario_at(ds, premise_id, event_id, t, what_if, recs, base_lambda)
        est = estimate(premise_id, sc, n_sims=n_sims, seed=int(t * 10), ds=ds, calib=calib)
        if est["status"] == "ok":
            h = est["hours_from_now"]
            steps.append({"t": t, "p10": round(t + h["p10"], 1), "p50": round(t + h["p50"], 1), "p90": round(t + h["p90"], 1)})
        if t >= out_h:
            if not any(e["kind"] == "outage_confirmed" for e in events):
                events.append({"t": t, "kind": "outage_confirmed", "estimate": est, "reasons": est.get("drivers", [])[:1]})
            else:
                send, rule_why = should_send_update(prev_est, est, customer, outage_start_h=out_h)
                reasons = explain_change(prev_sc, sc, premise_id, ds)
                if send and not reasons and abs(_p50_abs(prev_est) - _p50_abs(est)) < 2:
                    send = False  # no concrete cause and a small move: don't spam
                if send:
                    events.append({"t": t, "kind": "estimate_updated", "estimate": est, "rule": rule_why,
                                   "reasons": reasons or ["damage assessment is further along"]})
                else:
                    t += step_h
                    continue  # compare the next hour against the last estimate we actually sent
            prev_sc, prev_est = sc, est
        t += step_h
    events.append({"t": round(back_h, 2), "kind": "restored", "estimate": None, "reasons": []})
    return {"status": "ok", "event_id": event_id, "storm": storm["name"], "outage_h": round(out_h, 2),
            "restored_h": round(back_h, 2), "steps": steps, "events": events,
            "what_if_note": "What-if changes affect estimates only; the restore moment comes from the real record." if what_if else None}
