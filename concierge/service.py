"""Orchestration: the demo "world" (storm clock + what-ifs), fact cards, and
proactive alerts. SMS, voice, and the web view all call into this module.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import backtest, ops, outbound, prep, rules, store
from .data import Dataset, get_dataset
from .factcard import audit, build
from .narrate import render
from .replay import event_records, scenario_at
from .restoration import estimate, explain_change, prestorm_scenario
from .risk import predict

STORM_START = datetime(2026, 10, 25, 14, 0)  # when the demo storm hits


@dataclass
class World:
    phase: str = "pre_storm"  # pre_storm | outage
    t_h: float = 0.0  # hours since the storm hit (outage phase)
    what_if: dict = field(default_factory=dict)

    def now(self, ds: Dataset) -> datetime:
        if self.phase == "pre_storm":
            return STORM_START - timedelta(hours=ds.current_storm["eta_hours"])
        return STORM_START + timedelta(hours=self.t_h)


@dataclass
class Session:
    customer: dict
    battery_mode: bool = False
    emoji: bool = False  # plain GSM-7 text fits 160 chars per SMS; emoji drops it to 70
    last_sent: dict | None = None  # last estimate we texted, for "here's why"
    last_scenario: object = None
    outage_announced: bool = False
    restored_announced: bool = False
    escalations_sent: set = field(default_factory=set)


WORLD = World()
SESSIONS: dict[str, Session] = {}


def who(phone: str) -> str:
    """Audit-log identity: a hash, never the raw phone number."""
    return hashlib.sha256(phone.encode()).hexdigest()[:10]


def fmt_clock(dt: datetime, now: datetime) -> str:
    dt = (dt + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    h = dt.hour % 12 or 12
    s = f"{h}{'am' if dt.hour < 12 else 'pm'}"
    return s if dt.date() == now.date() else f"{dt.strftime('%a')} {s}"


def _conf(h: dict, est: dict | None = None) -> str:
    if est and est.get("calibration", {}).get("thin_history"):
        return "low"  # we haven't seen enough storms this size to trust the range
    spread = (h["p90"] - h["p10"]) / max(h["p50"], 1)
    return "high" if spread < 1.0 else "medium" if spread < 2.0 else "low"


# ---------- engine calls ----------

def risk_for(customer: dict, ds: Dataset | None = None) -> dict:
    return predict(customer["premise_id"], ds=ds)


_EST_CACHE: dict = {}


def _records(customer: dict, world: World | None, ds: Dataset):
    """The replayed storm's records for this customer's circuit, with staff actions applied up to now."""
    w = world or WORLD
    return ops.records(ds, ds.circuit_of(customer["premise_id"]), w.t_h if w.phase == "outage" else None)


def restoration_for(customer: dict, world: World = WORLD, ds: Dataset | None = None):
    ds = ds or get_dataset()
    pid = customer["premise_id"]
    what_if = dict(world.what_if)
    staff_crews = ops.crews_for(ds.circuit_of(pid), world.t_h if world.phase == "outage" else 0.0)
    if staff_crews and "crews" not in what_if:
        what_if["crews"] = staff_crews  # crews the operations team assigned to this circuit
    key = (id(ds), pid, world.phase, round(world.t_h, 2), tuple(sorted(what_if.items())), ops.version())
    if key in _EST_CACHE:
        return _EST_CACHE[key]
    calib = backtest.live_calibration(ds)
    if world.phase == "pre_storm":
        sc = prestorm_scenario(pid, ds.current_storm, ds)
        if what_if.get("crews"):
            sc.crews = int(what_if["crews"])
        sc.access = float(what_if.get("access", 1.0))
    else:
        sc = scenario_at(ds, pid, ds.current_storm["replay_of_event"], world.t_h, what_if, recs=_records(customer, world, ds))
    out = estimate(pid, sc, n_sims=800, seed=7, ds=ds, calib=calib), sc
    if len(_EST_CACHE) > 512:
        _EST_CACHE.clear()
    _EST_CACHE[key] = out
    return out


def outage_start_h(customer: dict, ds: Dataset | None = None, world: World | None = None) -> float | None:
    ds = ds or get_dataset()
    recs = _records(customer, world, ds)
    on_path = recs.loc[recs.device_id.isin(ds.path(customer["premise_id"]))]
    return None if on_path.empty else float(on_path.report_h.min())


def restored_h(customer: dict, ds: Dataset | None = None, world: World | None = None) -> float | None:
    """When power came back (from the records, closed earlier by staff repairs; inf while staff-logged damage is open)."""
    ds = ds or get_dataset()
    recs = _records(customer, world, ds)
    on_path = recs.loc[recs.device_id.isin(ds.path(customer["premise_id"]))]
    return None if on_path.empty else float(on_path.done_h.max())


def is_out(customer: dict, world: World = WORLD) -> bool:
    if world.phase != "outage":
        return False
    start, end = outage_start_h(customer, world=world), restored_h(customer, world=world)
    return start is not None and start <= world.t_h < end


def ops_lines(customer: dict, world: World, ds: Dataset) -> tuple[list[str], str | None]:
    """What the operations team has confirmed for this customer: notices, and a confirmed restoration time."""
    cid = ds.circuit_of(customer["premise_id"])
    t = world.t_h if world.phase == "outage" else 0.0
    lines = [n["text"] for n in ops.notices_for(cid, t)]
    etr = ops.confirmed_etr(cid, t) if world.phase == "outage" else None
    etr_txt = fmt_clock(STORM_START + timedelta(hours=etr), world.now(ds)) if etr is not None else None
    if etr_txt and is_out(customer, world):
        lines.insert(0, f"Our crews confirm power back by {etr_txt}.")
    return lines, (etr_txt if is_out(customer, world) else None)


# ---------- fact cards ----------

def make_card(kind: str, customer: dict, world: World = WORLD, extra: dict | None = None,
              est: dict | None = None, ds: Dataset | None = None) -> dict:
    ds = ds or get_dataset()
    now = world.now(ds)
    rk = risk_for(customer, ds)
    if est is None and (world.phase == "pre_storm" or is_out(customer, world)):
        est, _ = restoration_for(customer, world, ds)
    needs = customer.get("declared_needs", [])
    raw = {"kind": kind, "is_model_estimate": True,
           "storm": {"name": ds.current_storm["name"], "eta_hours": ds.current_storm["eta_hours"],
                     "max_wind_mph": ds.current_storm["max_wind_mph"]},
           "declared_needs": [n["need"] for n in needs],
           "battery_mode": bool(extra and extra.get("battery_mode"))}
    if rk["status"] == "ok":
        sims = rk["similar_storms"]
        raw["risk"] = {"band": rk["band"], "confidence": rk["confidence"],
                       "top_factors": [f["factor"] for f in rk["top_factors"]], "history": rk["history"],
                       "similar_storms_summary": f"In {len(sims)} similar past storms, your area lost power in {sum(s['lost_power'] for s in sims)}"}
        raw["honesty"] = backtest.honesty_line(rk["circuit_id"], ds)
    else:
        raw["fallback"] = True
        raw["general_guidance"] = rk.get("general_guidance", "Plan for up to a day without power.")
    p90_total = None
    if est and est.get("status") == "ok":
        h = est["hours_from_now"]
        raw["restoration"] = {
            "window": f"{fmt_clock(now + timedelta(hours=h['p10']), now)}-{fmt_clock(now + timedelta(hours=h['p90']), now)}",
            "likely": fmt_clock(now + timedelta(hours=h["p50"]), now),
            "hours_range": f"{round(h['p10'])}-{round(h['p90'])} hrs",
            "confidence": _conf(h, est),
            "drivers": est["drivers"],
            "step_restoration": est["step_restoration"],
        }
        if est.get("calibration", {}).get("thin_history"):
            raw["restoration"]["note"] = "We haven't seen many storms this big, so this range is less certain than usual."
        if est["step_restoration"] and est.get("upstream_fixed_p50") is not None:
            raw["restoration"]["upstream_first_by"] = fmt_clock(now + timedelta(hours=est["upstream_fixed_p50"]), now)
        p90_total = h["p90"]
    raw["actions"] = prep.actions(rk.get("band"), p90_total, needs, "outage" if is_out(customer, world) else "pre_storm")
    if kind == "plan" and est and est.get("status") == "ok":
        h = est["hours_from_now"]
        raw["power_budget"] = prep.power_budget(needs, h["p10"], h["p90"])
    if kind == "worse":
        raw["safety"] = prep.SAFETY
    if kind == "alerts":
        plan = rules.alert_plan(customer, rk, est)
        raw["alerts"] = {k: plan[k] for k in ("eligible", "enrolled", "channels", "eligibility_reason")}
        raw["alerts"]["pre_storm_hours_before"] = plan["pre_storm_alert_hours_before"]
    if extra:
        raw.update({k: v for k, v in extra.items() if k != "battery_mode"})
    staff, etr = ops_lines(customer, world, ds)
    if staff:
        raw["confirmed"] = list(raw.get("confirmed") or []) + staff
    if etr and "restoration" in raw:
        raw["restoration"]["confirmed_etr"] = etr
    return build(raw, audit_who=who(customer.get("phone", "anon")))


def message(kind: str, customer: dict, session: Session | None = None, **kw) -> dict:
    extra = dict(kw.pop("extra", {}) or {})
    if session:
        extra["battery_mode"] = session.battery_mode
    card = make_card(kind, customer, extra=extra, **kw)
    out = render(card, emoji=session.emoji if session else False)
    out["card"] = card
    return out


# ---------- proactive alerts ----------

def proactive(session: Session, world: World = WORLD, ds: Dataset | None = None) -> list[dict]:
    """What would we send this customer right now? Call after the clock or a what-if changes."""
    ds = ds or get_dataset()
    c = session.customer
    out = []

    def send(kind, **kw):
        if session.battery_mode and not rules.allowed_in_battery_mode(kind):
            audit("suppressed_battery_mode", who(c["phone"]), kind)
            return
        m = message(kind, c, session, **kw)
        m["kind"] = kind
        out.append(m)

    if world.phase == "pre_storm":
        rk = risk_for(c, ds)
        lead = rules.pre_storm_lead_hours(rk.get("band"))
        if c.get("alerts_opt_in") and (lead or rk["status"] != "ok"):
            send("pre_storm")
        return out

    start, end = outage_start_h(c, ds, world), restored_h(c, ds, world)
    if start is None or world.t_h < start:
        return out
    if world.t_h >= end:
        if not session.restored_announced:
            session.restored_announced = True
            send("restored", extra={"restored_at": fmt_clock(STORM_START + timedelta(hours=end), world.now(ds))})
        return out
    est, sc = restoration_for(c, world, ds)
    if not session.outage_announced:
        session.outage_announced = True
        send("outage_confirmed", est=est)
        session.last_sent, session.last_scenario = est, sc
    else:
        ok, _ = rules.should_send_update(session.last_sent, est, c, outage_start_h=start)
        reasons = explain_change(session.last_scenario, sc, c["premise_id"], ds) if session.last_scenario else []
        if ok and (reasons or rules.vague_move_is_big(session.last_sent, est)):
            send("estimate_updated", est=est, extra={"reasons_changed": reasons or ["damage assessment is further along"]})
            session.last_sent, session.last_scenario = est, sc
    plan = rules.alert_plan(c, risk_for(c, ds), est, hours_already_out=world.t_h - start)
    for e in plan["escalations"]:
        key = (e["rule"], e.get("need"))
        if key not in session.escalations_sent:
            session.escalations_sent.add(key)
            audit("escalation", who(c["phone"]), e)
            out.append({"kind": "escalation", "action": e["action"], "why": e["why"], "rule": e["rule"]})
    return out


def advance(to_h: float, world: World = WORLD) -> dict[str, list[dict]]:
    """Move the demo clock (storm hours) and collect proactive messages for every known session."""
    ds = get_dataset()
    world.phase = "outage"
    sent = {}
    for t in [x * 0.5 for x in range(int(world.t_h * 2) + 1, int(to_h * 2) + 1)] or [to_h]:
        world.t_h = t
        for phone, s in SESSIONS.items():
            msgs = proactive(s, world, ds)
            if msgs:
                sent.setdefault(phone, []).extend({**m, "at": fmt_clock(world.now(ds), world.now(ds))} for m in msgs)
    world.t_h = to_h
    return deliver(sent)


def reset_world():
    global WORLD
    WORLD.phase, WORLD.t_h, WORLD.what_if = "pre_storm", 0.0, {}
    SESSIONS.clear()
    store.clear()
    ops.clear()


def set_clock(storm_h: float, world: World = WORLD) -> dict[str, list[dict]]:
    """Move the storm clock. Negative = the night before (forecast); moving back in time re-arms alerts."""
    if storm_h < 0:
        world.phase, world.t_h = "pre_storm", 0.0
        persist()
        return {}
    if world.phase == "outage" and storm_h < world.t_h:
        world.t_h = storm_h
        for s in SESSIONS.values():
            s.outage_announced = s.restored_announced = False
            s.last_sent = s.last_scenario = None
            s.escalations_sent = set()
    return advance(storm_h, world)


# ---------- persistence + delivery ----------

def persist(phone: str | None = None) -> None:
    """Save the storm clock and one session (or all of them) so a restart loses nothing."""
    store.save_world(WORLD)
    for p in ([phone] if phone else list(SESSIONS)):
        if p in SESSIONS:
            store.save_session(p, SESSIONS[p])


def restore_state() -> None:
    """Reload linked customers and the storm clock saved before the last shutdown."""
    w = store.load_world()
    if w:
        WORLD.phase, WORLD.t_h, WORLD.what_if = w["phase"], w["t_h"], w["what_if"]
    SESSIONS.update(store.load_sessions(Session))


def deliver(sent: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Text each proactive message to its customer (a dry run unless Twilio is configured),
    record it in their thread, and save the sessions that produced it."""
    for phone, msgs in sent.items():
        for m in msgs:
            if m.get("text"):  # escalations are staff actions, not customer texts
                m["delivery"] = outbound.send(phone, m["text"])
                store.log_message(phone, "out", m["text"], m.get("kind"), m["delivery"])
    persist()
    return sent


def broadcast(world: World = WORLD) -> dict[str, list[dict]]:
    """Run proactive alerts for every session, deliver them, and return what went out."""
    return deliver({p: proactive(s, world) for p, s in list(SESSIONS.items())})
