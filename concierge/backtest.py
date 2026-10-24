"""Backtest / honesty score, with size-aware calibration.

For every past storm and every feeder path that lost power, we pretend it's
1 hour after the outage was first reported, estimate restoration using only
what was known then, and check whether the real restoration time (from ARCOS)
fell inside our P10-P90 range.

Testing: leave one storm out. For each storm, we learn the calibration from all
the OTHER storms, then score the storm we left out. Every storm gets scored by a
model that never saw it, and the scorecard shows each storm separately, so one
huge storm can't hide behind (or drag down) a single average.

Calibration by storm size: big storms behave differently (crews stretched thin,
more damage found late). We split by how much damage had been reported across
the whole system at the time of the estimate, which we'd know live. If we
haven't seen enough storms of that size, we fall back to all storms and the
estimate is marked low confidence (thin history).

Run:  python -m concierge.backtest
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import ROOT
from .data import Dataset, get_dataset
from .replay import actual_restore_h, event_records, scenario_at, system_reports
from .restoration import MIN_BUCKET_CASES, apply_calibration, estimate, pick_calibration, prestorm_scenario, storm_size

CACHE_DIR = ROOT / ".cache"
CACHE_VERSION = "2"  # bump when the restoration engine changes in a way that changes estimates


# ---------- cases (cached to disk: ~100 s on the real data) ----------

def _fingerprint(ds: Dataset, n_sims: int) -> str:
    src = getattr(ds, "source_dir", None)
    h = hashlib.sha1(f"{CACHE_VERSION}|{n_sims}|{len(ds.damage)}|{len(ds.paths)}|{len(ds.storms)}".encode())
    h.update(pd.util.hash_pandas_object(ds.damage[["event_id", "device_id", "damage_type"]], index=False).values.tobytes())
    if src:
        h.update(str(src).encode())
    return h.hexdigest()[:16]


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
            base = prestorm_scenario(premise, {"max_wind_mph": wind}, ds).hidden_lambda
            sc = scenario_at(ds, premise, ev, t, None, recs, base)
            est = estimate(premise, sc, n_sims=n_sims, seed=len(rows), ds=ds)
            if est["status"] != "ok":
                continue
            h = est["hours_from_now"]
            rows.append({"event_id": ev, "circuit_id": cid, "now_h": t, **h, "actual": round(actual - t, 2),
                         "system_reports": system_reports(ds, ev, t)})
    return pd.DataFrame(rows)


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


# ---------- calibration ----------

def fit_calibration(df: pd.DataFrame) -> dict:
    # Ratios of clock times (hours since storm start): reality vs our raw P50.
    r = np.log((df.now_h + df.actual) / (df.now_h + df.p50).clip(lower=0.25))
    return {"p10": float(np.quantile(r, 0.10)), "p50": float(np.quantile(r, 0.50)), "p90": float(np.quantile(r, 0.90))}


def fit_by_size(df: pd.DataFrame) -> dict:
    """One set of offsets per storm size, plus a pooled set for when a size has too little history."""
    size = df.system_reports.map(storm_size)
    model = {"pooled": fit_calibration(df) if len(df) else None, "support": {}}
    for s in ("small", "large"):
        part = df[size == s]
        model["support"][s] = int(len(part))
        model[s] = fit_calibration(part) if len(part) >= MIN_BUCKET_CASES else None
    return model


def _score(df: pd.DataFrame, model: dict | None) -> pd.DataFrame:
    df = df.copy()
    df["thin_history"] = False
    if model and model.get("pooled"):
        adj, thin = [], []
        for r in df.itertuples():
            offsets, info = pick_calibration(model, int(r.system_reports))
            adj.append(apply_calibration({"p10": r.p10, "p50": r.p50, "p90": r.p90}, offsets, r.now_h))
            thin.append(info["thin_history"])
        df[["p10", "p50", "p90"]] = pd.DataFrame(adj, index=df.index)[["p10", "p50", "p90"]]
        df["thin_history"] = thin
    df["inside"] = (df.p10 <= df.actual) & (df.actual <= df.p90)
    df["abs_err_p50"] = (df.p50 - df.actual).abs()
    return df


def _summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"cases": 0, "coverage": None, "too_optimistic": None, "too_pessimistic": None,
                "median_abs_error_h": None, "median_range_width_h": None}
    return {
        "cases": int(len(df)),
        "coverage": round(float(df.inside.mean()), 2),
        "too_optimistic": round(float((df.actual > df.p90).mean()), 2),  # real outage ran longer than P90
        "too_pessimistic": round(float((df.actual < df.p10).mean()), 2),
        "median_abs_error_h": round(float(df.abs_err_p50.median()), 1),
        "median_range_width_h": round(float((df.p90 - df.p10).median()), 1),
    }


@lru_cache(maxsize=1)
def leave_one_storm_out(ds: Dataset | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Every case scored twice: raw, and calibrated by a model that never saw its storm."""
    ds = ds or get_dataset()
    df = cases(ds=ds)
    raw_parts, cal_parts = [], []
    for ev in df.event_id.unique():
        held, train = df[df.event_id == ev], df[df.event_id != ev]
        raw_parts.append(_score(held, None))
        cal_parts.append(_score(held, fit_by_size(train) if len(train) else None))
    return pd.concat(raw_parts), pd.concat(cal_parts)


@lru_cache(maxsize=1)
def calibration(ds: Dataset | None = None) -> dict:
    """What the live app uses: calibration learned from ALL past storms, by size."""
    ds = ds or get_dataset()
    return fit_by_size(cases(ds=ds))


@lru_cache(maxsize=1)
def run(ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    raw, cal = leave_one_storm_out(ds)
    names = ds.storms.set_index("event_id")
    per_storm = []
    for ev, part in cal.groupby("event_id", sort=False):
        rpart = raw[raw.event_id == ev]
        s = names.loc[ev] if ev in names.index else None
        per_storm.append({
            "event_id": ev,
            "storm": (s["name"] if s is not None else ev),
            "date": (str(pd.Timestamp(s["start_time"]).date()) if s is not None else None),
            "damage_reports": int((ds.damage.event_id == ev).sum()),
            "size": storm_size(int((ds.damage.event_id == ev).sum())),  # label by total damage, for display
            "raw": _summary(rpart),
            "calibrated": _summary(part),
            "thin_history": bool(part.thin_history.mean() > 0.5),
        })
    per_storm.sort(key=lambda r: -r["damage_reports"])
    per_circuit = cal.groupby("circuit_id").agg(cases=("inside", "size"), inside=("inside", "sum")).reset_index()
    model = calibration(ds)
    cal_summary = _summary(cal)
    return {
        "method": "leave-one-storm-out",
        "raw_on_test": _summary(raw),
        "calibrated_on_test": cal_summary,
        "coverage": cal_summary["coverage"],
        "per_storm": per_storm,
        "per_circuit": [{"circuit_id": r["circuit_id"], "inside": int(r["inside"]), "cases": int(r["cases"])}
                        for r in per_circuit.to_dict("records")],
        "target_coverage": 0.8,
        "calibration_support": model["support"],
        "calibration": {k: ({kk: round(vv, 3) for kk, vv in v.items()} if isinstance(v, dict) and k != "support" else v)
                        for k, v in model.items()},
    }


def test_cases(calibrated: bool = True, ds: Dataset | None = None) -> list[dict]:
    """Every outage, scored by a model that never saw its storm (for the scorecard's range strip)."""
    raw, cal = leave_one_storm_out(ds or get_dataset())
    df = cal if calibrated else raw
    cols = ["event_id", "p10", "p50", "p90", "actual", "inside"]
    return [{**r, "inside": bool(r["inside"])} for r in df[cols].to_dict("records")]


def honesty_line(circuit_id: str, ds: Dataset | None = None) -> str:
    bt = run(ds)
    row = next((r for r in bt["per_circuit"] if r["circuit_id"] == circuit_id), None)
    if row and row["cases"] >= 3:
        return f"On your circuit, our ranges were right {row['inside']} of {row['cases']} times in past storms"
    return f"In past storms, our ranges were right {round(bt['coverage'] * 100)}% of the time"


if __name__ == "__main__":
    r = run()
    print(json.dumps({k: v for k, v in r.items() if k not in ("per_circuit",)}, indent=2, default=str))
