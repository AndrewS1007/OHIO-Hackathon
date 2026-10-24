"""Outage-risk engine: "Will I lose power?"

Model: logistic regression on (feeder path, past storm) pairs. The label is
"was any equipment upstream of this premise (main line, recloser, fuse)
damaged in that storm". Each prediction comes with a band, a confidence,
the top factors in plain words, and similar past storms.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from .data import Dataset, get_dataset

FEATURES = ["max_wind_mph", "ice_in", "overhead_pct", "length_mi", "hist_path_rate"]
BANDS = [(0.25, "Low"), (0.5, "Elevated"), (0.75, "High"), (1.01, "Severe")]
MIN_HISTORY = 3  # fewer past storms than this -> fallback


def band_for(p: float) -> str:
    return next(name for cut, name in BANDS if p < cut)


def _zone_path(ds: Dataset, premise_id: str) -> list[str]:
    """Upline devices that can take this premise out (main line, recloser, sectionalizer, fuse)."""
    return [d for d in ds.path(premise_id) if ds.device_type(d) in ("BRK", "REC", "SECT", "FUSE")]


@lru_cache(maxsize=4096)
def _history_events(ds: Dataset, circuit_id: str) -> list[str]:
    """Storms we have records for on this circuit (from its first appearance on)."""
    seen = ds.damage.loc[ds.damage.circuit_id == circuit_id, "event_id"]
    if seen.empty:
        return []
    first = ds.storms.loc[ds.storms.event_id.isin(seen), "start_time"].min()
    return ds.storms.loc[ds.storms.start_time >= first, "event_id"].tolist()


@lru_cache(maxsize=4)
def _events_by_device(ds: Dataset) -> dict[str, set[str]]:
    return ds.damage.groupby("device_id").event_id.agg(set).to_dict()


def _path_damaged(ds: Dataset, zone_path: list[str]) -> set[str]:
    by_dev = _events_by_device(ds)
    return set().union(*(by_dev.get(d, set()) for d in zone_path))


@dataclass
class RiskModel:
    model: LogisticRegression
    scaler: StandardScaler
    n_rows: int


@lru_cache(maxsize=1)
def train(ds: Dataset | None = None) -> RiskModel:
    ds = ds or get_dataset()
    circ = ds.circuits.set_index("circuit_id")
    storms = ds.storms.set_index("event_id")
    rows = []
    # One representative premise per fuse = one row per distinct feeder path.
    reps = {}
    for p, path in ds.paths.items():
        reps.setdefault(tuple(path[:-1]), p)
    for path_key, premise in reps.items():
        cid = ds.circuit_of(premise)
        hist = _history_events(ds, cid)
        damaged = _path_damaged(ds, _zone_path(ds, premise))
        for ev in hist:
            others = [e for e in hist if e != ev]
            rate = (sum(e in damaged for e in others) / len(others)) if others else 0.0
            rows.append({
                "max_wind_mph": storms.loc[ev, "max_wind_mph"], "ice_in": storms.loc[ev, "ice_in"],
                "overhead_pct": circ.loc[cid, "overhead_pct"], "length_mi": circ.loc[cid, "length_mi"],
                "hist_path_rate": rate, "y": int(ev in damaged),
            })
    df = pd.DataFrame(rows)
    scaler = StandardScaler().fit(df[FEATURES])
    model = LogisticRegression(C=1.0, max_iter=1000).fit(scaler.transform(df[FEATURES]), df.y)
    return RiskModel(model, scaler, len(df))


def _factor_text(name: str, x: dict, ctx: dict) -> str:
    if name == "max_wind_mph":
        return f"Forecast winds up to {int(x[name])} mph"
    if name == "ice_in":
        return f"About {x[name]:g} in of ice expected" if x[name] > 0 else "No ice in the forecast"
    if name == "overhead_pct":
        return f"About {round(x[name] * 100)}% of your line is overhead" if x[name] >= 0.6 else "Much of your line is underground"
    if name == "length_mi":
        return f"Your circuit is long ({x[name]:g} mi), so more line is exposed" if x[name] >= 9 else "Your circuit is fairly short"
    if name == "hist_path_rate":
        return f"Equipment upstream of you was damaged in {ctx['hits']} of {ctx['n_hist']} past storms"
    return name


def similar_storms(ds: Dataset, premise_id: str, wind: float, window: int = 12) -> list[dict]:
    cid = ds.circuit_of(premise_id)
    path = ds.path(premise_id)
    out = []
    for ev in _history_events(ds, cid):
        s = ds.storms.loc[ds.storms.event_id == ev].iloc[0]
        if abs(s.max_wind_mph - wind) > window:
            continue
        dmg = ds.damage.loc[(ds.damage.event_id == ev) & ds.damage.device_id.isin(path)]
        hrs = None
        if not dmg.empty:
            hrs = round((dmg.repair_complete_time.max() - s.start_time).total_seconds() / 3600, 1)
        out.append({"storm": s["name"], "year": int(s.start_time.year), "wind_mph": int(s.max_wind_mph),
                    "lost_power": hrs is not None, "hours_to_restore": hrs})
    return out


def predict(premise_id: str, storm: dict | None = None, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    storm = storm or ds.current_storm
    if ds.path(premise_id) is None:
        return {"status": "unknown_premise", "message": "We couldn't find that location."}
    cid = ds.circuit_of(premise_id)
    hist = _history_events(ds, cid)
    damaged = _path_damaged(ds, _zone_path(ds, premise_id))
    hits = sum(e in damaged for e in hist)
    if len(hist) < MIN_HISTORY:
        return {
            "status": "insufficient_data",
            "circuit_id": cid,
            "n_past_storms": len(hist),
            "message": "We don't have enough storm history for your area to make a reliable prediction.",
            "general_guidance": "With winds this strong, plan as if you could lose power for up to a day.",
        }
    rm = train(ds)
    c = ds.circuits.set_index("circuit_id").loc[cid]
    x = {"max_wind_mph": storm["max_wind_mph"], "ice_in": storm.get("ice_in", 0.0),
         "overhead_pct": c.overhead_pct, "length_mi": c.length_mi, "hist_path_rate": hits / len(hist)}
    xv = pd.DataFrame([x])[FEATURES]
    z = rm.scaler.transform(xv)[0]
    p = float(rm.model.predict_proba(z.reshape(1, -1))[0, 1])
    contrib = rm.model.coef_[0] * z
    order = np.argsort(-np.abs(contrib))
    ctx = {"hits": hits, "n_hist": len(hist)}
    # Only factors that move the answer noticeably; tiny weights just confuse people.
    factors = [{"factor": _factor_text(FEATURES[i], x, ctx), "direction": "raises" if contrib[i] > 0 else "lowers",
                "weight": round(float(contrib[i]), 2)} for i in order if abs(contrib[i]) >= 0.1][:3]
    # Confidence: more history and a probability away from band edges -> higher.
    edge_gap = min(abs(p - cut) for cut, _ in BANDS[:-1])
    if len(hist) >= 6 and edge_gap > 0.07:
        conf = "high"
    elif len(hist) >= 4:
        conf = "medium"
    else:
        conf = "low"
    return {
        "status": "ok",
        "circuit_id": cid,
        "probability": round(p, 2),
        "band": band_for(p),
        "confidence": conf,
        "top_factors": factors,
        "history": f"Equipment upstream of you was damaged in {hits} of {len(hist)} past storms",
        "n_past_storms": len(hist),
        "similar_storms": similar_storms(ds, premise_id, storm["max_wind_mph"]),
        "storm": {"name": storm.get("name"), "eta_hours": storm.get("eta_hours"),
                  "max_wind_mph": storm["max_wind_mph"], "ice_in": storm.get("ice_in", 0.0)},
    }
