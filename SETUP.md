# Built for the Dark: AI Outage Concierge

Hack/OHIO 2026 · AEP Challenge. The concept is in [README.md](README.md); the updated plan is in [PLAN.md](PLAN.md).

An outage assistant that works over plain SMS, voice and a web chat. A deterministic engine owns every number; an AI concierge (Claude) answers free-form questions by calling that engine as tools.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m concierge.mockdata      # (re)build the fake dataset in data/mock/
.venv/bin/python scripts/demo.py            # play the 3-minute demo story
.venv/bin/python scripts/sms_sim.py         # text the bot from your terminal (you are Maria)
.venv/bin/python -m pytest                  # 30 tests
.venv/bin/uvicorn concierge.api:app --port 8000  # web view at http://localhost:8000
```

## Turning on the AI concierge

Set a Claude API key before starting the server:

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # never commit this
.venv/bin/uvicorn concierge.api:app --port 8000
```

The phone panel's badge then says **AI on**. Without a key it says **Keyword mode** and everything still works with fixed answers. For offline testing, `CONCIERGE_FAKE_CLAUDE=1` uses a stand-in that follows the same tool-calling steps (badge: **Simulated AI**).

How it stays safe (`concierge/agent.py`):
- Danger words ("wire down", "smell gas"...) never reach the AI: a fixed safety message goes out instead.
- The AI's tools are locked to the customer's own account and return privacy-filtered fact cards.
- Every number in an AI answer must appear in a tool result or the customer's own message; otherwise the answer is thrown away and a template answer is sent. The chat shows "How I answered" with each check it ran.
- Over SMS, one-word commands (WHY, PLAN...) and short questions use the fast keyword path; longer free-form texts go to the AI.

Optional: `CONCIERGE_USE_LLM=1` also lets Claude reword the fixed alert texts (same number check).

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
| `concierge/agent.py` | AI concierge: Claude + engine tools, grounding check, fallbacks | AI + rules |
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
