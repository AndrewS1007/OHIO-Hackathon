"""Operations: what utility staff change during a storm, and how it reaches customers.

Staff actions are stored (SQLite, see store.py) and applied on top of the storm's
damage records before any estimate is made, so a fix in the operations console
shows up on the customer site, in the chatbot's answers and in SMS alerts.

  crews          set the crews working a circuit (stretches or shrinks the learned waits)
  damage_found   log damage crews found that isn't in the records yet (it stays open until repaired)
  repair_done    mark an open repair finished now
  etr_confirmed  confirm a restoration time for a circuit (shown to customers as confirmed, not estimated)
  notice         post a notice to every customer, or to one circuit

Every action can be undone. Times are storm hours (hours since the storm hit).
"""
from __future__ import annotations

import json
import math
from functools import lru_cache

import numpy as np
import pandas as pd

from . import store
from .data import DEVICE_WORDS, Dataset, get_dataset
from .replay import event_records

KINDS = ("crews", "damage_found", "repair_done", "etr_confirmed", "notice")
DAMAGE_TYPES = ("pole_replace", "conductor_repair", "transformer_replace", "fuse_replace", "recloser_replace",
                "switch_repair", "secondary_repair")

_version = 0  # bumped on every change, so caches keyed on it stay correct


def version() -> int:
    return _version


def _bump():
    global _version
    _version += 1
    _actions.cache_clear()


@lru_cache(maxsize=1)
def _actions() -> tuple[dict, ...]:
    return tuple(store.ops_actions())


def actions(include_undone: bool = False) -> list[dict]:
    return [a for a in _actions() if include_undone or not a["undone"]]


class OpsError(ValueError):
    pass


def add(kind: str, payload: dict, storm_h: float, circuit_id: str | None = None, author: str = "ops") -> dict:
    """Validate and record one staff action. Returns the stored action."""
    ds = get_dataset()
    if kind not in KINDS:
        raise OpsError(f"unknown action {kind}")
    if kind != "notice" or circuit_id:
        if circuit_id not in set(ds.circuits.circuit_id):
            raise OpsError("unknown circuit")
    p = dict(payload)
    if kind == "crews":
        p["crews"] = int(p.get("crews", 0))
        if not 1 <= p["crews"] <= 20:
            raise OpsError("crews must be 1-20")
    elif kind == "damage_found":
        dev = p.get("device_id")
        if dev not in ds.graph or ds.graph.nodes[dev].get("circuit_id") != circuit_id:
            raise OpsError("pick a device on this circuit")
        if p.get("damage_type") not in DAMAGE_TYPES:
            raise OpsError("unknown damage type")
        p["quantity"] = max(1, min(10, int(p.get("quantity", 1))))
    elif kind == "repair_done":
        inc = p.get("incident_id")
        open_ids = set(open_repairs(circuit_id, storm_h).incident_id)
        if inc not in open_ids:
            raise OpsError("that repair isn't open right now")
    elif kind == "etr_confirmed":
        p["at_h"] = float(p["at_h"])
        if p["at_h"] < storm_h:
            raise OpsError("a confirmed time can't be in the past")
    elif kind == "notice":
        p["text"] = str(p.get("text", "")).strip()[:280]
        if not p["text"]:
            raise OpsError("a notice needs text")
        p["level"] = "warning" if p.get("level") == "warning" else "info"
    a = store.add_ops_action(kind, circuit_id, p, storm_h, author)
    _bump()
    return a


def undo(action_id: int) -> None:
    store.undo_ops_action(action_id)
    _bump()


def clear() -> None:
    store.clear_ops()
    _bump()


# ---------- applying actions to the engines ----------

def crews_for(circuit_id: str, storm_h: float) -> int | None:
    xs = [a for a in actions() if a["kind"] == "crews" and a["circuit_id"] == circuit_id and a["storm_h"] <= storm_h]
    return xs[-1]["payload"]["crews"] if xs else None


def confirmed_etr(circuit_id: str, storm_h: float) -> float | None:
    xs = [a for a in actions() if a["kind"] == "etr_confirmed" and a["circuit_id"] == circuit_id and a["storm_h"] <= storm_h]
    return xs[-1]["payload"]["at_h"] if xs else None


def notices_for(circuit_id: str | None, storm_h: float) -> list[dict]:
    return [{"id": a["id"], "text": a["payload"]["text"], "level": a["payload"]["level"],
             "scope": "your area" if a["circuit_id"] else "everyone", "storm_h": a["storm_h"]}
            for a in actions() if a["kind"] == "notice" and a["storm_h"] <= storm_h
            and (a["circuit_id"] is None or a["circuit_id"] == circuit_id)]


def apply_records(recs: pd.DataFrame, storm_h: float | None = None) -> pd.DataFrame:
    """The storm's damage records with staff actions applied (logged damage added, repairs closed).

    Actions take effect from the storm hour they were entered, so replaying earlier hours
    shows the records as they were then."""
    acts = [a for a in actions() if a["kind"] in ("damage_found", "repair_done")
            and (storm_h is None or a["storm_h"] <= storm_h)]
    if not acts:
        return recs
    cids = set(recs.circuit_id) if len(recs) else set()
    recs = recs.copy()
    closed = {a["payload"]["incident_id"]: a["storm_h"] for a in acts if a["kind"] == "repair_done"}
    hit = recs.incident_id.isin(closed) if closed and len(recs) else None
    if hit is not None and hit.any():
        recs.loc[hit, "done_h"] = np.minimum(recs.loc[hit, "done_h"].astype(float),
                                             recs.loc[hit, "incident_id"].map(closed).astype(float))
    rows = []
    for a in acts:
        if a["kind"] != "damage_found" or (cids and a["circuit_id"] not in cids):
            continue
        inc = f"OPS{a['id']:04d}"
        p = a["payload"]
        rows.append({"incident_id": inc, "circuit_id": a["circuit_id"], "device_id": p["device_id"],
                     "asset_type": "logged by crews", "damage_type": p["damage_type"], "quantity": p["quantity"],
                     "crew_count": np.nan, "extra_hours": 0.0, "report_h": a["storm_h"],
                     "done_h": closed.get(inc, math.inf)})
    if rows:
        had_extra = "extra_hours" in recs.columns
        recs = pd.concat([recs, pd.DataFrame(rows)], ignore_index=True)
        if not had_extra:  # the mock records have no access adders: treat them as zero, not missing
            recs["extra_hours"] = recs["extra_hours"].fillna(0.0)
    return recs


def records(ds: Dataset, circuit_id: str, storm_h: float | None = None) -> pd.DataFrame:
    return apply_records(event_records(ds, ds.current_storm["replay_of_event"], circuit_id), storm_h)


# ---------- what the console shows ----------

def open_repairs(circuit_id: str, storm_h: float, ds: Dataset | None = None) -> pd.DataFrame:
    ds = ds or get_dataset()
    r = records(ds, circuit_id, storm_h)
    return r.loc[(r.report_h <= storm_h) & (r.done_h > storm_h)]


@lru_cache(maxsize=4)
def _downstream(ds: Dataset) -> dict[str, np.ndarray]:
    """Device -> indexes of the premises it feeds."""
    out: dict[str, list[int]] = {}
    for i, path in enumerate(ds.paths.values()):
        for d in path:
            out.setdefault(d, []).append(i)
    return {d: np.array(v) for d, v in out.items()}


@lru_cache(maxsize=8)
def _event_records_all(ds: Dataset) -> pd.DataFrame:
    ev = ds.current_storm["replay_of_event"]
    return pd.concat([event_records(ds, ev, c) for c in sorted(set(ds.damage.loc[ds.damage.event_id == ev, "circuit_id"]))],
                     ignore_index=True)


@lru_cache(maxsize=32)
def _overview(ds: Dataset, storm_h: float, _ver: int) -> dict:
    recs = apply_records(_event_records_all(ds), storm_h)
    reported = recs.loc[recs.report_h <= storm_h]
    is_open = reported.done_h > storm_h
    down = _downstream(ds)
    open_devs = set(reported.loc[is_open, "device_id"])
    out_idx = np.unique(np.concatenate([down[d] for d in open_devs if d in down])) if open_devs else np.array([], int)
    premises = list(ds.paths)
    out_circuit = pd.Series([ds.circuit_of(premises[i]) for i in out_idx], dtype=object).value_counts()
    customers = ds.premises.circuit_id.value_counts()
    rows = []
    for cid, g in reported.groupby("circuit_id"):
        o = g.done_h > storm_h
        rec_crews = g.crew_count.dropna()
        rows.append({
            "circuit_id": cid, "substation_id": ds.circuits.set_index("circuit_id").substation_id.get(cid),
            "customers": int(customers.get(cid, 0)), "customers_out": int(out_circuit.get(cid, 0)),
            "open_repairs": int(o.sum()), "done_repairs": int((~o).sum()),
            "crews": crews_for(cid, storm_h) or (int(rec_crews.iloc[0]) if len(rec_crews) else None),
            "crews_set_by_ops": crews_for(cid, storm_h) is not None,
            "confirmed_etr_h": confirmed_etr(cid, storm_h),
        })
    rows.sort(key=lambda r: (-r["customers_out"], -r["open_repairs"], r["circuit_id"]))
    return {"customers_out": int(len(out_idx)), "customers_total": int(len(premises)),
            "open_repairs": int(is_open.sum()), "done_repairs": int((~is_open).sum()),
            "circuits_affected": int(sum(r["open_repairs"] > 0 for r in rows)), "circuits": rows}


def overview(storm_h: float, ds: Dataset | None = None) -> dict:
    return _overview(ds or get_dataset(), round(float(storm_h), 2), _version)


def device_options(circuit_id: str, ds: Dataset | None = None) -> list[dict]:
    """Protective devices on a circuit staff can log damage at, biggest first."""
    ds = ds or get_dataset()
    down = _downstream(ds)
    devs = ds.devices.loc[(ds.devices.circuit_id == circuit_id) & ds.devices.device_type.isin(["BRK", "REC", "SECT", "FUSE"])]
    out = [{"device_id": d, "type": t, "label": f"{DEVICE_WORDS.get(t, t).capitalize()} {d}", "customers": int(len(down.get(d, [])))}
           for d, t in zip(devs.device_id, devs.device_type)]
    return sorted(out, key=lambda x: -x["customers"])
