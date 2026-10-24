"""A stand-in for the Claude API, for tests and for working without an API key.

It mimics the tool-use protocol: picks tools by keyword, then writes a short
answer from the tool results. Answers it produces are labeled "simulated" in
the app so they're never mistaken for the real AI.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace as NS

KEYWORD_TOOLS = [
    (r"\bwhat if\b|\bcrews?\b|\broads?\b|\bpoles?\b", "what_if"),
    (r"\bwhy\b|\bhow (sure|accurate)\b|\btrust\b", "explain_estimate"),
    (r"\bprepare|\bplan\b|\bready\b|\bcpap\b|\boxygen\b|\binsulin\b|\bmedical\b|\bbattery\b|\bstay\b", "get_storm_plan"),
    (r"\balerts?\b|\bqualify\b|\bnotif", "get_alert_info"),
    (r"\bgenerator\b|\bcandles?\b|\bsafe\b", "get_safety_info"),
    (r"\bperson\b|\bhuman\b|\bsomeone\b|\bcall me\b", "request_human"),
]


class _Messages:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        msgs = kw["messages"]
        last = msgs[-1]
        if isinstance(last["content"], str):
            return self._pick_tools(last["content"])
        return self._answer(last["content"], msgs)

    def _pick_tools(self, text):
        names = ["get_outage_status"] + [t for pat, t in KEYWORD_TOOLS if re.search(pat, text, re.I)]
        blocks = []
        for i, n in enumerate(dict.fromkeys(names)):
            args = {}
            if n == "what_if":
                m = re.search(r"(\d+)\s*crews?", text, re.I)
                args = {"crews": int(m.group(1)) if m else 1, "extra_poles": None, "roads_blocked": bool(re.search(r"flood|block", text, re.I)) or None}
            if n == "request_human":
                args = {"reason": "Customer asked for a person."}
            blocks.append(NS(type="tool_use", id=f"toolu_fake_{i}", name=n, input=args))
        return NS(stop_reason="tool_use", content=[NS(type="text", text="Let me check.")] + blocks)

    def _answer(self, results, msgs):
        cards = {}
        prior = msgs[-2]["content"]
        names = {b.id: b.name for b in prior if getattr(b, "type", "") == "tool_use"}
        for r in results:
            try:
                cards[names[r["tool_use_id"]]] = json.loads(r["content"])
            except (KeyError, ValueError):
                pass
        parts = []
        st = cards.get("get_outage_status", {})
        r = st.get("restoration")
        if st.get("kind") == "pre_storm" and st.get("risk"):
            parts.append(f"{st['storm']['name']} arrives in about {st['storm']['eta_hours']} hours. Your outage risk is {st['risk']['band']}, with {st['risk']['confidence']} confidence.")
            if r:
                parts.append(f"If your power goes out, plan for {r['hours_range']}.")
        elif r:
            parts.append(f"Your power is out. Our estimate is that it's back between {r['window']}, most likely around {r['likely']}. That's an estimate, not a promise.")
        elif st.get("fallback"):
            parts.append("We don't have enough history for your area to predict this well. " + st.get("general_guidance", ""))
        else:
            parts.append("Your power is on right now.")
        wi = cards.get("what_if", {}).get("restoration")
        if wi:
            parts.append(f"If that happened, the window would be about {wi['window']} instead. That's a what-if, not a forecast.")
        why = cards.get("explain_estimate", {})
        if why.get("restoration", {}).get("drivers"):
            parts.append("The main reasons: " + "; ".join(why["restoration"]["drivers"][:2]) + ".")
        plan = cards.get("get_storm_plan", {})
        for b in plan.get("power_budget", [])[1:2]:
            parts.append(f"Heads up: around hour {b['hour']:g}, {b['what'][0].lower() + b['what'][1:]}")
        if plan.get("actions"):
            parts.append(plan["actions"][0])
        al = cards.get("get_alert_info", {}).get("alerts")
        if al:
            parts.append(al["eligibility_reason"])
        if "get_safety_info" in cards:
            parts.append(cards["get_safety_info"].get("safety", [""])[1])
        if "request_human" in cards:
            parts.append("I've asked a person from our team to call you.")
        return NS(stop_reason="end_turn", content=[NS(type="text", text=" ".join(p for p in parts if p))])


class FakeClaude:
    def __init__(self):
        self.beta = NS(messages=_Messages())
