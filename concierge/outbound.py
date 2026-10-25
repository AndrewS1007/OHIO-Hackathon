"""Outbound SMS, through one of two providers:

  gateway  an Android phone running SMS Gateway for Android (sms-gate.app): the phone's own
           number sends the texts. Needs SMSGATE_USER and SMSGATE_PASSWORD; SMSGATE_URL
           defaults to the app's cloud API (for local-server mode use http://<phone-ip>:8080).
  twilio   needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM.

With neither configured (or for a non-phone id like "web", or a fictional 555 demo number)
nothing leaves the machine and the message is only recorded as a dry run, so the demo and
tests are safe.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

E164 = re.compile(r"^\+[1-9]\d{7,14}$")
FICTIONAL = re.compile(r"^\+1555")  # the sample customers' numbers; never text them
SMSGATE_CLOUD = "https://api.sms-gate.app/3rdparty/v1"


def provider() -> str | None:
    if os.environ.get("SMSGATE_USER") and os.environ.get("SMSGATE_PASSWORD"):
        return "gateway"
    if all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")):
        return "twilio"
    return None


def configured() -> bool:
    return provider() is not None


def _basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _gateway_request(phone: str, text: str) -> urllib.request.Request:
    base = os.environ.get("SMSGATE_URL", SMSGATE_CLOUD).rstrip("/")
    # Cloud/private servers take /messages; the phone's own local server takes /message.
    url = base + ("/messages" if "/3rdparty/" in base else "/message")
    return urllib.request.Request(
        url, method="POST",
        data=json.dumps({"textMessage": {"text": text}, "phoneNumbers": [phone]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": _basic(os.environ["SMSGATE_USER"], os.environ["SMSGATE_PASSWORD"])})


def _twilio_request(phone: str, text: str) -> urllib.request.Request:
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    return urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=urllib.parse.urlencode({"To": phone, "From": os.environ["TWILIO_FROM"], "Body": text}).encode(),
        headers={"Authorization": _basic(sid, token)})


def send(phone: str, text: str) -> str:
    """Returns "sent", "dry_run" or "failed"."""
    p = provider()
    if p is None or not E164.match(phone) or FICTIONAL.match(phone):
        return "dry_run"
    req = _gateway_request(phone, text) if p == "gateway" else _twilio_request(phone, text)
    try:
        with urllib.request.urlopen(req, timeout=15):
            return "sent"
    except (urllib.error.URLError, TimeoutError):
        return "failed"
