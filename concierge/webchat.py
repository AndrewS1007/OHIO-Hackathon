"""Web chat wording: the shared message templates are written for SMS ("Reply WHY", "[EST]"),
where those keywords work. On the website they don't, so web replies say it in plain words,
and anyone who types a keyword anyway gets what they meant.
"""
from __future__ import annotations

import re

# SMS keyword -> the question it stands for (what the web chat should answer instead)
KEYWORDS = {
    "WHEN": "When will my power be back?",
    "WHY": "Why is my power out, and why is it taking this long?",
    "PLAN": "What's my storm plan?",
    "ALERTS": "Do I get outage alerts?",
    "WORSE": "What should I do to stay safe?",
    "OUT": "My power is still out.",
    "STATUS": "When will my power be back?",
}
PERSON = re.compile(r"^\s*(help|agent|human|person)\s*[.!]?\s*$|\b(talk|speak)\b.{0,20}\b(person|human|someone|agent|representative)\b"
                    r"|\b(call me|call back|callback|real person|live agent)\b", re.I)

_REWRITES = [
    (r"\s*Try WHEN, WHY, PLAN or HELP\.", " I can tell you when your power will be back and why, go over your storm plan, or connect you with a person."),
    (r"\s*Reply PLAN/WHY\.", " Ask me why, or for your storm plan."),
    (r"\s*Reply WHY or PLAN\.", " Ask me why, or for your storm plan."),
    (r"\s*Reply WHY\.", " Ask me why if you'd like the details."),
    (r"\s*Reply PLAN\.", " Ask me for your storm plan."),
    (r"\s*Reply WHEN\.", " Ask me when your power will be back."),
    (r"\s*Reply HELP to reach a person\.", " You can ask to talk to a person any time."),
    (r"\s*Still out\? Reply OUT\.", " Still out? Tell me and we'll check your line."),
    (r"\s*Reply JOIN to start alerts\.", " Text JOIN to our outage number to start alerts."),
    (r"\s*Text (?:WHEN|WHY|PLAN|ALERTS|WORSE|HELP)[^.]*\.", ""),
    (r"\b(?:reply|text|send)\s+HELP\b", "ask to talk to a person"),
    (r"\[(?:OK|EST|DO|!|BATTERY)\]\s*", ""),
]


def question_for(text: str) -> str:
    """A bare SMS keyword typed into the web chat becomes the question it stands for."""
    return KEYWORDS.get(text.strip().strip(".!?").upper(), text)


def wants_person(text: str) -> bool:
    return bool(PERSON.search(text))


def for_web(text: str | None) -> str | None:
    if not text:
        return text
    for pat, rep in _REWRITES:
        text = re.sub(pat, rep, text)
    return re.sub(r"\s{2,}", " ", text).strip()
