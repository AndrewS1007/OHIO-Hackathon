"""Privacy firewall: the "fact card".

The LLM (and every outbound message) only ever sees a fact card. A fact card
is built from whitelisted keys, then scrubbed for anything that looks like an
internal ID, phone number, or coordinate. Every card is written to the audit log.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from .config import LOG_DIR

ALLOWED_KEYS = {
    "kind", "storm", "name", "eta_hours", "max_wind_mph",
    "risk", "band", "confidence", "top_factors", "history", "similar_storms_summary",
    "restoration", "window", "likely", "hours_range", "drivers", "step_restoration", "upstream_first_by",
    "confirmed", "estimate", "actions", "reasons_changed", "power_budget",
    "alerts", "eligible", "enrolled", "channels", "pre_storm_hours_before", "eligibility_reason",
    "honesty", "battery_mode", "declared_needs", "fallback", "general_guidance", "restored_at",
    "safety", "is_model_estimate", "hour", "what", "note",
}
SENSITIVE = [
    re.compile(r"\bC\d{2}(-[A-Z]\d?)+\b"),  # device / circuit ids (mock)
    re.compile(r"\bP\d{5,}\b"),  # premise ids (mock: P12345, real: P166621239)
    re.compile(r"\b[XFRSBT]\d{6,}\b"),  # real device ids (transformer, fuse, recloser, ...)
    re.compile(r"\b(CIR|STATION)\d+\b"),  # real circuit / substation names
    re.compile(r"\+?\d[\d\-\s]{9,}\d"),  # phone numbers
    re.compile(r"-?\d{1,3}\.\d{4,}"),  # coordinates
    re.compile(r"\bINC\d+\b"),  # incident ids
]


class PrivacyError(ValueError):
    pass


def _scrub(obj, path="card"):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k not in ALLOWED_KEYS:
                continue  # silently drop anything not whitelisted
            out[k] = _scrub(v, f"{path}.{k}")
        return out
    if isinstance(obj, list):
        return [_scrub(v, path) for v in obj]
    if isinstance(obj, str):
        for pat in SENSITIVE:
            if pat.search(obj):
                raise PrivacyError(f"sensitive-looking value blocked at {path}")
        return obj
    return obj


def build(raw: dict, audit_who: str = "anon") -> dict:
    card = _scrub(raw)
    audit("fact_card", audit_who, card)
    return card


def audit(event: str, who: str, payload) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "audit.jsonl", "a") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "event": event,
                            "who": who, "payload": payload}, default=str) + "\n")


def recent_audit(n: int = 50) -> list[dict]:
    p = LOG_DIR / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines()[-n:]]
