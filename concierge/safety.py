"""Safety gate: runs on every inbound message BEFORE anything else.

1. Danger keywords -> fixed, pre-approved safety message + human escalation.
   The AI is never in this path, so it can't soften or delay the warning.
2. Guardrails -> polite refusal + what we *can* tell them, for requests about
   other people, critical-facility locations, crews, internal data, or attempts
   to override the assistant's instructions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DANGER = [
    ("downed_line", [
        r"\b(wire|line|cable|pole)s?\b.{0,25}\b(down|fell|fallen|on the ground|sparking|arcing|hanging|snapped)\b",
        r"\b(downed|fallen|sparking|arcing|broken) (power )?(wire|line|cable|pole)s?\b",
        r"\bsparks?\b|\barcing\b",
    ], "SAFETY: Stay at least 35 ft from the wire and anything touching it. Assume it is live. Call 911 now. We've alerted our emergency team."),
    ("fire_smoke", [
        r"\bfire\b(?! (station|department|dept|house))", r"\bflames?\b", r"\bsmoke\b", r"\bburning smell\b", r"\bsmell(s|ing)? (like )?burning\b",
    ], "SAFETY: Get everyone out now and call 911. Don't touch electrical equipment. Stay 35 ft from any downed wires."),
    ("gas_co", [
        r"\bsmell(s|ing)? (of )?gas\b", r"\bgas (leak|smell)\b", r"\bcarbon monoxide\b", r"\bco (alarm|detector)\b",
        r"\bgenerator (fumes|exhaust)\b", r"\bfumes\b",
        r"\bgenerator\b.{0,30}\b(inside|indoors|in the (garage|house|basement|kitchen|living room)|in my (garage|house|basement))\b",
    ], "SAFETY: Leave the building now and call 911 from outside. Never run a generator indoors, in a garage, or near windows."),
    ("shock", [
        r"\belectrocut", r"\b(got|getting|been) (a )?shock(ed)?\b", r"\bshocked\b", r"\btingl(e|ing)\b.{0,20}\b(tap|faucet|water|outlet|metal)\b",
    ], "SAFETY: Don't touch the person if they may still be touching a power source. Call 911 now. Stay 35 ft from downed wires."),
    ("medical", [
        r"\bcan'?t breathe\b", r"\bnot breathing\b", r"\bchest pain\b", r"\bunconscious\b", r"\bpassed out\b",
        r"\boxygen\b.{0,20}\b(out|ran out|running out|stopped|failing|died)\b", r"\bmedical emergency\b",
    ], "If this is an emergency, call 911 now. We've flagged your account for an urgent call from our team."),
    ("water_electric", [
        r"\bwater\b.{0,25}\b(panel|outlet|breaker box|fuse box)\b", r"\bflood(ed|ing)?\b.{0,30}\b(panel|outlet|electric)",
    ], "SAFETY: Stay out of water near outlets or the electric panel. Don't touch the panel while standing in water. Call 911 if anyone is in danger."),
]

GUARDRAILS = [
    ("injection", [
        r"\bignore (all |any |your |the |previous |prior )*(instructions|rules|prompts?)\b", r"\bsystem prompt\b",
        r"\byou are now\b", r"\bpretend (to be|you are)\b", r"\bdeveloper mode\b", r"\bjailbreak\b",
    ], "I can only help with outage info for your own account. Try WHEN, WHY, PLAN or HELP."),
    ("other_people", [
        r"\b(who|which|neighbou?rs?|others?|anyone|someone)\b.{0,40}\b(medical|cpap|oxygen|life.?support|disab\w*|insulin|dialysis)\b",
        r"\b(neighbou?r|someone else)'?s?\b.{0,30}\b(account|address|phone|name|info|details)\b",
    ], "I can only share information about your own account. Neighbors can text this number to get their own updates."),
    ("facility_location", [
        r"\b(where|address|location|located|exact|coordinates|map)\b.{0,40}\b(hospital|fire (station|department)|ambulance|substation|water (treatment|plant)|critical facilit\w*)",
        r"\b(hospital|fire (station|department)|ambulance|substation|water (treatment|plant)|critical facilit\w*)\b.{0,40}\b(where|address|location|located|coordinates)\b",
    ], "For safety, we don't share the location of critical facilities or equipment. I can tell you whether one affects restoration on your circuit. Reply WHY."),
    ("crew_info", [
        r"\b(crew|lineman|linemen|line workers?|workers?|employees?|technicians?|staff)\b.{0,30}\b(names?|phone|numbers?|contact|where|location|located|address)\b",
        r"\b(where|name of|who)\b.{0,20}\b(crew|lineman|linemen|workers?|technicians?)\b",
    ], "We don't share crew names, contacts or locations. I can tell you if a crew is assigned to your area. Reply WHEN."),
    ("internal_data", [
        r"\b(device ids?|circuit ids?|internal|raw data|database|latitude|longitude|gps|coordinates|other premises?)\b",
    ], "I can't share internal system data. I can explain your estimate in plain words. Reply WHY."),
]

_DANGER = [(k, [re.compile(p, re.I) for p in pats], msg) for k, pats, msg in DANGER]
_GUARD = [(k, [re.compile(p, re.I) for p in pats], msg) for k, pats, msg in GUARDRAILS]


@dataclass
class GateResult:
    kind: str  # "danger" | "guardrail" | "ok"
    category: str | None = None
    reply: str | None = None
    escalate: bool = False


def check(text: str) -> GateResult:
    t = text.strip()
    for cat, pats, msg in _DANGER:
        if any(p.search(t) for p in pats):
            return GateResult("danger", cat, msg, escalate=True)
    for cat, pats, msg in _GUARD:
        if any(p.search(t) for p in pats):
            return GateResult("guardrail", cat, msg)
    return GateResult("ok")
