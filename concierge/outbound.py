"""Outbound SMS. Real sends need TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM;
without them (or for a non-phone id like "web") nothing leaves the machine and the
message is only recorded as a dry run, so the demo and tests are safe.
"""
from __future__ import annotations

import base64
import os
import re
import urllib.error
import urllib.parse
import urllib.request

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def configured() -> bool:
    return all(os.environ.get(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))


def send(phone: str, text: str) -> str:
    """Returns "sent", "dry_run" or "failed"."""
    if not (configured() and E164.match(phone)):
        return "dry_run"
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=urllib.parse.urlencode({"To": phone, "From": os.environ["TWILIO_FROM"], "Body": text}).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=10):
            return "sent"
    except (urllib.error.URLError, TimeoutError):
        return "failed"
