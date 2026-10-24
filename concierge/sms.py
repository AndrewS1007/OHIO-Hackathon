"""Inbound SMS handling. Order matters:

  1. safety gate (danger keywords -> fixed safety text; guardrails -> refusal)
  2. account linking
  3. keywords (WHY, WORSE, HELP, PLAN, JOIN, BATTERY n, EMOJI ON/OFF)
  4. intent from plain words ("when's my power back?")
"""
from __future__ import annotations

import copy
import re

from . import safety
from .data import get_dataset
from .factcard import audit
from .narrate import sms_segments
from .service import SESSIONS, WORLD, Session, is_out, message, who

MENU = "Text WHEN, WHY, PLAN, ALERTS, WORSE (safety) or HELP (a person)."


def _reply(text: str, **meta) -> dict:
    return {"text": text, "segments": sms_segments(text), **meta}


def _link(phone: str, body: str) -> Session | None:
    ds = get_dataset()
    m = re.match(r"^\s*(link|start)\s+(\S+)", body, re.I)
    if body.strip().upper() == "DEMO":
        # Judges text DEMO from their own phone and become a copy of Maria.
        c = copy.deepcopy(ds.customers[0])
        c["phone"] = phone
    elif m and ds.path(m.group(2).upper()):
        c = {"phone": phone, "premise_id": m.group(2).upper(), "alerts_opt_in": True, "declared_needs": []}
    else:
        known = ds.customer_by_phone(phone)
        if not known:
            return None
        c = copy.deepcopy(known)
    s = Session(customer=c)
    SESSIONS[phone] = s
    return s


def handle(phone: str, body: str) -> dict:
    audit("inbound_sms", who(phone), {"chars": len(body)})  # don't log message text: it may hold personal info
    gate = safety.check(body)
    if gate.kind == "danger":
        audit("danger_escalation", who(phone), {"category": gate.category})
        s = SESSIONS.get(phone)
        prefix = "⚠️ " if (s is not None and s.emoji) else ""
        return _reply(prefix + gate.reply, gate="danger", category=gate.category, escalated_to_human=True)
    if gate.kind == "guardrail":
        audit("guardrail", who(phone), {"category": gate.category})
        return _reply(gate.reply, gate="guardrail", category=gate.category)

    s = SESSIONS.get(phone)
    if s is None or re.match(r"^\s*(link|start)\b|^\s*demo\s*$", body, re.I):
        s = _link(phone, body)
        if s is None:
            return _reply("Hi! This is the outage assistant. Text LINK and your outage code (on your bill) to get personal updates. " + MENU)
        if re.match(r"^\s*(link|start|demo)\b", body, re.I):
            return _reply("You're linked. " + MENU)

    c = s.customer
    cmd = body.strip().upper()

    m = re.match(r"^BATTERY\s*(\d{1,3})\s*%?$|^BATTERY\s+(OFF)$", cmd)
    if m:
        if m.group(2) == "OFF" or int(m.group(1)) > 30:
            s.battery_mode = False
            return _reply("Battery mode off. You'll get all updates again.")
        s.battery_mode = True
        return _reply(message("battery", c, s)["text"])
    if cmd in ("EMOJI OFF", "PLAIN"):
        s.emoji = False
        return _reply("OK, plain text from now on.")
    if cmd == "EMOJI ON":
        s.emoji = True
        return _reply("OK, emoji on.")
    if cmd in ("HELP", "AGENT", "HUMAN", "PERSON"):
        audit("human_requested", who(phone), {})
        return _reply("A person from our team will call you at this number. If anyone is in danger, call 911 now.", escalated_to_human=True)
    if cmd in ("JOIN", "YES"):
        c["alerts_opt_in"] = True
        return _reply("You're signed up for outage alerts. Text STOP to end them.")
    if cmd == "STOP":
        c["alerts_opt_in"] = False
        return _reply("Alerts stopped. Text JOIN to start again.")
    if cmd == "OUT":
        audit("still_out_report", who(phone), {})
        return _reply("Thanks for telling us. Your home may have damage of its own. We've opened a ticket and will text you a new estimate.", escalated_to_human=True)

    kind = None
    if cmd in ("WHY", "WHY?"):
        kind = "why"
    elif cmd in ("WORSE", "SAFETY") or re.search(r"\bgenerator\b", body, re.I):
        kind = "worse"
    elif cmd in ("PLAN", "PREP") or re.search(r"\b(prepare|prep|ready|what should i do)\b", body, re.I):
        kind = "plan"
    elif cmd in ("ALERTS", "ALERT") or re.search(r"\b(alerts?|qualify|sign me up|notif)", body, re.I):
        kind = "alerts"
    elif cmd in ("WHEN", "STATUS") or re.search(r"\b(when|how long|back on|restor|eta|lose power|risk|will i|outage)\b", body, re.I):
        kind = "status"
    if kind is None:
        return _reply("Sorry, I didn't get that. " + MENU)

    if kind == "status":
        if WORLD.phase == "pre_storm":
            kind = "pre_storm"
        elif is_out(c):
            kind = "status"
        else:
            kind = "no_outage"
    out = message(kind, c, s,
                  extra={"confirmed": ["Your power is out. Crews are working on it."]} if kind == "status" else None)
    return _reply(out["text"], source=out["source"], kind=kind)
