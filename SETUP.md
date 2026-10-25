# Setup

This guide covers installing and running Built for the Dark, turning on the AI assistant, connecting real SMS and voice, and running the tests and the evaluation. For what the project is and how it works, see [README.md](README.md).

- [Install](#install)
- [Run](#run)
- [Use the real AEP data](#use-the-real-aep-data)
- [Turn on the AI assistant](#turn-on-the-ai-assistant)
- [Connect SMS and voice](#connect-sms-and-voice)
- [Tests and evaluation](#tests-and-evaluation)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Code layout](#code-layout)

---

## Install

Requires Python 3.12.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/uvicorn concierge.api:app --port 8000
```

| URL | Page |
|---|---|
| http://localhost:8000 | Customer site: sign in as a demo customer to see outage status and chat with the assistant |
| http://localhost:8000/ops | Storm Operations console: the storm clock, live corrections, and the *Needs a person* queue |
| http://localhost:8000/api | Index of API endpoints |

The first start can take about a minute while data loads. Wait for `Application startup complete`.

### Without a browser

```bash
.venv/bin/python scripts/demo.py      # play the demo story in the terminal
.venv/bin/python scripts/sms_sim.py   # text the assistant from your terminal as Maria
```

The SMS simulator also accepts `/advance <hours>` to move the storm clock, `/whatif crews=1 extra_poles=1 access=1.5`, and `/reset`. To text from a new number, run `scripts/sms_sim.py +15559998888` and then text `DEMO` (you become a copy of Maria) or `LINK <premise>`.

### Demo customers

| Name | Situation |
|---|---|
| Maria | CPAP machine with an 8-hour battery; gets check-in escalations |
| Dev | On a circuit with no storm history, which shows the "not enough data" fallback |
| Sam | Has an insulin fridge but isn't signed up for alerts |

The demo customers' 555 phone numbers are never actually texted.

---

## Use the real AEP data

The app ships with synthetic data in `data/mock/`. To use the AEP challenge data, extract the package in `aep_challenge_hack26-main/` and convert it:

```bash
cd aep_challenge_hack26-main
mkdir -p data/extracted/Data1 data/extracted/Data2 data/extracted/matched_upstream
unzip -n data/raw/Data1.zip -d data/extracted/Data1
unzip -n data/raw/Data2.zip -d data/extracted/Data2
unzip -n data/raw/matched_upstream.zip -d data/extracted/matched_upstream
cd ..
.venv/bin/python -m concierge.realdata
```

Conversion takes about 45 seconds and writes `data/real/`. Every assumption the converter makes is listed in `data/real/conversion_report.json`.

Once `data/real/` exists, the app uses it automatically. To switch back to the synthetic data, set `CONCIERGE_DATA_DIR=data/mock`. The tests always use the synthetic data.

To regenerate the synthetic data:

```bash
.venv/bin/python -m concierge.mockdata
```

---

## Turn on the AI assistant

Without an AI model, the assistant answers with fixed keyword replies, and everything else still works. With a model, it answers free-form questions by calling the engines as tools.

The `CONCIERGE_AI` variable picks the backend:

| Value | Backend |
|---|---|
| `auto` (default) | Ollama if it's reachable and has the model, otherwise Claude if credentials are set, otherwise keyword mode |
| `claude` | The Anthropic API |
| `ollama` | A local or remote Ollama model |
| `0` | Keyword mode only |

### Claude

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # never commit this
.venv/bin/uvicorn concierge.api:app --port 8000
```

A profile from `ant auth login` also works. For offline testing, `CONCIERGE_FAKE_CLAUDE=1` uses a stand-in that follows the same tool-calling steps without any network calls.

### Ollama

The included `Modelfile` builds a small chatbot model from Qwen 2.5 that can run on a laptop without a GPU:

```bash
curl -fsSL https://ollama.com/install.sh | sh   # install Ollama (Linux/WSL)
ollama pull qwen2.5:3b
ollama create outage-concierge -f Modelfile
ollama run outage-concierge "can you check my neighbor's power?"   # quick check
```

The app connects to the Ollama server and model named by `OLLAMA_URL` and `OLLAMA_MODEL` in `concierge/agent.py`. These are constants, not environment variables, so edit them to point at your server (for example `http://localhost:11434`) and at the model name you created.

The `Modelfile` holds the same rules as `SYSTEM` in `concierge/agent.py`, which is the source of truth. Keep the two in sync.

### How the assistant stays safe

- Danger words ("wire down", "smell gas", and so on) never reach the AI. A fixed safety message is sent instead (`concierge/safety.py`).
- The AI's tools are locked to the customer's own account and return privacy-filtered fact cards (`concierge/factcard.py`).
- Every number in an AI reply must appear in a tool result or in the customer's own message. If one doesn't, the reply is discarded and a template answer is sent. The web chat shows each check under "How I answered."
- Over SMS, one-word commands and short questions take the fast keyword path, and longer free-form texts go to the AI.

Setting `CONCIERGE_USE_LLM=1` also lets the AI reword the fixed alert texts, with the same number check.

---

## Connect SMS and voice

### Twilio

1. Expose the server publicly, for example with `ngrok http 8000`.
2. In the Twilio console, point the number's Messaging webhook to `POST /sms/twilio` and its Voice webhook to `POST /voice/twilio`.
3. Set `TWILIO_AUTH_TOKEN` to turn on request signature checks. Behind a proxy, also set `PUBLIC_BASE_URL` to the public address.
4. To send proactive alerts by text, also set `TWILIO_ACCOUNT_SID` and `TWILIO_FROM` (the Twilio number). Without them, outgoing messages are only recorded as a dry run.

Check-in calls aren't placed automatically yet. Customers who need one appear in the console's *Needs a person* queue.

### An Android phone (SMS Gateway)

The free [SMS Gateway for Android](https://sms-gate.app) app turns an Android phone's own number into the assistant's number, and it sends proactive alerts for real. The phone needs cell signal, not shared Wi-Fi.

1. Install the app, turn on **Cloud server**, and tap **Online**. Note the username and password on the Home tab.
2. Expose the server and keep the tunnel running (a quick tunnel's URL changes each time it restarts):
   ```bash
   cloudflared tunnel --url http://localhost:8000
   ```
3. Register the webhook once, filling in your credentials and the tunnel URL:
   ```bash
   curl -X POST -u USER:PASS -H "Content-Type: application/json" \
     -d '{"url": "https://<tunnel-url>/sms/gateway", "event": "sms:received"}' \
     https://api.sms-gate.app/3rdparty/v1/webhooks
   ```
4. Start the server with the credentials. The signing key is under Settings → Webhooks → Signing Key in the app, and it makes the server reject webhooks that don't come from your phone:
   ```bash
   SMSGATE_USER=USER SMSGATE_PASSWORD=PASS SMSGATE_SIGNING_KEY=KEY \
     .venv/bin/uvicorn concierge.api:app --port 8000
   ```
5. From another phone, text `DEMO` to the Android phone's number, then try `WHEN`, `WHY` and `PLAN`.

Replies are sent in the background, so slow AI answers are fine. The gateway waits only 30 seconds for the webhook to respond.

The app's local-server mode works too: set `SMSGATE_URL=http://<phone-ip>:8080`. The app then requires HTTPS for webhooks to anything other than the phone itself, so cloud mode is simpler.

---

## Tests and evaluation

```bash
.venv/bin/python -m pytest               # about 200 tests, ~3 minutes
.venv/bin/python -m concierge.evaluate   # ~5 minutes on the real data
```

The evaluation writes `reports/EVALUATION.md` (headline numbers and tables), `reports/evaluation.json` (every number) and CSV files for charts. Every model number is scored leave-one-storm-out. It covers:
- risk accuracy (AUC, Brier skill, reliability by band)
- restoration ranges (coverage, too optimistic or pessimistic, error and width, compared against a no-model baseline and several calibrations)
- privacy (ID leaks across every assistant tool)
- SMS length and reading level
- the safety gate
- speed

---

## API reference

Customer-facing engines:

| Endpoint | Returns |
|---|---|
| `GET /api/premises/{id}/risk` | Risk band, confidence and top factors |
| `GET /api/premises/{id}/restoration` | P10/P50/P90 restoration range and its drivers |
| `GET /api/premises/{id}/alerts` | Alert eligibility, timing and channel |
| `GET /api/premises/{id}/tree` | Upstream equipment between the customer and the substation |
| `GET /api/backtest` | The estimate accuracy scorecard |
| `GET /api/replay/{event_id}?premise_id=...` | Hour-by-hour replay of a past storm for one customer |

Demo controls:

| Endpoint | Effect |
|---|---|
| `POST /api/demo/reset` | Reset the demo state |
| `POST /api/demo/prestorm_alerts` | Send the pre-storm alerts |
| `POST /api/demo/advance` with `{"to_h": 7}` | Move the storm clock to a number of hours after the storm hits |
| `POST /api/demo/what_if` with `{"crews": 1, "extra_poles": 1, "access": 1.5}` | Change crews, damage or road access |

The operations console uses the `/api/ops/...` endpoints. Every console action is stored in SQLite and applied before any estimate is computed, and each one can be undone with `POST /api/ops/actions/{id}/undo`. The full list is at `GET /api`.

---

## Configuration

| Variable | Purpose |
|---|---|
| `CONCIERGE_DATA_DIR` | Data folder. Defaults to `data/real/` if it exists, otherwise `data/mock/` |
| `CONCIERGE_DB` | Path to the SQLite database for console actions. Defaults to `logs/concierge.db` |
| `CONCIERGE_AI` | AI backend: `auto`, `claude`, `ollama` or `0` |
| `CONCIERGE_FAKE_CLAUDE` | Set to `1` to use the offline stand-in for Claude |
| `CONCIERGE_USE_LLM` | Set to `1` to let the AI reword alert texts |
| `CONCIERGE_LARGE_STORM_REPORTS` | Number of damage reports at which a storm counts as "large" (default 100) |
| `ANTHROPIC_API_KEY` | Claude API key |
| `TWILIO_AUTH_TOKEN` | Turns on Twilio signature checks; also needed for outbound texts |
| `TWILIO_ACCOUNT_SID`, `TWILIO_FROM` | Twilio account and sending number for outbound texts |
| `PUBLIC_BASE_URL` | Public URL of the server, used behind a proxy |
| `SMSGATE_USER`, `SMSGATE_PASSWORD` | SMS Gateway for Android credentials |
| `SMSGATE_SIGNING_KEY` | SMS Gateway webhook signing key |
| `SMSGATE_URL` | SMS Gateway address. Defaults to the cloud service |

Never commit API keys or tokens. `.env` is ignored by git.

---

## Code layout

| File | What it does |
|---|---|
| `concierge/api.py` | FastAPI server: pages, engines, demo controls, ops console, SMS and voice webhooks |
| `concierge/config.py` | Paths and data-folder selection |
| `concierge/data.py` | Loads the data tables and builds the topology graph |
| `concierge/mockdata.py` | Generates synthetic data in the AEP extracts' format |
| `concierge/realdata.py` | Converts the AEP package into the app's tables |
| `concierge/risk.py` | Risk band, confidence, top factors and similar past storms, with a fallback when history is thin |
| `concierge/restoration.py` | Monte Carlo restoration: P10/P50/P90, drivers and "why it changed" |
| `concierge/backtest.py` | Estimate accuracy scorecard |
| `concierge/evaluate.py` | Full leave-one-storm-out evaluation report |
| `concierge/replay.py` | Hour-by-hour replay of a past storm |
| `concierge/rules.py` | Alert eligibility, timing, channel and escalation rules |
| `concierge/safety.py` | Danger-word gate (no AI involved) and guardrails |
| `concierge/factcard.py` | Privacy firewall: field whitelist, ID/phone/coordinate blocker, audit log |
| `concierge/agent.py` | AI assistant: model backends, engine tools, number check and fallbacks |
| `concierge/narrate.py` | Message templates, SMS segment counter, reading-level check |
| `concierge/prep.py` | Preparation steps and the power-budget timeline (declared needs only) |
| `concierge/service.py` | Storm clock, what-ifs, fact cards and proactive alerts |
| `concierge/ops.py`, `concierge/store.py` | Operations console actions and their SQLite storage |
| `concierge/portal.py`, `concierge/views.py` | Data for the customer site and console views |
| `concierge/sms.py` | Inbound SMS: safety gate, account linking, keywords and intent |
| `concierge/webchat.py` | Web chat handling |
| `concierge/outbound.py` | Sending outbound SMS through the gateway |
