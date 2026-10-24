# Built for the Dark: AI Outage Concierge

Hack/OHIO 2026 · AEP Challenge. The full concept is in [PLAN.md](PLAN.md).

An outage assistant that works over plain SMS and voice, with a deterministic engine that owns every number and an optional AI layer that only rewrites the wording.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m concierge.mockdata      # (re)build the fake dataset in data/mock/
.venv/bin/python scripts/demo.py            # play the 3-minute demo story
.venv/bin/python scripts/sms_sim.py         # text the bot from your terminal (you are Maria)
.venv/bin/python -m pytest                  # 24 tests
.venv/bin/uvicorn concierge.api:app --reload  # API at http://127.0.0.1:8000/docs
```

Optional AI wording: set `CONCIERGE_USE_LLM=1` and `ANTHROPIC_API_KEY`. Without them, templates are used and everything still works. AI text is thrown away if it contains any number that isn't in the fact card, runs over the SMS budget, or reads above grade ~8.

## Layout

| File | What it does | Owner |
|---|---|---|
| `concierge/mockdata.py` | Fake data in the challenge's shapes (circuits, devices, topology, ARCOS damage, labor hours, multipliers, priority sites, personas) | Data |
| `concierge/data.py` | Loads tables, builds the topology graph. **Swap in real extracts here** (same tables, `CONCIERGE_DATA_DIR`) | Data |
| `concierge/risk.py` | Risk band + confidence + top factors + similar storms; fallback below 3 past storms | Models |
| `concierge/restoration.py` | Monte Carlo restoration: labor × multipliers × access, crews, upstream-first, step restoration → P10/P50/P90 + drivers + "why it changed" | Models |
| `concierge/backtest.py` | Honesty score: learn calibration on older storms, score on newer ones | Models |
| `concierge/replay.py` | Replays a past storm hour by hour with "here's why" updates | Models |
| `concierge/rules.py` | Alert eligibility, timing, channel, escalation rules R1–R7 | AI + rules |
| `concierge/safety.py` | Danger keywords (no AI in the loop) + guardrails | AI + rules |
| `concierge/factcard.py` | Privacy firewall: key whitelist, ID/phone/coordinate blocker, audit log | AI + rules |
| `concierge/narrate.py` | Templates, SMS segment counter, reading level, optional Claude rewrite with checks | AI + rules |
| `concierge/prep.py` | Prep actions + power-budget timeline (declared needs only) | AI + rules |
| `concierge/service.py` | Demo storm clock, what-ifs, fact cards, proactive alerts | Channels |
| `concierge/sms.py` | Inbound SMS: safety gate → linking → keywords → intent | Channels |
| `concierge/api.py` | FastAPI: engines, demo controls, replay, backtest, Twilio SMS + voice webhooks | Channels |

## Demo controls

- `POST /api/demo/reset`, `POST /api/demo/prestorm_alerts`
- `POST /api/demo/advance {"to_h": 7}`: move the storm clock (hours after it hits)
- `POST /api/demo/what_if {"crews": 1, "extra_poles": 1, "access": 1.5}`: judges' sliders
- `GET /api/replay/E07?premise_id=P57423`: the full replay timeline for the web view's uncertainty cone
- `GET /api/backtest`: the honesty scorecard

Demo personas (from `data/mock/customers.json`): Maria (CPAP, 8 h battery, Circuit 14), Dev (brand-new circuit → fallback), Sam (insulin fridge, not opted in). A judge texting `DEMO` becomes a copy of Maria.

## Twilio

Point the number's Messaging webhook to `POST /sms/twilio` and Voice to `POST /voice/twilio` (use ngrok for local). Set `TWILIO_AUTH_TOKEN` (and `PUBLIC_BASE_URL` behind a proxy) to turn on signature checks. Not done yet: outbound sends for proactive alerts and the check-in call; `service.proactive()` already decides *what* to send.

## Known limits (fake data)

- The numbers come from made-up data, so the risk model and honesty score demonstrate the method, not real accuracy.
- Calibration fixes coverage (67% → 81% on held-out storms) but doubles range width (~12 h → ~22 h). Late in long outages P90 gets very wide; calibrating per "hours into the outage" would tighten it.
- Danger keywords are regex: good for a demo, but should be checked against real customer texts.
