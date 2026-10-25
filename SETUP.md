# Built for the Dark: AI Outage Concierge

Hack/OHIO 2026 · AEP Challenge. The concept is in [README.md](README.md); the updated plan is in [PLAN.md](PLAN.md).

An outage assistant that works over plain SMS, voice and a web chat. A deterministic engine owns every number; an AI concierge (Claude) answers free-form questions by calling that engine as tools.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m concierge.mockdata      # (re)build the fake dataset in data/mock/
.venv/bin/python -m concierge.realdata      # build data/real/ from the AEP package (see below); used by default once built
.venv/bin/python scripts/demo.py            # play the 3-minute demo story
.venv/bin/python scripts/sms_sim.py         # text the bot from your terminal (you are Maria)
.venv/bin/python -m pytest                  # 30 tests
.venv/bin/uvicorn concierge.api:app --port 8000  # customer site at /, operations console at /ops
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

## Two web pages

- **Customer site** (`/`): sign in to a demo account, see your outage status front and center, and chat with the outage assistant.
- **Operations console** (`/ops`): staff see customers out, open repairs and who needs a person, and make fixes: set crews on a circuit, log damage, mark repairs done, confirm a restoration time, post notices. Every fix is saved (`concierge/ops.py`, SQLite) and applied before any estimate, so it reaches the customer site, the assistant and SMS alerts right away. Every fix can be undone. The storm clock at the top drives the replay.

See [DEMO.md](DEMO.md) for the presentation walkthrough.

## Demo controls

- `POST /api/demo/reset`, `POST /api/demo/prestorm_alerts`
- `POST /api/demo/advance {"to_h": 7}`: move the storm clock (hours after it hits)
- `POST /api/demo/what_if {"crews": 1, "extra_poles": 1, "access": 1.5}`: judges' sliders
- `GET /api/replay/E07?premise_id=P57423`: the full replay timeline for the web view's uncertainty cone
- `GET /api/backtest`: the honesty scorecard

Demo personas (from `data/mock/customers.json`): Maria (CPAP, 8 h battery, Circuit 14), Dev (brand-new circuit → fallback), Sam (insulin fridge, not opted in). A judge texting `DEMO` becomes a copy of Maria.

## Twilio

Point the number's Messaging webhook to `POST /sms/twilio` and Voice to `POST /voice/twilio` (use ngrok for local). Set `TWILIO_AUTH_TOKEN` (and `PUBLIC_BASE_URL` behind a proxy) to turn on signature checks. Not done yet: outbound sends for proactive alerts and the check-in call; `service.proactive()` already decides *what* to send.

## Real data

Put the challenge package at `aep_challenge_hack26-main/` (it isn't committed), extract the archives as its `data/README.md` says, then convert:

```bash
cd aep_challenge_hack26-main && mkdir -p data/extracted/Data1 data/extracted/Data2 data/extracted/matched_upstream
unzip -n data/raw/Data1.zip -d data/extracted/Data1 && unzip -n data/raw/Data2.zip -d data/extracted/Data2
unzip -n data/raw/matched_upstream.zip -d data/extracted/matched_upstream && cd ..
.venv/bin/python -m concierge.realdata      # ~45 s; writes data/real/ + conversion_report.json
```

Once `data/real/` exists, the app uses it. Set `CONCIERGE_DATA_DIR=data/mock` to go back to the fake data (the unit tests always use it). Every assumption the converter makes is listed in `data/real/conversion_report.json` and in [PLAN.md](PLAN.md#real-data-what-we-found-oct-24).

## Evaluation (numbers for the presentation)

```bash
.venv/bin/python -m concierge.evaluate      # ~5 min on real data
```

Writes `reports/EVALUATION.md` (headline numbers and tables), `reports/evaluation.json` (every number) and CSVs for charts. Every model number is scored leave-one-storm-out: each storm is predicted by a model that never saw it. It covers risk accuracy (AUC, Brier skill, reliability by band), restoration ranges (coverage, too optimistic/pessimistic, error, width, against a no-model baseline and several calibrations), privacy (ID leaks across every chatbot tool), SMS length and reading level, the safety gate, and speed.

## Local chatbot model (Ollama)

```bash
ollama pull qwen2.5:3b
ollama create outage-concierge -f Modelfile
ollama run outage-concierge "can you check my neighbor's power?"
```

The `Modelfile` holds the chatbot's rules (same as `SYSTEM` in `concierge/agent.py`), a low temperature, and a few no-number examples of refusals and safety answers.

## Known limits (fake data)

- The numbers come from made-up data, so the risk model and honesty score demonstrate the method, not real accuracy.
- Calibration fixes coverage (67% → 81% on held-out storms) but doubles range width (~12 h → ~22 h). Late in long outages P90 gets very wide; calibrating per "hours into the outage" would tighten it.
- Danger keywords are regex: good for a demo, but should be checked against real customer texts.

## Real SMS through an Android phone

The phone's own number becomes the concierge's number, using the free [SMS Gateway for Android](https://sms-gate.app) app in **cloud mode**. The phone needs signal, not shared Wi-Fi.

1. Install the app, turn on **Cloud server**, and tap **Online**. Note the username and password on the Home tab.
2. Expose the server (keep this running; a quick tunnel's URL changes on restart):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Register the webhook once (replace the credentials and tunnel URL):
   ```bash
   curl -X POST -u USER:PASS -H "Content-Type: application/json" -d '{"url": "https://<tunnel-url>/sms/gateway", "event": "sms:received"}' https://api.sms-gate.app/3rdparty/v1/webhooks
   ```
4. Start the server with the credentials. `SMSGATE_SIGNING_KEY` (app: Settings → Webhooks → Signing Key) makes the server reject webhooks that aren't from your phone:
   ```bash
   SMSGATE_USER=USER SMSGATE_PASSWORD=PASS SMSGATE_SIGNING_KEY=KEY .venv/bin/uvicorn concierge.api:app --port 8000
   ```
5. From another phone, text `DEMO` to the Android phone's number, then `WHEN`, `WHY`, `PLAN`. Proactive alerts from `/api/demo/prestorm_alerts` and `/api/demo/advance` are now texted for real.

Replies are sent in the background, so slow AI answers are fine (the app only waits 30 s for the webhook). The sample customers' 555 numbers are never texted. Local-server mode works too: set `SMSGATE_URL=http://<phone-ip>:8080`, but the app then needs HTTPS for webhooks to anything other than the phone itself, so cloud mode is simpler.
