# Built for the Dark: an AI Outage Concierge

Hack/OHIO 2026 · AEP Challenge: AI Outage Concierge

Most outage tools stop working at the moment you need them: the power is out, the Wi-Fi is down, and your phone is at 30%. We built an outage assistant that works in the dark, over SMS and voice, with a battery-aware, offline-first design.

## The challenge (summary)

A major storm is approaching. Build an AI assistant that answers the questions customers actually ask:

- "Will I lose power?"
- "How long could restoration take?"
- "What should I do to prepare?"
- "Do I qualify for outage alerts?"

Requirements from the brief:

1. **Outage risk prediction**: risk band + confidence + top contributing factors for an anonymized premise, circuit, or location.
2. **Restoration-time estimate**: a range, not a promise, based on damage type and quantity, upstream dependencies, step restoration, labor hours, travel, and nonproductive time. It updates when inputs change.
3. **Personalized preparation**: safety and practical guidance, adapted to declared (never inferred) medical, accessibility, or critical-facility needs.
4. **Alerts and proactive communications**: eligibility, timing, channel, escalation triggers, and messages for pre-storm, outage confirmed, estimate updated, and restored.
5. **Explainability and responsible use**: plain-language reasons, disclosed confidence and assumptions, no exposure of sensitive data, and a clear fallback when we can't predict reliably.

## Our concept

The insight: the customer's experience of an outage is defined by its constraints: no power, no home internet, a dying phone, and often an elderly or medically vulnerable person in the house. We design for those constraints first.

### Core features

| Feature | What it does | Brief req. |
|---|---|---|
| SMS and voice first | Text "when's my power back?" and get a short answer with a range, confidence, and one next step. Call in and talk to it. Works on any phone. | 1, 2, 5 |
| Keyword replies | WHY → explanation · WORSE → safety steps · HELP → a human · PLAN → your storm plan | 3, 5 |
| Battery-aware mode | Reply `battery 20` and we cut updates to the essentials (crew assigned, restored) and add phone-saving tips. | 4 |
| Offline storm plan | Sent the night before as a page that caches itself, plus a printable one-pager for the fridge: a personal timeline, safety steps, and the nearest warming or cooling center. | 3 |
| Power-budget timeline | Customers declare what matters (fridge with insulin, CPAP, sump pump, freezer), and we line it up against the restoration range. "Hour 4: fridge window ends. Move insulin to the cooler." | 3 |
| Proactive check-in calls | If the restoration range runs past a declared medical device's battery life, we call. No answer → escalate to a human. | 4 |
| "Here's why" updates | Every estimate change explains its cause: "About 4h later: crews found a second damaged pole upstream of you." | 4, 5 |
| Three-lane messages | Every message separates ✅ Confirmed by AEP · 🔮 Our estimate · 👉 Your action. | 5 |
| Web view (pre-storm) | Optional map + "power family tree" (substation → recloser → fuse → you) + uncertainty cone + what-if sliders. | 1, 2, 5 |
| **NEW · Danger keyword escalation** | Texts like "wire down", "sparks", "smell gas", "generator fumes" skip the AI entirely and get a fixed, pre-approved safety message + a human escalation, instantly. | 3, 4, 5 |
| **NEW · Guardrail layer** | Requests for other customers' info, exact facility locations, crew names/locations, or "ignore your instructions" get a polite, safe refusal with a useful alternative. | 5 |
| **NEW · Storm replay** | Replay a real past storm from the ARCOS data hour by hour. Damage reports appear in the order they were logged, and the customer's estimate (and texts) update live, each with a "here's why". | 2, 4, 5 |
| **NEW · Honesty score** | Backtest on past storms: "our ranges contained the real restoration time X% of the time". Shown to judges as a scorecard, and to customers in WHY ("on your circuit, our ranges were right 8 of 10 times"). | 5 |

Example message (grade-6 reading level):

```
✅ Outage confirmed. 🔮 Back ~6–11pm (med. confidence, pole damage upstream). 👉 Keep fridge closed. Reply WHY for details.
```

> SMS length note: any emoji switches a text to UCS-2 encoding, which drops a segment from 160 to 70 characters. The message above is 2 segments with emoji, 1 without. We support both: a plain-text mode (`[OK]` / `[EST]` / `[DO]`) that fits one segment, and an emoji mode for richer phones.

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
                     │ NEW: storm replay · backtest (honesty score)      │
                     └──────────────────────┬────────────────────────────┘
                                            ▼
 inbound text ──► NEW: Safety gate ──(danger)──► fixed safety message + human escalation
                  (danger keywords,            (AI is never in this path)
                   guardrails)
                        │ (safe)
                        ▼
                         ┌──── Privacy firewall: "fact card" JSON ────┐
                         │ whitelisted fields only · circuit level    │
                         │ no raw rows, coords, crew, or facility     │
                         │ locations · audit log                      │
                         └──────────────────────┬─────────────────────┘
                                                ▼
                              ┌──── LLM (narration only) ────┐
                              │ fact card → plain language   │
                              │ never computes numbers       │
                              │ number check: every number   │
                              │  must appear in fact card    │
                              │ length + reading-level checks│
                              │ fails → template fallback    │
                              └──────────────┬───────────────┘
                     ┌───────────────────────┼───────────────────────┐
                     ▼                       ▼                       ▼
                SMS (Twilio)          Voice (Twilio)         Web view + offline plan
```

**Key design rule:** the engine owns every number and the LLM owns only the wording. The LLM never sees raw data, so it can't hallucinate a time or leak a sensitive field. We enforce it: any number in the LLM's text that isn't in the fact card makes us throw the text away and use a template.

## How we use each data source

| Data | Use |
|---|---|
| ARCOS damage assessment | Risk features, historical restoration durations, similar-storm retrieval, **storm replay, backtesting** |
| Circuit GIS | Map view, proximity features, circuit context |
| Upline flow / topology | Dependency graph: "what has to be fixed before you", step restoration |
| STORMS labor hours | Base repair effort per damage type (Monte Carlo inputs) |
| Indirect labor assumptions | Travel and nonproductive-time multipliers |
| Priority locations | Sequencing ("a critical facility on your circuit is prioritized"), never exact locations |
| Structure diagrams | Plain-language repair explanations ("a pole takes longer than a wire, here's why") |

## Engine details

**Risk engine** (ARCOS + circuit GIS)
- Per circuit: past damage frequency, asset types, and overhead exposure, combined with storm severity (wind, ice).
- Logistic regression on (circuit, past storm) pairs: "did this circuit get damaged?"
- Output: Low / Elevated / High / Severe, a confidence level, and the top 3 factors.
- Too little history → "Not enough data, here's general guidance" (the required fallback).

**Restoration engine** (topology + STORMS + indirect labor)
- Trace the premise upstream through the topology with networkx.
- Each damage job: hours = labor hours × quantity × travel/NPT multiplier × access factor.
- Crews work jobs upstream-first (critical-facility circuits first); you're back when every job on *your* path is done (step restoration).
- Monte Carlo (~1,000 runs, randomizing job time, assessment delay, and not-yet-found damage) → P10 / P50 / P90 + the biggest driver.

**Rules engine** (written-down if/then logic we show to judges)
- Alert eligibility, timing (e.g. 24 hrs out for High or above), and channel (SMS, voice call for declared medical needs).
- Escalation triggers, e.g. a medical-needs customer whose P90 runs past their declared battery life or past 8 hrs.

**NEW · Safety gate** (runs before anything else on every inbound text)
- Danger keywords (downed line, sparks, fire, smoke, gas smell, CO / generator fumes, shock, medical emergency) → fixed message that always leads with "Stay 35 ft away. Call 911." + human escalation logged. No LLM involved.
- Guardrails: other customers, facility addresses, crew/employee details, exact coordinates, prompt-injection attempts → safe refusal + what we *can* tell them.

**NEW · Storm replay**
- Pick a past storm. Step through ARCOS reports by report time; at each step re-run the restoration engine with only the damage known so far.
- Each estimate change produces a "here's why" (new damage found upstream, job finished, crews added).
- Powers the demo's "estimate changes" moment with real data instead of a hand-made slider.

**NEW · Backtest / honesty score**
- For each past storm and circuit, estimate restoration from the damage it had, then compare to the actual repair completion time in ARCOS.
- Report coverage (how often the real time fell inside P10–P90; target ~80%) and median error, overall and per circuit.

The LLM calls the engines as tools: `get_risk`, `estimate_restoration`, `get_alert_plan`, `draft_message`.

## Tech stack

- Backend: Python, FastAPI, networkx, numpy/pandas, scikit-learn (geopandas once real shapefiles arrive)
- Messaging: Twilio (SMS + voice)
- Web view: React with MapLibre or Leaflet, or Streamlit if the team is mostly Python
- AI: Claude API (`claude-opus-5-5`), narration only, with a template fallback when no key is set

## Responsible AI

- **Uncertainty:** restoration is always a range (P10–P90), and risk is a band with a confidence level.
- **Honesty score:** published backtest coverage, overall and per circuit.
- **Fallback:** below a confidence threshold, we say so plainly ("damage assessment isn't complete in your area yet") and promise a notification.
- **Privacy:** circuit-level aggregation, a field whitelist, and critical facilities mentioned only as "on your circuit."
- **No inference:** medical and accessibility needs are opt-in declarations only.
- **Safety first:** danger keywords bypass the AI; emergency messages always lead with "Stay 35 ft away. Call 911."
- **Guardrails:** sensitive or manipulative requests get a safe refusal.
- **Audit trail:** every fact card, safety trigger, and generated message is logged.

## MVP scope (this weekend)

- [x] Fake dataset in the challenge's shapes (so everyone can start before real data arrives)
- [x] Topology graph + labor-hour tables
- [x] Risk model: band + top 3 factors + similar past storms
- [x] Restoration engine: Monte Carlo → P10/P50/P90, inputs for crews, access, extra damage
- [x] Fact card + privacy firewall (whitelist, audit log)
- [x] LLM narration with number check, SMS length and reading-level checks
- [x] **NEW** Safety gate: danger keywords + guardrails
- [x] **NEW** Storm replay with "here's why" updates
- [x] **NEW** Backtest / honesty score
- [x] SMS conversation logic: ask, WHY, WORSE, HELP, PLAN, battery mode (local simulator)
- [ ] Twilio wiring (webhook exists; needs account + phone number)
- [ ] Proactive alerts scheduler: pre-storm, outage confirmed, estimate updated, restored
- [ ] Check-in call for a declared-medical persona (Twilio voice)
- [ ] Offline storm plan (cacheable page + printable PDF)
- [ ] Web view: family tree, uncertainty cone, 2–3 what-if sliders, replay timeline
- [ ] Swap fake data for the real extracts

Stretch: multilingual messages, safe customer damage reporting.

## Team split (4 people)

- **Data:** load the real shapefiles and spreadsheets into the same tables as `data/mock/`, build the topology graph
- **Models:** risk engine, restoration / Monte Carlo, backtest + honesty score, storm replay
- **Channels:** Twilio SMS/voice flows, offline storm plan, web view (map, family tree, cone, sliders, replay timeline)
- **AI + rules:** narration prompt, fact card and privacy filter, safety gate + guardrails, alert rules, demo script

## Demo script (~3 min)

1. **Setup:** "Maria, a CPAP user on Circuit 14, the night before a storm." She gets a pre-storm text and her offline storm plan.
2. **Live:** a judge texts our number from their own phone and gets a personal risk and restoration answer, then replies WHY and sees the honesty score.
3. **Kill the Wi-Fi:** the outage hits. Updates keep arriving by SMS. Maria replies `battery 20` and the assistant switches to low-power mode.
4. **Storm replay:** we fast-forward a real past storm. A second pole is found upstream; Maria gets a "here's why" update, her range now runs past her CPAP battery life, and the check-in call rings.
5. **Safety:** a judge texts "there's a wire sparking in my yard" and gets the safety message instantly, with no AI in the loop. Then "where's the hospital's backup generator?" gets a polite refusal.
6. **Close:** restored message → calibration scorecard → "We designed for the moment the power goes out, not for the moment before."

## Directions we considered

- **"StormSense," a map + chat with what-if sliders:** our first plan. Solid but expected; its storm card, sliders, and engines now live on in our web view and engine layer.
- **"Neighborhood, not household":** opt-in mutual aid matching (generators, phone charging) without sharing addresses. Compelling but hard to build credibly in a weekend.
- **"Restoration as a live story":** narrating crew progress step by step. Depends on live operational status we'd have to simulate; storm replay gives us most of this with real data.
