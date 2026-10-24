"""Turn a fact card into a short message.

Templates always work. If CONCIERGE_USE_LLM=1 and Claude credentials are set,
Claude rewrites the template more naturally, and the result must pass three checks
or we fall back to the template:
  1. every number in the text appears in the fact card (the LLM never invents numbers)
  2. it fits the SMS length budget
  3. it reads at about grade 6-8
"""
from __future__ import annotations

import json
import os
import re

from .factcard import audit

MODEL = "claude-opus-5-5"
GSM7 = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
ALWAYS_OK_NUMBERS = {"911", "35"}

LANES = {
    True: {"ok": "✅", "est": "🔮", "do": "👉", "warn": "⚠️", "bat": "🔋"},
    False: {"ok": "[OK]", "est": "[EST]", "do": "[DO]", "warn": "[!]", "bat": "[BATTERY]"},
}


# ---------- SMS helpers ----------

GSM7_EXT = set("^{}\\[~]|€\f")  # valid in GSM-7 but cost 2 units each


def sms_segments(text: str) -> int:
    if all(ch in GSM7 or ch in GSM7_EXT for ch in text):
        units = sum(2 if ch in GSM7_EXT else 1 for ch in text)
        return 1 if units <= 160 else -(-units // 153)
    units = len(text.encode("utf-16-le")) // 2
    return 1 if units <= 70 else -(-units // 67)


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    groups = re.findall(r"[aeiouy]+", w)
    n = len(groups) - (1 if w.endswith("e") and len(groups) > 1 else 0)
    return max(1, n)


def reading_grade(text: str) -> float:
    words = re.findall(r"[A-Za-z']+", text)
    if not words:
        return 0.0
    sentences = max(1, len(re.findall(r"[.!?]+", text)))
    syl = sum(_syllables(w) for w in words)
    return round(0.39 * len(words) / sentences + 11.8 * syl / len(words) - 15.59, 1)


def numbers_in(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def numbers_grounded(text: str, card: dict) -> bool:
    allowed = numbers_in(json.dumps(card, ensure_ascii=False)) | ALWAYS_OK_NUMBERS
    return numbers_in(text) <= allowed


# ---------- templates ----------

def _first(xs, default=""):
    return xs[0] if xs else default


def template(card: dict, emoji: bool = True) -> str:
    L = LANES[emoji]
    k = card["kind"]
    r = card.get("restoration", {})
    risk = card.get("risk", {})
    act = _first(card.get("actions", []))
    if card.get("fallback"):
        return (f"{L['est']} We don't have enough history for your area to predict this reliably. "
                f"{L['do']} {card.get('general_guidance', 'Plan for up to a day without power.')} We'll text when we know more. Reply PLAN.")
    if k == "pre_storm":
        s = card["storm"]
        return (f"{L['est']} {s['name']} hits in ~{s['eta_hours']}h. Outage risk: {risk['band']} ({risk['confidence']} conf.). "
                f"If out: {r['hours_range']}. {L['do']} {act} Reply PLAN/WHY.")
    if k in ("status", "outage_confirmed"):
        head = f"{L['ok']} Outage confirmed." if k == "outage_confirmed" else f"{L['ok']} {_first(card.get('confirmed', []), 'Your power is out.')}"
        return f"{head} {L['est']} Back ~{r['window']} ({r['confidence']} conf.). {L['do']} {act} Reply WHY."
    if k == "estimate_updated":
        why = _first(card.get("reasons_changed", []), "new info from crews")
        return f"{L['est']} Update: now back ~{r['window']}. Why: {why}. {L['do']} {act}"
    if k == "restored":
        return (f"{L['ok']} Power restored at {card.get('restored_at', 'now')}. Still out? Reply OUT. "
                f"{L['do']} Toss fridge food that was above 40F for over 4 hrs.")
    if k == "why":
        parts = []
        if r.get("drivers"):
            parts.append("What matters most: " + "; ".join(r["drivers"][:2]) + ".")
        if risk.get("top_factors"):
            parts.append("Risk is " + risk["band"] + " because: " + "; ".join(risk["top_factors"][:2]) + ".")
        if risk.get("history"):
            parts.append(risk["history"] + ".")
        if card.get("honesty"):
            parts.append(card["honesty"] + ".")
        parts.append("This is our estimate, not a promise.")
        return " ".join(parts)
    if k == "alerts":
        a = card["alerts"]
        ch = " and a call" if "voice" in a.get("channels", []) else ""
        return f"{L['ok']} {a['eligibility_reason']} You'll get texts{ch} before the storm, when power goes out, if the estimate changes, and when it's back."
    if k == "worse":
        return f"{L['warn']} " + " ".join(card.get("safety", []))
    if k == "plan":
        lines = [f"{L['do']} Your storm plan:"] + [f"- {a}" for a in card.get("actions", [])[:4]]
        for b in card.get("power_budget", []):
            lines.append(f"- Hour {b['hour']:g}: {b['what']} ({b['risk']})")
        return "\n".join(lines)
    if k == "battery":
        return f"{L['bat']} Battery mode on. We'll only text when power is confirmed out or back, or if you're in danger. Tip: turn on low-power mode and dim your screen."
    if k == "no_outage":
        return f"{L['ok']} No damage reported on your line right now. {L['est']} Risk: {risk.get('band', 'unknown')}. Reply WHY or PLAN."
    return json.dumps(card)


# ---------- optional LLM rewrite ----------

SYSTEM = """You write SMS messages for a power-outage assistant.
Rewrite the DRAFT so it sounds warm and clear, using ONLY facts in the FACT CARD.
Rules:
- Never add a number, time, or fact that is not in the fact card.
- Keep the three lanes in this order if present: confirmed facts, our estimate, the customer's action.
- Keep the lane markers from the draft exactly.
- Plain words, short sentences, grade 6 reading level.
- At most {max_chars} characters. Output only the message text."""


import httpx
import json

def _llm_enabled() -> bool:
    # Always turn on if the variable is set
    return os.environ.get("CONCIERGE_USE_LLM") == "1"

def _llm_rewrite(card: dict, draft: str, max_chars: int) -> str | None:
    try:
        system_prompt = SYSTEM.format(max_chars=max_chars)
        user_prompt = f"FACT CARD:\n{json.dumps(card, ensure_ascii=False)}\n\nDRAFT:\n{draft}"
        
        # Call the local Ollama API
        response = httpx.post(
            "http://localhost:11434/api/chat",
            json={
                "model": "qwen2.5:3b", # Match the model you downloaded
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "stream": False
            },
            timeout=30.0 # Give your local machine a bit of time to think
        )
        response.raise_for_status()
        
        text = response.json().get("message", {}).get("content", "").strip()
        return text or None
        
    except Exception as e:
        from .factcard import audit
        audit("llm_error", "system", repr(e))
        return None


def render(card: dict, emoji: bool = True, max_segments: int = 2, use_llm: bool | None = None) -> dict:
    draft = template(card, emoji)
    out = {"text": draft, "source": "template"}
    long_form = card["kind"] in ("plan", "why")
    if (use_llm if use_llm is not None else _llm_enabled()) and not long_form:
        max_chars = 140 * max_segments if not emoji else 67 * max_segments
        cand = _llm_rewrite(card, draft, max_chars)
        checks = {}
        if cand:
            checks = {"numbers_grounded": numbers_grounded(cand, card),
                      "fits_sms": sms_segments(cand) <= max_segments,
                      "reading_ok": reading_grade(cand) <= 8.5}
            if all(checks.values()):
                out = {"text": cand, "source": "llm"}
        out["llm_checks"] = checks
    out["segments"] = sms_segments(out["text"])
    out["reading_grade"] = reading_grade(out["text"])
    audit("message", "system", {"kind": card["kind"], **out})
    return out
