"""Data for the two web pages: the customer site and the operations console.

Customer data always goes through fact cards (the privacy filter); the console is
company-facing, so it can show circuits, devices and repair IDs.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from . import ops, rules, service, store
from .data import DEVICE_WORDS, Dataset, get_dataset
from .factcard import recent_audit
from .restoration import damage_label

STAFF_EVENTS = {"danger_escalation": "Safety report", "human_requested": "Callback requested", "escalation": "Medical check-in"}


def clock(storm_h: float | None = None, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    w = service.WORLD
    now = w.now(ds)
    ev = ds.current_storm["replay_of_event"]
    last = (ds.damage.loc[ds.damage.event_id == ev, "repair_complete_time"].max()
            - ds.storms.set_index("event_id").start_time[ev]) / timedelta(hours=1)
    return {"phase": w.phase, "t_h": w.t_h if w.phase == "outage" else -1, "label": now.strftime("%a %-I%p").replace("AM", "am").replace("PM", "pm"),
            "date": now.strftime("%b %-d"), "storm": ds.current_storm["name"], "max_wind_mph": ds.current_storm["max_wind_mph"],
            "eta_hours": ds.current_storm["eta_hours"], "max_h": int(last) + 6}


def label_at(storm_h: float) -> str:
    t = service.STORM_START + timedelta(hours=storm_h)
    return service.fmt_clock(t, t - timedelta(days=30))


# ---------- customer site ----------

def account_label(c: dict) -> str:
    return "•••• " + str(int(hashlib.sha256(c["premise_id"].encode()).hexdigest()[:8], 16) % 10000).zfill(4)


def accounts(ds: Dataset | None = None) -> list[dict]:
    ds = ds or get_dataset()
    return [{"name": c["name"], "premise_id": c["premise_id"], "account": account_label(c),
             "declared_needs": [n["need"] for n in c.get("declared_needs", [])]} for c in ds.customers]


def customer_status(c: dict, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    w = service.WORLD
    now = w.now(ds)
    start, end = service.outage_start_h(c, ds, w), service.restored_h(c, ds, w)
    if w.phase == "pre_storm":
        state = "storm_coming"
    elif service.is_out(c, w):
        state = "out"
    elif start is not None and w.t_h >= end:
        state = "restored"
    else:
        state = "on"
    kind = {"storm_coming": "pre_storm", "out": "status"}.get(state, "no_outage")
    card = service.make_card(kind, c, w, ds=ds)
    plan = service.make_card("plan", c, w, ds=ds) if state in ("storm_coming", "out") else {}
    alerts = service.make_card("alerts", c, w, ds=ds).get("alerts", {})
    cid = ds.circuit_of(c["premise_id"])
    return {
        "account": {"name": c["name"], "account": account_label(c)},
        "now": now.strftime("%a %-I:%M %p").replace("AM", "am").replace("PM", "pm"), "state": state,
        "outage_since": service.fmt_clock(service.STORM_START + timedelta(hours=start), now) if state == "out" else None,
        "restored_at": service.fmt_clock(service.STORM_START + timedelta(hours=end), now) if state == "restored" else None,
        "storm": card.get("storm"), "risk": card.get("risk"), "fallback": card.get("fallback", False),
        "general_guidance": card.get("general_guidance"), "estimate": card.get("restoration"),
        "actions": card.get("actions", []), "honesty": card.get("honesty"),
        "notices": [{"text": n["text"], "level": n["level"], "scope": n["scope"]}
                    for n in ops.notices_for(cid, w.t_h if w.phase == "outage" else 0.0)],
        "power_budget": plan.get("power_budget", []), "declared_needs": card.get("declared_needs", []),
        "alerts": alerts, "version": ops.version(),
    }


# ---------- operations console ----------

def _known_customers(ds: Dataset) -> list[dict]:
    seen, out = set(), []
    for c in list(ds.customers) + [s.customer for s in service.SESSIONS.values()]:
        if c["premise_id"] not in seen:
            seen.add(c["premise_id"])
            out.append(c)
    return out


def escalations(ds: Dataset | None = None) -> list[dict]:
    """Customers who need a person: declared medical needs that may outlast the outage, plus safety reports and callback requests."""
    ds = ds or get_dataset()
    w = service.WORLD
    out = []
    for c in _known_customers(ds):
        if not c.get("declared_needs") or not service.is_out(c, w):
            continue
        est, _ = service.restoration_for(c, w, ds)
        start = service.outage_start_h(c, ds, w) or 0.0
        plan = rules.alert_plan(c, service.risk_for(c, ds), est, hours_already_out=w.t_h - start)
        for e in plan["escalations"]:
            out.append({"kind": "Medical check-in" if e["rule"] == "R4" else "Outreach", "who": c["name"],
                        "account": account_label(c), "circuit_id": ds.circuit_of(c["premise_id"]), "why": e["why"], "rule": e["rule"]})
    since = store.reset_at()
    for a in reversed(recent_audit(300)):
        if a["ts"] < since:
            break
        if a["event"] in ("danger_escalation", "human_requested"):
            p = a.get("payload") or {}
            via = "Website chat" if str(a["who"]).startswith("web-") else "Text message"
            out.append({"kind": STAFF_EVENTS[a["event"]], "who": via, "account": None, "circuit_id": None,
                        "why": p.get("reason") or (p.get("category") or "").replace("_", " ") or "see log", "at": a["ts"][11:16]})
        if len(out) > 40:
            break
    return out


def circuit_detail(circuit_id: str, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    w = service.WORLD
    t = w.t_h if w.phase == "outage" else 0.0
    row = next((r for r in ops.overview(t, ds)["circuits"] if r["circuit_id"] == circuit_id),
               {"circuit_id": circuit_id, "customers_out": 0, "open_repairs": 0, "done_repairs": 0, "crews": None,
                "customers": int((ds.premises.circuit_id == circuit_id).sum()), "confirmed_etr_h": None})
    repairs = []
    for r in ops.open_repairs(circuit_id, t, ds).itertuples() if w.phase == "outage" else []:
        dtype = ds.device_type(r.device_id)
        repairs.append({"incident_id": r.incident_id, "device_id": r.device_id, "where": DEVICE_WORDS.get(dtype, dtype),
                        "what": damage_label(r.damage_type, int(r.quantity)), "reported": label_at(r.report_h),
                        "source": "logged by staff" if str(r.incident_id).startswith("OPS") else "field report"})
    est = None
    out_devs = {x["device_id"] for x in repairs}
    rep = next((p for p, path in ds.paths.items() if ds.circuit_of(p) == circuit_id and out_devs & set(path)), None) if out_devs else None
    if rep:
        e, _ = service.restoration_for({"premise_id": rep, "phone": "ops", "declared_needs": []}, w, ds)
        if e.get("status") == "ok":
            h = e["hours_from_now"]
            est = {"window": f"{label_at(t + h['p10'])} – {label_at(t + h['p90'])}", "likely": label_at(t + h["p50"]),
                   "drivers": e["drivers"][:4]}
    return {**row, "now_h": t, "open": repairs, "estimate": est,
            "confirmed_etr": label_at(row["confirmed_etr_h"]) if row.get("confirmed_etr_h") is not None else None,
            "devices": ops.device_options(circuit_id, ds)[:40], "damage_types": [
                {"id": d, "label": damage_label(d, 1).removeprefix("a ").removeprefix("an ")} for d in ops.DAMAGE_TYPES],
            "notices": ops.notices_for(circuit_id, t),
            "etr_options": [{"h": t + k, "label": label_at(t + k)} for k in range(1, 121) if (t + k) % 1 == 0 and k % (1 if k <= 12 else 2) == 0]}


def action_log(ds: Dataset | None = None) -> list[dict]:
    words = {"crews": "Set crews", "damage_found": "Logged damage", "repair_done": "Marked repaired",
             "etr_confirmed": "Confirmed restoration time", "notice": "Posted notice"}
    out = []
    for a in reversed(ops.actions(include_undone=True)):
        p = a["payload"]
        detail = {"crews": lambda: f"{p['crews']} crews", "damage_found": lambda: f"{damage_label(p['damage_type'], p['quantity'])} at {p['device_id']}",
                  "repair_done": lambda: p["incident_id"], "etr_confirmed": lambda: f"power back by {label_at(p['at_h'])}",
                  "notice": lambda: f"“{p['text']}”"}[a["kind"]]()
        out.append({"id": a["id"], "what": words[a["kind"]], "detail": detail, "circuit_id": a["circuit_id"] or "All customers",
                    "at": label_at(a["storm_h"]), "undone": a["undone"]})
    return out
