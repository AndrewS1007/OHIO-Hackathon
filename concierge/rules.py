"""Alert eligibility, timing, channel and escalation rules.

These are written-down if/then rules (not a model) so judges and operators can
read exactly why someone got a text or a call. RULES is shown in the web view.
"""
from __future__ import annotations

RULES = [
    ("R1", "Any residential customer with a mobile number qualifies for outage alerts. Alerts start once they opt in."),
    ("R2", "Pre-storm alert: 24 h before arrival for High/Severe risk, 12 h for Elevated, none for Low."),
    ("R3", "Channel: SMS by default. A declared medical need adds a voice call. Battery mode keeps only essential texts."),
    ("R4", "Escalate: if a declared device's battery runs out before our P90 restoration time, place a check-in call; no answer in 15 min goes to a human."),
    ("R5", "Escalate: any declared medical need with P90 over 8 h gets human outreach."),
    ("R6", "Send an 'estimate updated' text when there's a concrete reason (damage found or fixed, crews changed) and the likely time moves by 1 h or 10% of the wait, whichever is more; without a concrete reason, only when it moves by a quarter of the window. Always when the range crosses a declared battery limit."),
    ("R7", "When we can't predict reliably, a human reviews the message before it goes out."),
]

ESSENTIAL_IN_BATTERY_MODE = {"outage_confirmed", "restored", "safety", "escalation"}


def pre_storm_lead_hours(band: str | None) -> int | None:
    return {"Severe": 24, "High": 24, "Elevated": 12}.get(band or "")


def alert_plan(customer: dict, risk: dict, restoration: dict | None = None, battery_mode: bool = False,
               hours_already_out: float = 0.0) -> dict:
    needs = customer.get("declared_needs", [])
    opted = customer.get("alerts_opt_in", False)
    plan = {
        "eligible": True,
        "enrolled": opted,
        "eligibility_reason": "Yes, you qualify: you have a mobile number on file." + ("" if opted else " Reply JOIN to start alerts."),
        "channels": ["sms"] + (["voice"] if needs else []),
        "pre_storm_alert_hours_before": pre_storm_lead_hours(risk.get("band")),
        "battery_mode": battery_mode,
        "escalations": [],
        "rules_applied": ["R1", "R3"],
    }
    if plan["pre_storm_alert_hours_before"]:
        plan["rules_applied"].append("R2")
    if risk.get("status") == "insufficient_data":
        plan["escalations"].append({"rule": "R7", "action": "human_review", "why": "Not enough data for a reliable prediction"})
    p90 = (restoration or {}).get("hours_from_now", {}).get("p90")
    if p90 is not None:
        p90 = round(p90 + hours_already_out, 1)  # total outage length, compared with battery life
        for n in needs:
            bh = n.get("battery_hours")
            if bh is not None and bh < p90:
                plan["escalations"].append({
                    "rule": "R4", "action": "check_in_call", "need": n["need"],
                    "why": f"Your {n['need']} battery ({bh} h) may run out before power is back (it could be out up to about {round(p90)} h)",
                })
        if needs and p90 > 8:
            plan["escalations"].append({"rule": "R5", "action": "human_outreach", "why": "Medical need with a long possible outage"})
    for e in plan["escalations"]:
        plan["rules_applied"].append(e["rule"])
    return plan


def _abs(est: dict, q: str) -> float:
    """Estimate as a clock time (hours since storm start), so hourly re-runs compare fairly."""
    return est.get("now_h", 0.0) + est["hours_from_now"][q]


def should_send_update(prev: dict | None, new: dict, customer: dict, outage_start_h: float = 0.0) -> tuple[bool, str]:
    """R6: is the change big enough to text about?"""
    if new.get("status") != "ok":
        return False, "no estimate"
    if not prev or prev.get("status") != "ok":
        return True, "first estimate"
    remaining = max(new["hours_from_now"]["p50"], 0.0)
    if abs(_abs(prev, "p50") - _abs(new, "p50")) >= max(1.0, 0.1 * remaining):
        return True, "likely time moved by 1 h or 10% of the wait"
    for n in customer.get("declared_needs", []):
        bh = n.get("battery_hours")
        if bh is not None:
            before = _abs(prev, "p90") - outage_start_h <= bh
            after = _abs(new, "p90") - outage_start_h <= bh
            if before != after:
                return True, f"range crossed the {n['need']} battery limit"
    return False, "no meaningful change"


def vague_move_is_big(prev: dict, new: dict) -> bool:
    """With no concrete reason to point to, only text if the likely time moved by a quarter of the window."""
    h = new["hours_from_now"]
    return abs(_abs(prev, "p50") - _abs(new, "p50")) >= max(2.0, 0.25 * (h["p90"] - h["p10"]))


def allowed_in_battery_mode(kind: str) -> bool:
    return kind in ESSENTIAL_IN_BATTERY_MODE
