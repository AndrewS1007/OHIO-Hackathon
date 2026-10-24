"""Backtest / honesty score, with calibration.

For every past storm and every feeder path that lost power, we pretend it's
1 hour after the outage was first reported, estimate restoration using only
what was known then, and check whether the real restoration time (from ARCOS)
fell inside our P10-P90 range.

Calibration: the raw Monte Carlo range can be too narrow or too optimistic.
We learn how reality compared to our raw P50 on OLDER storms (calibration set)
and score the corrected ranges on NEWER storms we didn't learn from (test set).
The honesty score we show customers is the test-set number.

Cases are cached to disk in .cache/ (~2 min to compute on the real data). The cache key
includes the engine's source code, so changing the model recomputes it.

Run:  python -m concierge.backtest
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT
from .data import Dataset, get_dataset
from .replay import actual_restore_h, event_records, scenario_at
from .restoration import apply_calibration, estimate, prestorm_scenario

CALIB_SHARE = 0.6  # oldest 60% of storms for calibration, newest 40% for testing
CACHE_DIR = ROOT / ".cache"
ENGINE_FILES = ("backtest.py", "delays.py", "replay.py", "restoration.py", "risk.py")


def _fingerprint(ds: Dataset, n_sims: int) -> str:
    h = hashlib.sha1(f"{n_sims}|{len(ds.damage)}|{len(ds.paths)}|{len(ds.storms)}|{ds.labor_hours}|{ds.indirect}".encode())
    for f in ENGINE_FILES:
        h.update((Path(__file__).parent / f).read_bytes())
    return h.hexdigest()[:12]


@lru_cache(maxsize=4)
def cases(n_sims: int = 300, ds: Dataset | None = None) -> pd.DataFrame:
    ds = ds or get_dataset()
    path = CACHE_DIR / f"backtest_cases_{_fingerprint(ds, n_sims)}.csv"
    if path.exists():
        return pd.read_csv(path, dtype={"event_id": str, "circuit_id": str})
    df = _compute_cases(n_sims, ds)
    CACHE_DIR.mkdir(exist_ok=True)
    df.to_csv(path, index=False)
    return df


def _compute_cases(n_sims: int, ds: Dataset) -> pd.DataFrame:
    reps = {}
    for p, path in ds.paths.items():
        reps.setdefault(tuple(path[:-1]), p)  # one premise per fuse path
    rows = []
    for ev in ds.storms.sort_values("start_time").event_id:
        wind = float(ds.storms.loc[ds.storms.event_id == ev, "max_wind_mph"].iloc[0])
        by_circuit, seen = {}, set()
        for premise in reps.values():
            cid = ds.circuit_of(premise)
            if cid not in by_circuit:
                by_circuit[cid] = event_records(ds, ev, cid)
            recs = by_circuit[cid]
            if recs.empty:
                continue
            on_path = recs.loc[recs.device_id.isin(ds.path(premise))]
            # Paths that share the same damage get the same answer: score each once.
            key = frozenset(on_path.incident_id)
            if on_path.empty or key in seen:
                continue
            seen.add(key)
            t = float(on_path.report_h.min()) + 1.0
            actual = actual_restore_h(ds, premise, recs)
            if actual <= t:
                continue
            base = prestorm_scenario(premise, {"max_wind_mph": wind}, ds, exclude=(ev,)).hidden_lambda
            sc = scenario_at(ds, premise, ev, t, None, recs, base)
            est = estimate(premise, sc, n_sims=n_sims, seed=len(rows), ds=ds)
            if est["status"] != "ok":
                continue
            h = est["hours_from_now"]
            rows.append({"event_id": ev, "circuit_id": cid, "now_h": t, **h, "actual": round(actual - t, 2)})
    return pd.DataFrame(rows)


def _split(ds: Dataset) -> tuple[list[str], list[str]]:
    evs = ds.storms.sort_values("start_time").event_id.tolist()
    k = int(round(len(evs) * CALIB_SHARE))
    return evs[:k], evs[k:]


def fit_calibration(df: pd.DataFrame) -> dict:
    # Ratios of clock times (hours since storm start): reality vs our raw P50.
    r = np.log((df.now_h + df.actual) / (df.now_h + df.p50).clip(lower=0.25))
    return {"p10": float(np.quantile(r, 0.10)), "p50": float(np.quantile(r, 0.50)), "p90": float(np.quantile(r, 0.90))}


def _score(df: pd.DataFrame, calib: dict | None) -> pd.DataFrame:
    df = df.copy()
    if calib:
        adj = df.apply(lambda r: apply_calibration({"p10": r.p10, "p50": r.p50, "p90": r.p90}, calib, r.now_h), axis=1)
        df[["p10", "p50", "p90"]] = pd.DataFrame(list(adj), index=df.index)[["p10", "p50", "p90"]]
    df["inside"] = (df.p10 <= df.actual) & (df.actual <= df.p90)
    df["abs_err_p50"] = (df.p50 - df.actual).abs()
    return df


def _summary(df: pd.DataFrame) -> dict:
    return {
        "cases": int(len(df)),
        "coverage": round(float(df.inside.mean()), 2),
        "too_optimistic": round(float((df.actual > df.p90).mean()), 2),  # real outage ran longer than P90
        "too_pessimistic": round(float((df.actual < df.p10).mean()), 2),
        "median_abs_error_h": round(float(df.abs_err_p50.median()), 1),
        "median_range_width_h": round(float((df.p90 - df.p10).median()), 1),
    }


MAX_WIDTH_GROWTH = 2.0  # a range more than twice as wide isn't one people can plan around


@lru_cache(maxsize=1)
def calibration(ds: Dataset | None = None) -> dict:
    """Calibration learned from the older storms only, or {} (use raw ranges) if it would
    more than double the range width on those storms."""
    ds = ds or get_dataset()
    df = cases(ds=ds)
    calib_evs, _ = _split(ds)
    train = df[df.event_id.isin(calib_evs)]
    cal = fit_calibration(train)
    raw_w = _summary(_score(train, None))["median_range_width_h"]
    cal_w = _summary(_score(train, cal))["median_range_width_h"]
    return cal if cal_w <= MAX_WIDTH_GROWTH * max(raw_w, 1.0) else {}


def live_calibration(ds: Dataset | None = None) -> dict | None:
    """What the live app applies: the calibration, or None for raw ranges."""
    return calibration(ds or get_dataset()) or None


@lru_cache(maxsize=1)
def run(ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    df = cases(ds=ds)
    calib_evs, test_evs = _split(ds)
    cal = calibration(ds)
    test = df[df.event_id.isin(test_evs)]
    scored = _score(test, cal)
    per_circuit = scored.groupby("circuit_id").agg(cases=("inside", "size"), inside=("inside", "sum")).reset_index()
    names = ds.storms.set_index("event_id")
    per_storm = []
    for ev, part in test.groupby("event_id", sort=False):  # newest storms: the ones calibration never saw
        per_storm.append({
            "event_id": ev, "storm": names.loc[ev, "name"], "date": str(pd.Timestamp(names.loc[ev, "start_time"]).date()),
            "damage_reports": int((ds.damage.event_id == ev).sum()),
            "raw": _summary(_score(part, None)), "calibrated": _summary(_score(part, cal)), "thin_history": False,
        })
    per_storm.sort(key=lambda r: -r["damage_reports"])
    return {
        "method": "restoration delays learned leaving each storm out; calibration on older storms, scored on newer",
        "uses_calibration": bool(cal),
        "per_storm": per_storm,
        "calibration_storms": calib_evs,
        "test_storms": test_evs,
        "raw_on_test": _summary(_score(test, None)),
        "calibrated_on_test": _summary(scored),
        "coverage": _summary(scored)["coverage"],
        "per_circuit": [{**r, "inside": int(r["inside"]), "cases": int(r["cases"])} for r in per_circuit.to_dict("records")],
        "target_coverage": 0.8,
        "calibration": {k: round(v, 3) for k, v in cal.items()},
        "calibration_used": bool(cal),
    }


def test_cases(calibrated: bool = True, ds: Dataset | None = None) -> list[dict]:
    """Held-out cases (raw or calibrated), for the scorecard's range strip."""
    ds = ds or get_dataset()
    df = cases(ds=ds)
    _, test_evs = _split(ds)
    scored = _score(df[df.event_id.isin(test_evs)], calibration(ds) if calibrated else None)
    cols = ["event_id", "p10", "p50", "p90", "actual", "inside"]
    return [{**r, "inside": bool(r["inside"])} for r in scored[cols].to_dict("records")]


def honesty_line(circuit_id: str, ds: Dataset | None = None) -> str:
    bt = run(ds)
    row = next((r for r in bt["per_circuit"] if r["circuit_id"] == circuit_id), None)
    if row and row["cases"] >= 3:
        return f"On your circuit, our ranges were right {row['inside']} of {row['cases']} times in recent storms"
    return f"In recent storms, our ranges were right {round(bt['coverage'] * 100)}% of the time"


if __name__ == "__main__":
    import json
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k != "per_circuit"}, indent=2))
