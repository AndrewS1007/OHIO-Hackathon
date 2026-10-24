# Built for the Dark: an AI Outage Concierge

**Hack/OHIO 2026 · AEP Challenge: AI Outage Concierge**

> Most outage tools stop working at the moment you need them: the power is out, the Wi-Fi is down, and your phone is at 30%.
> We built an outage assistant that works **in the dark**, over SMS and voice, with a battery-aware, offline-first design.

**Run it:** see [SETUP.md](SETUP.md) (demo script, SMS simulator, web view at http://localhost:8000). The plan with our latest additions (safety gate, guardrails, storm replay, honesty score) is in [PLAN.md](PLAN.md).

---

## The challenge (summary)

A major storm is approaching. Build an AI assistant that answers the questions customers actually ask:

- "Will I lose power?"
- "How long could restoration take?"
- "What should I do to prepare?"
- "Do I qualify for outage alerts?"

Requirements from the brief:

1. **Outage risk prediction:** risk band + confidence + top contributing factors for an anonymized premise, circuit, or location.
2. **Restoration-time estimate:** a *range*, not a promise, based on damage type and quantity, upstream dependencies, step restoration, labor hours, travel, and nonproductive time. It updates when inputs change.
3. **Personalized preparation:** safety and practical guidance, adapted to *declared* (never inferred) medical, accessibility, or critical-facility needs.
4. **Alerts and proactive communications:** eligibility, timing, channel, escalation triggers, and messages for pre-storm, outage confirmed, estimate updated, and restored.
5. **Explainability and responsible use:** plain-language reasons, disclosed confidence and assumptions, no exposure of sensitive data, and a clear fallback when we can't predict reliably.

---

## Our concept

**The insight:** the customer's experience of an outage is defined by its constraints: no power, no home internet, a dying phone, and often an elderly or medically vulnerable person in the house. We design for those constraints first.

### Core features

| Feature | What it does | Brief requirement |
|---|---|---|
| **SMS and voice first** | Text "when's my power back?" and get a single-SMS answer with a range, confidence, and one next step. Call in and talk to it. Works on any phone. | 1, 2, 5 |
| **Keyword replies** | `WHY` → explanation · `WORSE` → safety steps · `HELP` → a human · `PLAN` → your storm plan | 3, 5 |
| **Battery-aware mode** | Reply `battery 20%` and we cut updates to the essentials (crew assigned, restored) and add phone-saving tips. | 4 |
| **Offline storm plan** | Sent the night before as a page that caches itself, plus a printable one-pager for the fridge: a personal timeline, safety steps, and the nearest warming or cooling center. | 3 |
| **Power-budget timeline** | Customers *declare* what matters (fridge with insulin, CPAP, sump pump, freezer), and we line it up against the restoration range. *"Hour 4: fridge window ends. Move insulin to the cooler."* | 3 |
| **Proactive check-in calls** | If the restoration range runs past a declared medical device's battery life, we call. No answer → escalate to a human. | 4 |
| **"Here's why" updates** | Every estimate change explains its cause: *"About 4h later: crews found a second damaged pole upstream of you."* | 4, 5 |
| **Three-lane messages** | Every message separates ✅ Confirmed by AEP · 🔮 Our estimate · 👉 Your action. | 5 |
| **Web view (pre-storm)** | Optional map + "power family tree" (substation → recloser → fuse → you) + uncertainty cone + what-if sliders. | 1, 2, 5 |

Example message (one SMS segment, grade-6 reading level):

```
✅ Outage confirmed. 🔮 Back ~6–11pm (med. confidence, pole damage upstream). 👉 Keep fridge closed. Reply WHY for details.
```

---

## Architecture

```
            ┌──────────────── Data (sanitized challenge extracts) ────────────────┐
            │ ARCOS damage · Circuit GIS · Upline topology · STORMS labor hours   │
            │ Indirect labor multipliers · Priority locations · Structure diagrams│
            └──────────────────────────────┬──────────────────────────────────────┘
                                           ▼
                     ┌────────────── Deterministic engine ───────────────┐
                     │ Risk model (risk band + top factors)              │
                     │ Topology graph (upstream dependencies, step       │
                     │   restoration)                                    │
                     │ Monte Carlo restoration (labor × travel/NPT ×     │
                     │   sequencing × crews) → P10 / P50 / P90           │
                     │ Similar-storm retrieval (past events on circuit)  │
                     │ Alert and escalation rules                        │
                     └──────────────────────┬────────────────────────────┘
                                            ▼
                         ┌──── Privacy firewall: "fact card" JSON ────┐
                         │ whitelisted fields only · H3 hex or circuit│
                         │ level · no raw rows, coords, crew, or      │
                         │ facility locations · audit log             │
                         └──────────────────────┬─────────────────────┘
                                                ▼
                              ┌──── LLM (narration only) ────┐
                              │ fact card → plain language   │
                              │ never computes numbers       │
                              │ length + reading-level checks│
                              └──────────────┬───────────────┘
                     ┌───────────────────────┼───────────────────────┐
                     ▼                       ▼                       ▼
                SMS (Twilio)          Voice (Twilio)         Web view + offline plan
```

**Key design rule:** the engine owns every number and the LLM owns only the wording. The LLM never sees raw data, so it can't hallucinate a time or leak a sensitive field.

### How we use each data source

| Data | Use |
|---|---|
| ARCOS damage assessment | Risk features, historical restoration durations, similar-storm retrieval, backtesting |
| Circuit GIS | Map view, proximity features, circuit context |
| Upline flow / topology | Dependency graph: "what has to be fixed before you", step restoration |
| STORMS labor hours | Base repair effort per damage type (Monte Carlo inputs) |
| Indirect labor assumptions | Travel and nonproductive-time multipliers |
| Priority locations | Sequencing ("a critical facility on your circuit is prioritized"), never exact locations |
| Structure diagrams | Plain-language repair explanations ("a pole takes longer than a wire, here's why") |

### Engine details

**Risk engine** (ARCOS + circuit GIS)
- Per circuit: past damage frequency, asset types, and the number of upstream protective devices, combined with storm severity.
- A simple score to start, then a logistic regression or LightGBM model.
- Output: a **Low / Elevated / High / Severe** band, a confidence level, and the top 3 factors.
- Too little history → "Not enough data, here's general guidance" (the required fallback).

**Restoration engine** (topology + STORMS + indirect labor)
- Trace the premise upstream through the topology TXT with `networkx`.
- For each damaged item on that path: `hours = labor hours × quantity × travel/NPT multiplier ÷ crews`.
- Step restoration: the upstream device gets fixed first, so you may come back before the whole circuit does.
- Monte Carlo (~1,000 runs, randomizing damage and crew delays) → a P10–P90 range plus the biggest driver ("3 broken poles upstream").

**Rules engine** (written-down if/then logic we show to judges)
- Alert eligibility, timing (e.g. 24 hrs out for High or above), and channel (SMS, email, call for declared medical needs).
- Escalation triggers, e.g. a medical-needs customer whose estimate runs past their declared battery life or past 8 hrs.

The LLM calls the engines as tools: `get_risk`, `estimate_restoration`, `get_alert_plan`, `draft_message`.

### Tech stack

- **Backend:** Python, FastAPI, geopandas (shapefiles), networkx (topology), scikit-learn or LightGBM
- **Messaging:** Twilio (SMS + voice)
- **Web view:** React with MapLibre or Leaflet, or Streamlit if the team is mostly Python
- **AI:** Claude API with tool use

---

## Responsible AI

- **Uncertainty:** restoration is always a range (P10–P90), and risk is a band with a confidence level.
- **Calibration scorecard:** we backtest on historical ARCOS events and publish how often our ranges contained the actual restoration time.
- **Fallback:** below a confidence threshold, we say so plainly ("damage assessment isn't complete in your area yet") and promise a notification.
- **Privacy:** aggregation to the circuit or H3 hex level, a field whitelist, and critical facilities mentioned only as "on your circuit."
- **No inference:** medical and accessibility needs are opt-in declarations only.
- **Safety first:** downed-line or emergency messages always lead with *"Stay 35 ft away. Call 911."*
- **Audit trail:** every fact card and generated message is logged.

---

## MVP scope (what we're building this weekend)

- [ ] **Data prep:** load the extracts, build the topology graph, assemble the labor-hour tables
- [ ] **Risk model:** risk band + top 3 factors + similar past storms
- [ ] **Restoration engine:** Monte Carlo → P10/P50/P90, with inputs for crews, access, and damage type
- [ ] **Fact card + privacy firewall** (schema, whitelist, audit log)
- [ ] **LLM narration** with SMS length and reading-level checks
- [ ] **SMS flow** (Twilio): ask, `WHY`, `WORSE`, `HELP`, `PLAN`, battery mode
- [ ] **Proactive alerts:** pre-storm, outage confirmed, estimate updated ("here's why"), restored
- [ ] **Check-in call** for a declared-medical persona
- [ ] **Offline storm plan** (cacheable page + printable PDF)
- [ ] **Web view:** family tree, uncertainty cone, 2–3 what-if sliders
- [ ] **Calibration scorecard** from the backtest

**Stretch:** multilingual messages, safe customer damage reporting.

**Tip:** the brief says the data package "may vary," so start with a small fake dataset in the same format. That way the messaging, web view, and AI work can begin before the real data arrives.

### Team split (4 people)

- **Data:** load the shapefiles and spreadsheets, build the circuit-level feature table and topology graph
- **Models:** risk engine + restoration / Monte Carlo engine + calibration backtest
- **Channels:** Twilio SMS/voice flows, offline storm plan, web view (map, family tree, cone, sliders)
- **AI + rules:** tool calling, fact card and privacy filter, alert rules, message generation, demo script

---

## Demo script (~3 min)

1. **Setup:** "Maria, a CPAP user on Circuit 14, the night before a storm." She gets a pre-storm text and her offline storm plan.
2. **Live:** a judge texts our number from their own phone and gets a personal risk and restoration answer, then replies `WHY`.
3. **Kill the Wi-Fi:** the outage hits. Updates keep arriving by SMS. Maria replies `battery 20%` and the assistant switches to low-power mode.
4. **Estimate changes:** a judge drags a slider (another pole found upstream). Maria gets a "here's why" update, her range now runs past her CPAP battery life, and **the check-in call rings**.
5. **Close:** restored message → calibration scorecard → *"We designed for the moment the power goes out, not for the moment before."*

---

## Directions we considered

- **"StormSense," a map + chat with what-if sliders:** our first plan. Solid but expected; its storm card, sliders, and engines now live on in our web view and engine layer.
- **"Neighborhood, not household":** opt-in mutual aid matching (generators, phone charging) without sharing addresses. Compelling but hard to build credibly in a weekend.
- **"Restoration as a live story":** narrating crew progress step by step. Depends on live operational status we'd have to simulate.
