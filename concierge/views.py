"""Data shaped for the web view: risk map, power family tree, replay texts.

Nothing here exposes device IDs: the tree uses plain labels ("Recloser"),
and the map uses area cells, not coordinates.
"""
from __future__ import annotations

import copy
from functools import lru_cache

from . import backtest, replay, rules, service
from .data import Dataset, get_dataset
from .restoration import damage_label
from .risk import predict

TREE_LABELS = {"SUB": "Substation", "BRK": "Main line", "REC": "Recloser", "SECT": "Sectionalizer", "STEP": "Step transformer",
               "FUSE": "Fuse (your street)", "XFMR": "Transformer"}


def _replay_event(ds: Dataset) -> str:
    return ds.current_storm["replay_of_event"]


@lru_cache(maxsize=1)
def risk_map(ds: Dataset | None = None) -> list[dict]:
    """One cell per area (fuse zone): its risk band for the coming storm."""
    ds = ds or get_dataset()
    reps = {}
    for p in ds.premises.itertuples():
        reps.setdefault(p.hex_id, p.premise_id)
    out = []
    subs = dict(zip(ds.circuits.circuit_id, ds.circuits.substation_id))
    for hex_id, pid in sorted(reps.items()):
        r = predict(pid, ds=ds)
        circuit_n = int(hex_id.split("-")[1])
        rf = hex_id.split("-")[2]
        cid = ds.circuit_of(pid)
        out.append({"cell": hex_id, "circuit_n": circuit_n, "circuit": cid, "substation": subs.get(cid),
                    "zone": (int(rf[0]) - 1) * 2 + (int(rf[1]) - 1),
                    "premise_id": pid, "band": r.get("band"), "probability": r.get("probability"),
                    "status": r["status"]})
    return out


def _intervals(ds: Dataset, devices: list[str], circuit_id: str) -> list[list[float]]:
    recs = replay.event_records(ds, _replay_event(ds), circuit_id)
    on = recs.loc[recs.device_id.isin(devices)]
    return [[round(r.report_h, 2), round(r.done_h, 2)] for r in on.itertuples()]


@lru_cache(maxsize=1)
def risk_map_with_outages(ds: Dataset | None = None) -> list[dict]:
    """Map cells plus, for the replayed storm, when each area's power was out
    ([reported, repaired] windows of damage on its path). The browser works out
    which cells are dark at any hour, so scrubbing needs no extra requests."""
    ds = ds or get_dataset()
    out = []
    for cell in risk_map(ds):
        pid = cell["premise_id"]
        out.append({**cell, "outage_windows": _intervals(ds, ds.path(pid)[:-1], ds.circuit_of(pid))})
    return out


def family_tree(premise_id: str, ds: Dataset | None = None) -> list[dict]:
    """Substation -> main line -> recloser -> fuse -> transformer -> home.
    Each node lists the replayed storm's damage with report/repair hours."""
    ds = ds or get_dataset()
    path = ds.path(premise_id)
    n_storms = len(ds.storms)
    recs = replay.event_records(ds, _replay_event(ds), ds.circuit_of(premise_id))
    nodes = []
    for dev in path:
        dtype = ds.device_type(dev)
        past = ds.damage.loc[ds.damage.device_id == dev, "event_id"].nunique()
        here = recs.loc[recs.device_id == dev]
        nodes.append({
            "label": TREE_LABELS.get(dtype, dtype), "type": dtype,
            "history": f"Damaged in {past} of {n_storms} past storms" if dtype != "SUB" else "Feeds every circuit in your area",
            "damage": [{"text": damage_label(r.damage_type, int(r.quantity)), "report_h": round(r.report_h, 2),
                        "done_h": round(r.done_h, 2)} for r in here.itertuples()],
        })
    nodes.append({"label": "Your home", "type": "HOME", "history": None, "damage": []})
    return nodes


def replay_with_texts(premise_id: str, customer: dict, what_if: dict | None = None, ds: Dataset | None = None) -> dict:
    """Replay timeline + the exact texts the customer would have received."""
    ds = ds or get_dataset()
    ev = _replay_event(ds)
    tl = replay.timeline(premise_id, ev, customer=customer, what_if=what_if, calib=backtest.live_calibration(ds), ds=ds)
    cust = copy.deepcopy(customer)
    risk = predict(premise_id, ds=ds)
    escalations, done_rules = [], set()
    for e in tl["events"]:
        world = service.World("outage", e["t"], dict(what_if or {}))
        if e.get("estimate") and e["estimate"].get("status") == "ok":
            # Same escalation rules the SMS side uses (R4 check-in call, R5 human outreach).
            plan = rules.alert_plan(cust, risk, e["estimate"], hours_already_out=e["t"] - tl["outage_h"])
            for x in plan["escalations"]:
                key = (x["rule"], x.get("need"))
                if key in done_rules:
                    continue
                done_rules.add(key)
                label = {"check_in_call": "Check-in call placed", "human_outreach": "Outreach team notified",
                         "human_review": "Message sent for human review"}.get(x["action"], x["action"])
                escalations.append({"t": e["t"], "kind": "escalation", "rule": x["rule"], "action": x["action"],
                                    "text": f"{label}: {x['why']}.", "clock": service.fmt_clock(world.now(ds), service.STORM_START),
                                    "segments": 0, "reasons": []})
        extra = {}
        if e["kind"] == "estimate_updated":
            extra["reasons_changed"] = e["reasons"]
        if e["kind"] == "restored":
            extra["restored_at"] = service.fmt_clock(service.STORM_START + _hours(e["t"]), world.now(ds))
            world.t_h = e["t"] + 0.01
        msg = service.message(e["kind"], cust, world=world, est=e["estimate"], extra=extra or None)
        e["text"] = msg["text"]
        e["segments"] = msg["segments"]
        e["clock"] = service.fmt_clock(world.now(ds), service.STORM_START)
        e["card"] = msg["card"]
        e.pop("estimate", None)
    # Escalations go right after the text that triggered them.
    tl["events"] = sorted(tl["events"] + escalations, key=lambda x: (x["t"], x["kind"] == "escalation"))
    tl["storm_start"] = service.STORM_START.isoformat()
    return tl


def _hours(h: float):
    from datetime import timedelta
    return timedelta(hours=h)
