"""The AI concierge: an LLM answers free-form questions by calling our engines.

How it stays trustworthy:
  - The safety gate runs BEFORE this (danger words never reach the AI).
  - Tools are bound to the customer's own account server-side. Claude can't ask
    about another address, and every tool returns a privacy-scrubbed fact card.
  - Grounding check: every number in Claude's reply must appear in a tool
    result (or in the customer's own message). Otherwise we discard the reply
    and send a safe template answer instead.
  - No model reachable, a network error, or a refusal -> the keyword bot answers instead.

Backends (CONCIERGE_AI picks one; default "auto"):
  ollama  the local `outage-concierge` model from our Modelfile (OLLAMA_URL, OLLAMA_MODEL)
  claude  the Anthropic API (ANTHROPIC_API_KEY or an `ant auth login` profile)
  auto    Ollama if it's running with our model, else Claude if credentials exist
  0       keyword mode only
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from . import service
from .factcard import audit
from .narrate import numbers_grounded, numbers_in

MODEL = "claude-opus-5-5"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "outage-concierge")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "240"))  # CPU-only laptops take a while per round
MAX_TOOL_ROUNDS = 5
MAX_HISTORY_MESSAGES = 24  # start a fresh conversation after this, rather than editing old turns

SYSTEM = """You are the Outage Concierge for an electric utility, helping one customer before and during a storm.
You talk to them by SMS or a small chat window, often while their power is out and their phone battery is low.

How to answer:
- Use the tools to look things up. Never guess an outage risk, a time, a range, or a number of any kind: every number and time you say must come from a tool result or from the customer's own message.
- Restoration times are estimates, not promises. Say so briefly when you give one, and give the range, not just one time.
- When you explain why, use the reasons the tools give (damage found, crews, past storms).
- If they mention a medical device, need, or risk, take it seriously: check their storm plan and alert info, and offer HELP (a person calls them) when their power could be out longer than their device lasts. Only use needs they tell you or that the tools list; never assume a medical condition.
- If anyone may be in danger (downed wire, fire, gas, someone hurt), tell them to call 911 first.
- You can only discuss this customer's own service. You can't share other customers' information, exact locations of hospitals or utility equipment, or crew names and locations.
- If the tools can't answer something, say so plainly and suggest HELP to reach a person.

Style: warm, plain words, short sentences, about a 6th-grade reading level. No markdown, no bullet symbols, no emoji. Keep it under {max_chars} characters unless they ask for detail."""

TOOLS = [
    {
        "name": "get_outage_status",
        "description": "The customer's current situation: storm forecast, outage risk band with confidence and top reasons, whether their power is out, and the restoration window if it is. Call this first for almost any question about their power.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "explain_estimate",
        "description": "Why the risk and restoration estimate are what they are: damage on the line feeding them, crew situation, history of past storms, and how accurate our past ranges were on their circuit.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "what_if",
        "description": "Re-run the restoration estimate under a hypothetical: a different number of crews, extra broken poles on their street, or blocked roads. Results are hypothetical, not a forecast; say so.",
        "input_schema": {
            "type": "object",
            "properties": {
                "crews": {"type": ["integer", "null"], "description": "Crews working their circuit (1-6), or null to keep as is."},
                "extra_poles": {"type": ["integer", "null"], "description": "Extra broken poles on their street (0-3), or null."},
                "roads_blocked": {"type": ["boolean", "null"], "description": "True if roads are flooded or blocked, or null."},
            },
            "required": ["crews", "extra_poles", "roads_blocked"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_storm_plan",
        "description": "The customer's personal preparation list and power-budget timeline (for example when the fridge stops being safe or a declared device battery runs out), based on the restoration range and the needs they declared.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "get_alert_info",
        "description": "Whether the customer qualifies for outage alerts, if they're signed up, which channels they'd get (text, call), and when pre-storm alerts go out.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "get_safety_info",
        "description": "Approved safety guidance: downed wires, generators, candles, carbon monoxide.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "request_human",
        "description": "Ask a person from the utility to call the customer back. Use when they ask for a person, when a declared medical need may outlast the outage, or when you can't help.",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "One short sentence on why."}},
            "required": ["reason"],
            "additionalProperties": False,
        },
    },
]
TOOL_LABELS = {
    "get_outage_status": "Checked outage status", "explain_estimate": "Looked up the reasons",
    "what_if": "Ran a what-if", "get_storm_plan": "Pulled the storm plan", "get_alert_info": "Checked alert rules",
    "get_safety_info": "Pulled safety guidance", "request_human": "Asked a person to call",
}


@dataclass
class ChatContext:
    """Who we're talking to and when. The tools can only see this customer."""
    customer: dict
    world: service.World
    channel: str = "web"  # web | sms
    who: str = "anon"


@dataclass
class Conversation:
    messages: list = field(default_factory=list)


CONVERSATIONS: dict[str, Conversation] = {}


# ---------- tools ----------

def _status_kind(ctx: ChatContext) -> str:
    if ctx.world.phase == "pre_storm":
        return "pre_storm"
    return "status" if service.is_out(ctx.customer, ctx.world) else "no_outage"


def run_tool(name: str, args: dict, ctx: ChatContext) -> dict:
    c, w = ctx.customer, ctx.world
    if name == "get_outage_status":
        kind = _status_kind(ctx)
        extra = {"confirmed": ["Your power is out. Crews are working on it."]} if kind == "status" else None
        return service.make_card(kind, c, w, extra=extra)
    if name == "explain_estimate":
        return service.make_card("why", c, w)
    if name == "what_if":
        hypo = service.World(w.phase, w.t_h, dict(w.what_if))
        crews, poles = args.get("crews"), args.get("extra_poles")
        if isinstance(crews, (int, float)) and crews:
            hypo.what_if["crews"] = max(1, min(6, int(crews)))
        if isinstance(poles, (int, float)):
            hypo.what_if["extra_poles"] = max(0, min(3, int(poles)))
            hypo.what_if.setdefault("extra_poles_at", w.t_h if w.phase == "outage" else 0.0)
        if isinstance(args.get("roads_blocked"), bool):
            hypo.what_if["access"] = 1.5 if args["roads_blocked"] else 1.0
        if hypo.phase == "outage" and not service.is_out(c, hypo):
            return {"kind": "what_if", "note": "Their power isn't out right now in this scenario, so there's nothing to re-estimate."}
        est, _ = service.restoration_for(c, hypo)
        card = service.make_card(_status_kind(ctx), c, hypo, est=est)
        card["kind"] = "what_if"
        return {**card, "hypothetical": True}
    if name == "get_storm_plan":
        return service.make_card("plan", c, w)
    if name == "get_alert_info":
        return service.make_card("alerts", c, w)
    if name == "get_safety_info":
        return service.make_card("worse", c, w)
    if name == "request_human":
        audit("human_requested", ctx.who, {"via": "ai_concierge", "reason": str(args.get("reason", ""))[:200]})
        return {"kind": "human", "status": "requested", "message": "A person from our team will call this customer back."}
    raise ValueError(f"unknown tool {name}")


# ---------- the loop ----------

_ollama_seen: tuple[float, bool] = (0.0, False)


def ollama_ready() -> bool:
    """Is Ollama running with our model? Cached for 30 s so the chat stays fast."""
    global _ollama_seen
    if time.monotonic() - _ollama_seen[0] < 30:
        return _ollama_seen[1]
    try:
        import httpx
        names = {m["name"] for m in httpx.get(f"{OLLAMA_URL}/api/tags", timeout=2).json().get("models", [])}
        ok = OLLAMA_MODEL in names or f"{OLLAMA_MODEL}:latest" in names
    except Exception:
        ok = False
    _ollama_seen = (time.monotonic(), ok)
    return ok


def _claude_ready() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("CONCIERGE_FAKE_CLAUDE"):
        return True
    return os.path.isdir(os.path.expanduser("~/.config/anthropic"))  # `ant auth login` profile


def backend() -> str | None:
    """"ollama", "claude", or None (keyword mode)."""
    choice = os.environ.get("CONCIERGE_AI", "auto").lower()
    if choice == "0":
        return None
    if os.environ.get("CONCIERGE_FAKE_CLAUDE"):
        return "claude"
    if choice == "ollama":
        return "ollama" if ollama_ready() else None
    if choice == "claude":
        return "claude" if _claude_ready() else None
    if ollama_ready():
        return "ollama"
    return "claude" if _claude_ready() else None


def model_name() -> str | None:
    return {"ollama": OLLAMA_MODEL, "claude": MODEL}.get(backend())


def available() -> bool:
    return backend() is not None


def _client():
    if os.environ.get("CONCIERGE_FAKE_CLAUDE"):
        from .fake_claude import FakeClaude
        return FakeClaude()
    import anthropic
    return anthropic.Anthropic(max_retries=1, timeout=45.0)


def _text(content) -> str:
    return "".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text").strip()


OLLAMA_TOOLS = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                 "parameters": t["input_schema"]}} for t in TOOLS]


def _ollama_chat(messages: list) -> dict:
    import httpx
    r = httpx.post(f"{OLLAMA_URL}/api/chat", timeout=OLLAMA_TIMEOUT, json={
        "model": OLLAMA_MODEL, "messages": messages, "tools": OLLAMA_TOOLS,
        "stream": False, "keep_alive": "30m"})
    r.raise_for_status()
    return r.json()["message"]


def _ask_ollama(conv: Conversation, ctx: ChatContext, steps: list, tool_outputs: list) -> str:
    """Tool loop against the local model. Its rules live in the Modelfile's SYSTEM.

    Almost every question starts with get_outage_status, so we run it up front and hand the
    model its result: one model call instead of two, which matters on a CPU-only laptop."""
    status = run_tool("get_outage_status", {}, ctx)
    tool_outputs.append(status)
    steps.append({"tool": "get_outage_status", "label": TOOL_LABELS["get_outage_status"], "input": {}, "result": status})
    conv.messages += [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_outage_status", "arguments": {}}}]},
        {"role": "tool", "tool_name": "get_outage_status", "content": json.dumps(status, ensure_ascii=False)},
    ]
    for _ in range(MAX_TOOL_ROUNDS + 1):
        msg = _ollama_chat(conv.messages)
        calls = msg.get("tool_calls") or []
        conv.messages.append({"role": "assistant", "content": msg.get("content", ""),
                              **({"tool_calls": calls} if calls else {})})
        if not calls:
            return (msg.get("content") or "").strip()
        for call in calls:
            name, args = call["function"]["name"], call["function"].get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            try:
                out = run_tool(name, args, ctx)
                tool_outputs.append(out)
                steps.append({"tool": name, "label": TOOL_LABELS.get(name, name), "input": args, "result": out})
                content = json.dumps(out, ensure_ascii=False)
            except Exception as e:  # tell the model, don't crash the chat
                content = f"Error: {e}"
            conv.messages.append({"role": "tool", "tool_name": name, "content": content})
    raise RuntimeError("too many tool rounds")


def ask(conversation_id: str, user_text: str, ctx: ChatContext, client=None) -> dict:
    """Answer one customer message. Returns {text, source, steps, grounded}."""
    use_ollama = client is None and backend() == "ollama"
    conv = CONVERSATIONS.setdefault(("ollama:" if use_ollama else "") + conversation_id, Conversation())
    if len(conv.messages) > MAX_HISTORY_MESSAGES:
        conv.messages = []  # fresh start; never trim or edit earlier turns
    max_chars = 300 if ctx.channel == "sms" else 600
    turn_start = len(conv.messages)
    conv.messages.append({"role": "user", "content": user_text})
    steps, tool_outputs = [], []
    try:
        if use_ollama:
            text = _ask_ollama(conv, ctx, steps, tool_outputs)
            return _finish(text, user_text, ctx, steps, tool_outputs, "ai")
        client = client or _client()
        for _ in range(MAX_TOOL_ROUNDS + 1):
            resp = client.beta.messages.create(
                model=MODEL,
                max_tokens=4000,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=SYSTEM.format(max_chars=max_chars),
                tools=TOOLS,
                messages=conv.messages,
            )
            if resp.stop_reason == "refusal":
                raise RuntimeError("refusal")
            conv.messages.append({"role": "assistant", "content": resp.content})
            calls = [b for b in resp.content if getattr(b, "type", "") == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                break
            results = []
            for b in calls:
                try:
                    out = run_tool(b.name, dict(b.input or {}), ctx)
                    tool_outputs.append(out)
                    steps.append({"tool": b.name, "label": TOOL_LABELS.get(b.name, b.name), "input": dict(b.input or {}), "result": out})
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out, ensure_ascii=False)})
                except Exception as e:  # tell Claude, don't crash the chat
                    results.append({"type": "tool_result", "tool_use_id": b.id, "content": f"Error: {e}", "is_error": True})
            conv.messages.append({"role": "user", "content": results})
        else:
            raise RuntimeError("too many tool rounds")
        text = _text(resp.content)
    except Exception as e:
        audit("ai_fallback", ctx.who, {"reason": type(e).__name__, "backend": "ollama" if use_ollama else "claude"})
        del conv.messages[turn_start:]  # this turn never completed; drop it whole
        return {"text": None, "source": "fallback", "steps": steps, "grounded": None, "error": type(e).__name__}
    return _finish(text, user_text, ctx, steps, tool_outputs,
                   "simulated" if os.environ.get("CONCIERGE_FAKE_CLAUDE") else "ai")


def _finish(text: str, user_text: str, ctx: ChatContext, steps: list, tool_outputs: list, source: str) -> dict:
    """Grounding check shared by both backends: every number must come from a tool or the customer."""
    evidence = {"tools": tool_outputs, "customer_said": user_text}
    grounded = bool(text) and numbers_grounded(text, evidence)
    audit("ai_answer", ctx.who, {"grounded": grounded, "tools": [s["tool"] for s in steps], "chars": len(text)})
    if not grounded:
        bad = sorted(numbers_in(text) - numbers_in(json.dumps(evidence, ensure_ascii=False)) - {"911", "35"})
        return {"text": None, "source": "rejected", "steps": steps, "grounded": False, "ungrounded_numbers": bad}
    return {"text": text, "source": source, "steps": steps, "grounded": True}


def answer(conversation_id: str, user_text: str, ctx: ChatContext, client=None) -> dict:
    """AI first; if it's off, fails, or isn't grounded, a safe template answer."""
    if available() or client is not None:
        r = ask(conversation_id, user_text, ctx, client)
        if r["text"]:
            return r
    else:
        r = {"source": "keyword", "steps": [], "grounded": None}
    card = service.make_card(_status_kind(ctx), ctx.customer, ctx.world,
                             extra={"confirmed": ["Your power is out. Crews are working on it."]} if _status_kind(ctx) == "status" else None)
    from .narrate import template
    text = template(card, emoji=False)
    if r["source"] in ("rejected", "fallback"):
        text = "I couldn't check that reliably, so here's what I know for sure. " + text
    return {**r, "text": text + " Reply HELP to reach a person."}

