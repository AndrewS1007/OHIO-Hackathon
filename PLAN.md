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

`python -m concierge.realdata` turns the challenge package into these tables (`data/real/`). The engines read them by default; the mock dataset is still used by the unit tests.

| Data (real file) | Becomes | Use |
|---|---|---|
| ARCOS damage (`arcos_prod_damage_09_28_Clip`, 1,659 records) | `arcos_damage.csv`, `storms.csv` | Risk labels, restoration durations, similar storms, **storm replay, backtesting** |
| Customers (`Customer_Clip`, 117,642) | `premises.csv` | Premise → circuit + one of 4 coarse map areas per circuit. Nothing else is read |
| Topology (`matched_upstream.txt`, 117,599 chains) | `devices.csv`, `topology.txt` | Dependency graph: "what has to be fixed before you", step restoration |
| Primary lines (`Primary_ExportFeatures`) | `circuits.csv` | Circuit length and overhead share (risk features) |
| Transformers, fuses, reclosers | (used during conversion) | Coordinates to place each damage report on the network |
| Labor workbook (`Install_Labor_HackOHIO.xlsx`) | `labor_hours.csv`, `labor_basis.csv`, `indirect_labor.csv` | Person-hours per repair recipe × indirect labor multiplier (4.5) |
| Hospitals, fire stations, EMS | `priority_locations.csv` | Sequencing ("a critical facility on your circuit is restored first"); only device + circuit kept |
| Structure diagrams (PDF) | not used yet | Plain-language repair explanations, and checking our repair recipes |

## Engine details

**Risk engine** (ARCOS + circuit GIS)
- Per circuit: past damage frequency, asset types, and overhead exposure, combined with storm severity (wind, ice).
- Logistic regression on (circuit, past storm) pairs: "did this circuit get damaged?"
- Output: Low / Elevated / High / Severe, a confidence level, and the top 3 factors.
- Too little history → "Not enough data, here's general guidance" (the required fallback).

**Restoration engine** (topology + STORMS + indirect labor)
- Trace the premise upstream through the topology with networkx.
- Each damage job: hours = (labor hours × quantity + access adders) × indirect multipliers × access factor. With real data: workbook person-hours × 4.5 ILM ÷ a 4-person crew, plus the workbook's access / backyard / remote-site adders when ARCOS flags them.
- **Delays learned from past storms** (`concierge/delays.py`): when damage gets reported (by storm size; big storms keep reporting damage for days) and how long a repair waits for a crew after its report (pooled across storms). A storm is never used to learn its own delays.
- Not-yet-reported damage is nowcast: if X% of a storm's damage is usually reported by now, the reports so far imply the total. Fewer or more crews than usual stretch or shrink the learned waits.
- You're back when every job on *your* path is done (step restoration).
- Monte Carlo (~1,000 runs) → P10 / P50 / P90 + the biggest driver. Scenarios built without learned delays (unit tests) still use the crew-queue model: crews work jobs upstream-first, critical-facility circuits first.

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

- Backend: Python, FastAPI, networkx, numpy/pandas, scikit-learn; geopandas/pyogrio + openpyxl for the data conversion
- Messaging: Twilio (SMS + voice)
- Web view: React with MapLibre or Leaflet, or Streamlit if the team is mostly Python
- AI narration: local Ollama `qwen2.5:3b` (`narrate.py`), with a template fallback when it's off or fails a check
- AI chatbot: tool use in `agent.py`, on local Ollama by default with Claude as an option; `Modelfile` builds the `outage-concierge` Ollama model (on `qwen2.5:3b`) with the same rules for running it locally

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
- [x] **NEW** Swap fake data for the real extracts (`concierge/realdata.py` → `data/real/`)
- [x] **NEW** Simulation ~30× faster (vectorized Monte Carlo) so estimates take ~1 s on real circuits
- [x] **NEW** Ollama `Modelfile` for the chatbot (`outage-concierge`)
- [x] **NEW** Tool test suite (`tests/test_tools.py`) and evaluation report (`python -m concierge.evaluate` → `reports/EVALUATION.md`)
- [x] **NEW** Restoration learns report delays and repair waits from past storms (too optimistic: 75% → 5%)
- [x] Decided (Oct 24): keep the calibrated ranges in the app (wider, but 0% too optimistic on the test storms)
- [ ] Rewrite demo beats 4 and the slider around the real Feb 2025 storm
- [ ] Wire `agent.py` to Ollama, or keep Claude for the chatbot (see latency below)
- [ ] Add real weather for past storms (NOAA) instead of assumed wind

Stretch: multilingual messages, safe customer damage reporting.

## Real data: what we found (Oct 24)

**What's in the package.** Appalachian Power (APCO), around Roanoke: 110 circuits, 31 substations, 117,595 premises with a full upline path, 41 critical facilities near our lines. ARCOS has 1,659 damage records from Oct 2022 to Aug 2026.

**What's missing, and the assumption we made** (all listed in `data/real/conversion_report.json`):

| Gap | Assumption |
|---|---|
| No storm/event IDs (every record says "Default") | Reports more than 3 days apart start a new event. Events with fewer than 10 records are everyday damage, not storms. That gives **11 storms**, dominated by **Feb 2025 (1,254 records)** and **Sep 2024 (194)** |
| Dates only, no times | Reports at 06:00, repairs at 18:00 on the logged date. So restoration times are only accurate to about half a day |
| Open records; repairs logged weeks later | Dropped: 56 open, 245 repaired more than 7 days after the report (follow-up work, not storm restoration). **1,290 records used** |
| No weather | `max_wind_mph` is **assumed** from each storm's damage volume (35–75 mph). The risk model's "winds" factor is really "storm size" until we add NOAA data |
| No crew counts | Inferred per storm and circuit from throughput: crew-hours of work ÷ hours the repairs took |
| Damage points aren't linked to devices | Placed at the nearest device on the same circuit (median 38 m). Wire, pole and switch damage goes to the protective device above the nearest transformer, since that's what trips |
| Workbook has hours per part, not per repair | Repair "recipes" built from parts, e.g. a pole = pole + crossarm + 3 insulators + 3 pins + guy + anchor + ground ≈ 6.3 person-hours ≈ 7 crew-hours. See `labor_basis.csv` |

**Privacy.** The customer layer has names, addresses, road names, coordinates and a life-support flag. The converter reads only the premise number, circuit and service point, and uses coordinates only to place damage and to build four coarse areas per circuit. **The life-support flag is never read**: medical needs stay declared-only. A test checks that none of these fields reach the tables.

**First results on real data.**
- Maria (synthetic CPAP persona on a real premise, circuit CIR2): High risk (53%, medium confidence). In the Feb 2025 replay her power is out at hour 30 and back at hour 66, with two "here's why" updates in between.
- Dev lands on a circuit with no damage history, so he gets the fallback, as designed.
- **Honesty score, first version: bad.** Raw ranges contained the real time 19% of the time and were too optimistic 75% of the time, because the model assumed damage is found within hours and repairs take crew-hours. In the real data, big-storm damage keeps being reported for days (median 54 h after the start) and a repair waits a median ~1.5 days.
- **After learning those delays from past storms** (full numbers in `reports/EVALUATION.md`, every storm scored by a model that never saw it):

  | | Inside range | Too optimistic | Too pessimistic | Median error | Median width |
  |---|---|---|---|---|---|
  | Old model | 18% | 75% | 7% | 42 h | 10 h |
  | **Learned delays (raw)** | **54%** | **5%** | 41% | 48 h | 116 h |
  | + app's calibration | 66% | 0% | 34% | 163 h | 275 h |
  | No-model baseline | 46% | 4% | 50% | 48 h | 48 h |

  We now rarely promise too early, but we lean pessimistic and the ranges are wide. Large storms have only 2–3 examples to learn from, and the timestamps are dates, not times. The app still applies calibration because it passes the "at most doubles the width" rule on the older storms. On Feb 2025 it doesn't: Maria's first range becomes 71–292 h.
- **Risk model:** within a storm, ranking areas by risk scores AUC 0.59 from line history and design alone (0.65 with storm size). Only Feb 2025 has enough damage to score reliably. A leak in the history feature (it used later storms) is fixed.
- **Speed:** estimate ~1 s, full replay ~16 s, risk model training ~19 s, backtest ~100 s (both cached at startup). The backtest should be cached to disk before the demo.
- **Local chatbot (`outage-concierge`, first test on this laptop, CPU only, 8 cores, other jobs running):**
  - It called the status tool for "when is my power back?" and then answered using only the fact card's times and range. Good.
  - It refused a hospital-location question. Good, but it also printed a raw tool call as text, which our grounding check must catch.
  - It made up tool arguments (a fake customer ID). That's harmless, because tools are bound to the customer server-side.
  - For "a power line is sparking in my driveway" it called a tool instead of saying 911. Our safety gate answers this before any AI sees it, which shows why the gate has to stay first.
  - **28–80 s per reply**, far over Twilio's ~15 s. To use it live: a GPU machine, a smaller model (e.g. `llama3.2:3b`), or keep Claude for the chatbot and use Ollama only as the offline backup.

## Team split (4 people)

- **Data:** real extracts are converted (`concierge/realdata.py`); next: NOAA weather per storm, check repair recipes against the structure diagrams
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
