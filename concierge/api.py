"""HTTP API.  Run:  uvicorn concierge.api:app --reload

Web view / judges:  /api/premises/{id}/..., /api/demo/..., /api/replay/..., /api/backtest
Twilio webhooks:    POST /sms/twilio (Messaging), POST /voice/twilio (Voice)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from . import backtest, replay, rules, service, sms, views
from .config import ROOT
from .data import get_dataset
from .factcard import recent_audit
from .risk import predict

app = FastAPI(title="Built for the Dark: AI Outage Concierge")


@app.on_event("startup")
def warm_up():
    """Run the backtest and risk map once at startup so the first page load is fast."""
    backtest.run()
    views.risk_map_with_outages()


def _customer(premise_id: str) -> dict:
    ds = get_dataset()
    if ds.path(premise_id) is None:
        raise HTTPException(404, "Unknown premise")
    known = next((c for c in ds.customers if c["premise_id"] == premise_id), None)
    return known or {"phone": "web", "premise_id": premise_id, "alerts_opt_in": False, "declared_needs": []}


@app.get("/")
def web_view():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api")
def index():
    ds = get_dataset()
    return {"service": "AI Outage Concierge", "phase": service.WORLD.phase, "storm": ds.current_storm,
            "demo_premises": {c["name"]: c["premise_id"] for c in ds.customers}}


# ---------- engines ----------

@app.get("/api/premises/{premise_id}/risk")
def get_risk(premise_id: str):
    _customer(premise_id)
    return predict(premise_id)


@app.get("/api/premises/{premise_id}/restoration")
def get_restoration(premise_id: str, crews: int | None = None, access: float | None = None, extra_poles: int = 0):
    c = _customer(premise_id)
    world = service.World(service.WORLD.phase, service.WORLD.t_h, dict(service.WORLD.what_if))
    if crews:
        world.what_if["crews"] = crews
    if access:
        world.what_if["access"] = access
    if extra_poles:
        world.what_if["extra_poles"] = extra_poles
    est, _ = service.restoration_for(c, world)
    return {"phase": world.phase, "t_h": world.t_h, "what_if": world.what_if, **est}


@app.get("/api/premises/{premise_id}/alerts")
def get_alerts(premise_id: str):
    c = _customer(premise_id)
    est, _ = service.restoration_for(c)
    return rules.alert_plan(c, predict(premise_id), est)


@app.get("/api/premises/{premise_id}/card/{kind}")
def get_card(premise_id: str, kind: str):
    """Fact card + rendered message for any message kind (pre_storm, status, why, plan, alerts, worse...)."""
    world = service.WORLD if kind not in ("pre_storm",) else service.World()
    return service.message(kind, _customer(premise_id), world=world)


@app.get("/api/rules")
def get_rules():
    return [{"id": i, "rule": r} for i, r in rules.RULES]


@app.get("/api/backtest")
def get_backtest():
    return backtest.run()


@app.get("/api/replay/{event_id}")
def get_replay(event_id: str, premise_id: str, crews: int | None = None, extra_poles: int = 0):
    c = _customer(premise_id)
    what_if = {k: v for k, v in {"crews": crews, "extra_poles": extra_poles}.items() if v}
    tl = replay.timeline(premise_id, event_id, customer=c, what_if=what_if or None, calib=backtest.calibration())
    for e in tl["events"]:
        e.pop("estimate", None)
    return tl


@app.get("/api/audit")
def get_audit(n: int = 50):
    return recent_audit(n)


# ---------- web view ----------

@app.get("/api/customers")
def get_customers():
    ds = get_dataset()
    return [{"name": c["name"], "premise_id": c["premise_id"], "declared_needs": c["declared_needs"]} for c in ds.customers]


@app.get("/api/map")
def get_map():
    return views.risk_map_with_outages()


@app.get("/api/premises/{premise_id}/tree")
def get_tree(premise_id: str):
    _customer(premise_id)
    return views.family_tree(premise_id)


@app.get("/api/view/replay")
def get_view_replay(premise_id: str, crews: int | None = None, extra_poles: int = 0, access: float = 1.0):
    c = _customer(premise_id)
    what_if = {k: v for k, v in {"crews": crews, "extra_poles": extra_poles, "access": access if access != 1.0 else None}.items() if v}
    return views.replay_with_texts(premise_id, c, what_if or None)


@app.get("/api/backtest/cases")
def get_backtest_cases(calibrated: bool = True):
    return backtest.test_cases(calibrated)


# ---------- demo controls (storm clock + what-ifs) ----------

class Advance(BaseModel):
    to_h: float


class WhatIf(BaseModel):
    crews: int | None = None
    access: float | None = None
    extra_poles: int | None = None


@app.get("/api/demo/state")
def demo_state():
    w = service.WORLD
    return {"phase": w.phase, "t_h": w.t_h, "what_if": w.what_if, "sessions": list(service.SESSIONS)}


@app.post("/api/demo/reset")
def demo_reset():
    service.reset_world()
    return demo_state()


@app.post("/api/demo/prestorm_alerts")
def demo_prestorm():
    return {p: service.proactive(s) for p, s in service.SESSIONS.items()}


@app.post("/api/demo/advance")
def demo_advance(a: Advance):
    return service.advance(a.to_h)


@app.post("/api/demo/what_if")
def demo_what_if(w: WhatIf):
    service.WORLD.what_if.update({k: v for k, v in w.model_dump().items() if v is not None})
    return {p: service.proactive(s) for p, s in service.SESSIONS.items()}


# ---------- SMS / voice ----------

class SimSMS(BaseModel):
    phone: str
    body: str


@app.post("/api/sms/simulate")
def sms_simulate(m: SimSMS):
    return sms.handle(m.phone, m.body)


def _twilio_ok(request: Request, form: dict) -> bool:
    """Validate X-Twilio-Signature when TWILIO_AUTH_TOKEN is set (skip in local dev)."""
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    if not token:
        return True
    url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") + request.url.path if os.environ.get("PUBLIC_BASE_URL") else str(request.url)
    data = url + "".join(k + form[k] for k in sorted(form))
    expected = base64.b64encode(hmac.new(token.encode(), data.encode(), hashlib.sha1).digest()).decode()
    return hmac.compare_digest(expected, request.headers.get("X-Twilio-Signature", ""))


@app.post("/sms/twilio")
async def sms_twilio(request: Request):
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not _twilio_ok(request, form):
        raise HTTPException(403, "Bad signature")
    r = sms.handle(form.get("From", ""), form.get("Body", ""))
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(r["text"])}</Message></Response>'
    return Response(xml, media_type="application/xml")


@app.post("/voice/twilio")
async def voice_twilio(request: Request):
    """Caller hears their status read aloud (any phone, no data needed)."""
    form = {k: str(v) for k, v in (await request.form()).items()}
    if not _twilio_ok(request, form):
        raise HTTPException(403, "Bad signature")
    r = sms.handle(form.get("From", ""), "when is my power back")
    text = r["text"].replace("[OK]", "").replace("[EST]", "Our estimate:").replace("[DO]", "What to do:")
    text += " If you see a downed wire, stay 35 feet away and call 9 1 1."
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Say>{escape(text)}</Say></Response>'
    return Response(xml, media_type="application/xml")
