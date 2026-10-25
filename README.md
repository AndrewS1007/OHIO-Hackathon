# Built for the Dark

**An AI outage concierge that keeps working after the power goes out.**

Built at Hack/OHIO 2026 for the AEP *AI Outage Concierge* challenge.

Most outage tools stop working right when people need them. The power is out, the Wi-Fi is down, and the phone is at 30%. Built for the Dark answers the questions customers actually ask before and during a storm: *Will I lose power? When will it be back? What should I do? Will I get alerts?* It works over plain text messages, voice and a lightweight web page, and it's honest about how sure it is.

Utility staff get an operations console. Any correction they make there, like sending more crews, confirming a restoration time or posting a warming-center notice, reaches every affected customer within seconds.

---

## Features

### For customers

- **Outage risk before the storm:** a risk band (Low / Elevated / High / Severe) with a confidence level and the top reasons behind it.
- **Restoration time as a range, not a promise:** for example, "plan for 13–80 hours." The range comes from a simulation of the actual repairs between the customer and the substation, and it updates whenever conditions change.
- **"Here's why" updates:** every change to an estimate comes with its cause, such as "crews found a second damaged pole upstream of you."
- **A personal storm plan:** customers can *declare* what matters to them, like a CPAP machine, an insulin fridge or a sump pump. The plan lines those up against the restoration range: "Hour 4: fridge no longer safe for insulin. Hour 8: CPAP battery runs out." Medical needs are never inferred.
- **Works over text:** customers can ask in their own words, or reply `WHY`, `PLAN`, `WORSE` or `HELP`. Replying `battery 20%` cuts updates down to the essentials.
- **Safety first:** messages about downed wires, sparks or gas skip the AI entirely. The customer gets a fixed, approved reply ("Stay 35 feet away. Call 911.") and staff are alerted.

### For utility staff

- **Storm Operations console:** see how many customers are out, which repairs are open and which circuits are affected. A *Needs a person* queue lists customers who should get a call, such as someone whose outage may outlast their medical device's battery.
- **Live corrections:** set crew counts, log damage, mark repairs done, confirm restoration times and post notices. Each change updates the customer site, the assistant and outgoing SMS alerts right away, and every change can be undone.
- **Storm replay:** step through a real past storm (February 2025) hour by hour.

---

## How it works

```
 AEP data (damage records, circuit GIS, upstream topology, labor hours)
                               │
                               ▼
 ┌──────────────────────── Deterministic engines ────────────────────────┐
 │ Risk model          → risk band, confidence, top factors              │
 │ Topology graph      → what has to be fixed before power reaches you   │
 │ Monte Carlo restore → P10 / P50 / P90 range and its biggest drivers   │
 │ Alert rules         → who gets told what, when, and on which channel  │
 └───────────────────────────────┬───────────────────────────────────────┘
                                 ▼
          Privacy firewall: a "fact card" with whitelisted fields only
            (no device IDs, addresses, coordinates or crew details)
                                 │
                                 ▼
          AI assistant: turns fact cards into plain language
          (every number it says must come from a fact card)
                                 │
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
   SMS (Twilio or         Voice (Twilio)          Web: customer site
   an Android phone)                              and ops console
```

**The key rule:** the engines own every number, and the AI owns only the wording. If the AI's reply contains a number that isn't in the data it was given, the reply is discarded and a fixed template answer goes out instead. With no AI model available, the whole system still works using keyword replies.

**Restoration estimates:** the engine traces a customer's line upstream through the circuit topology. For each damaged item on that path, it combines standard labor hours, travel and nonproductive-time multipliers, crew counts and the typical wait for a crew in past storms. It repeats this across about 1,000 simulated runs and reports the range. Upstream equipment is repaired first, so a customer can get power back before the whole circuit does.

---

## Results

Every model was scored *leave-one-storm-out*: each of the 11 storms in the data (Oct 2023 – Dec 2025) was predicted by a model that never saw it. The full report is in [reports/EVALUATION.md](reports/EVALUATION.md).

| Question | Result |
|---|---|
| Is the restoration range right? | The actual restoration time fell inside our range **81%** of the time (target 80%, 488 outages). A baseline using past storms' spread scored 46%. |
| Is it too optimistic? | Customers waited longer than our latest time only **5%** of the time. Our first model did so 75% of the time. |
| Does it leak private data? | **0 ID leaks** across 78 assistant tool calls. |
| Does it handle danger? | 87% of danger messages got the 911 reply with no AI involved, and no normal questions were refused. |
| Can people read it? | Median reading grade of the status texts: 2.7. |

**What we didn't solve:** the ranges are wide (median about 5 days), because repairs often wait days for a crew and the records give dates, not times. When a crew confirms a time, customers see that confirmed time instead. Risk prediction is weak: an AUC of 0.59 within a storm, where 0.5 is a coin flip. Almost all the damage in the data comes from a single storm.

---

## Responsible AI

- **Honest uncertainty:** restoration is always a range, and risk always comes with a confidence level.
- **A fallback when we can't predict:** circuits with too little history get "Not enough history to predict this reliably" plus general guidance.
- **Privacy:** customers and the AI see only a filtered summary of that one customer's own data. Critical facilities are described only as "on your circuit."
- **No inference:** medical and accessibility needs are opt-in. The life-support flag in AEP's data is deliberately never read.
- **Audit trail:** every fact card and generated message is logged.

---

## Quick start

Requires Python 3.12.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn concierge.api:app --port 8000
```

Then open:
- **http://localhost:8000**: the customer site. Sign in as one of the demo customers (Maria, Dev or Sam).
- **http://localhost:8000/ops**: the Storm Operations console.

This runs on a small synthetic dataset included in the repo, with keyword replies. [SETUP.md](SETUP.md) covers the real AEP data, turning on the AI assistant, connecting real SMS, running tests and the evaluation, and every configuration option.

---

## Demo walkthrough (~3 minutes)

To prepare, build the real data (see [SETUP.md](SETUP.md#use-the-real-aep-data)) and start the server. Open the console at `/ops` on the left and click **Reset demo**. Open the customer site at `/` on the right and sign in as **Maria**.

| Time | Do | What it shows |
|---|---|---|
| 0:00 | Customer window | Maria uses a CPAP machine with an 8-hour battery, which she declared herself. It's the night before a 75-mph storm. Her risk is **Elevated** (medium confidence), and if she loses power, she should plan for **13–80 hours**. Her plan says the fridge is unsafe for insulin at hour 4 and her CPAP battery runs out at hour 8. |
| 0:45 | Console: drag the storm clock to **hour 31** | The real February 2025 storm, replayed: **13,817** customers out, **490** open repairs, **46** circuits. Maria shows up under *Needs a person*, because her outage could outlast her battery. |
| 1:00 | Customer window | Maria sees when her outage started, the estimate, and why: a broken pole on the main line, crews working through 77 other repairs, and past repairs like this waiting about 58 hours. |
| 1:15 | Console: on **CIR2**, set crews from 7 to 10 and click **Update crews**. Then confirm **Tue 6pm** and post the notice *"Warming center open at the Main St library until 10pm."* | Within seconds, Maria's page reads **Power back by Tue 6pm, confirmed by our crews**, with the warming center at the top. |
| 1:50 | Customer assistant: click **When will my power be back?** | The assistant answers from the same live data and mentions the warming center. |
| 2:10 | Type **"there's a wire sparking in my yard"** | A fixed safety message comes back with no AI involved, and a safety report appears in the console. |
| 2:25 | Console: point at **Estimate accuracy** | 81% of real restoration times fell inside the range on storms the model never saw. |
| 2:45 | Console: drag the clock to **hour 66** | The records show Maria's power came back on Wednesday at 8am, and her page says **Power restored**. |

**More to try:** set CIR2 to 1 crew to watch Maria's estimate move out by days, then click **Undo** under *Activity*. Sign in as **Dev**, whose circuit has no storm history, to see the honest fallback. **Sam** has an insulin fridge but isn't signed up for alerts.

These numbers come from the real AEP data and were last checked on October 24, 2026. They change if the data or models change.

---

## Repository layout

| Path | Contents |
|---|---|
| `concierge/` | The application: engines, rules, privacy firewall, AI assistant, SMS handling and the FastAPI server |
| `web/` | The customer site (`index.html`) and the operations console (`ops.html`) |
| `data/mock/` | Synthetic data in the same format as the AEP extracts |
| `reports/` | Evaluation results (`EVALUATION.md` plus JSON and CSV files) |
| `scripts/` | A terminal demo and an SMS simulator |
| `tests/` | Test suite (pytest) |
| `Modelfile` | Definition of an optional local chatbot model for Ollama |
| `PLAN.md` | Our design notes and findings from the real data |

---

## Acknowledgments

Built in a weekend at Hack/OHIO 2026. AEP (American Electric Power) provided the challenge and sanitized data extracts. All customer profiles are synthetic.
