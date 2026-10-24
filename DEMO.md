# Demo script: Built for the Dark (≈3 minutes)

Two windows side by side: the **Storm Operations console** (what utility staff use) on the left, and the **customer's Outage Center** on the right. Every fix made in the console shows up on the customer side within a few seconds: status, the assistant's answers, and SMS alerts.

It runs on the **real AEP data**, replaying the **Feb 2025 storm**. Every number below was checked against the running app on Oct 24. If the data or model changes, re-check the ones marked ✔.

## Before judges arrive (5 min)

1. In your terminal, in the project folder: `git pull`, then start the server:
   `.venv/bin/uvicorn concierge.api:app --port 8000`
   Wait for `Application startup complete` (about a minute the first time).
2. Open two browser windows side by side:
   - Left: **http://localhost:8000/ops** (operations console). Click **Reset demo**.
   - Right: **http://localhost:8000** (customer site). Sign in as **Maria**.
3. Check the assistant header on the customer site says **AI assistant** (the chatbot model is reachable). If it doesn't, the assistant still answers with fixed replies.
4. Browser zoom ~110%. Close other tabs. Turn off notifications. Have the backup video ready.

## The script

| Time | Do | Say |
|---|---|---|
| 0:00 | Nothing yet. | "Most outage tools stop working the moment you need them, and they're guessing. We built an outage assistant that's honest about what it knows, and a console that lets the utility fix what it gets wrong, live." |
| 0:15 | Customer window. | "This is Maria's account. She uses a CPAP machine with an 8-hour battery. **She told us that.** AEP's data has a life-support flag, and we deliberately never read it." |
| 0:25 | Point at the big status card. | "It's the night before a 75-mph storm. Her risk is **Elevated**, medium confidence, because of the winds and a long line. If she loses power, plan for **13 to 80 hours**. ✔ Below that: what to do tonight, and her plan: hour 4 the fridge stops being safe for insulin, hour 8 her CPAP battery runs out." |
| 0:45 | Console: drag the storm clock to **hour 31** (Mon 9pm). | "This is the real Feb 2025 storm, replayed hour by hour. On the console: **13,817** customers out, **490** open repairs, **46** circuits. ✔ And under *Needs a person*: Maria. Her outage could outlast her battery, so she's flagged for a check-in call." ✔ |
| 1:00 | Customer window. | "Maria sees: power out since 8pm, our best estimate, and *why*: a broken pole on the main line, crews fixing 77 other repairs, and repairs like this waited about 58 hours in past storms." ✔ |
| 1:15 | Console: click **CIR2** (Maria's circuit). Set crews **7 → 10**, click **Update crews**. | "Ops sends three more crews to her circuit. Watch her estimate." (Customer side moves earlier within a few seconds.) ✔ |
| 1:30 | Console: pick **Tue 6pm**, click **Confirm restoration time**. Then post a notice to all customers: *"Warming center open at the Main St library until 10pm."* | "Now a crew lead confirms a time, and we open a warming center." |
| 1:40 | Customer window. | "Maria now sees **Power back by Tue 6pm, confirmed by our crews**, with the model's range underneath, and the warming center at the top. Nothing on her side was edited by hand." ✔ |
| 1:50 | Customer assistant: click **When will my power be back?** | "The assistant answers from the same live data: *Our crews confirm power back by Tuesday at 6pm*, and it mentions the warming center. It never makes up a number: every number it says is checked against our engines, and anything it can't back up is thrown away." ✔ |
| 2:10 | Type into the assistant: **"there's a wire sparking in my yard"** | "Safety words skip the AI entirely: a fixed, approved message, stay 35 feet away, call 911. And on the console, a **Safety report** lands in *Needs a person*." ✔ |
| 2:25 | Console: point at **Estimate accuracy 81%**. | "Are the estimates any good? We scored every past storm with a model that never saw it. Our first model was right **18%** of the time and too optimistic. Repairs wait a day and a half for a crew, so we learned that wait from past storms. Now the real time lands inside our range **81%** of the time, close to our 80% target, and we say 'too soon' only **5%** of the time." ✔ |
| 2:45 | Console: drag the clock to **hour 66** (Wed 8am). | "Wednesday 8am, the records say her power came back. Her page says **Power restored**." ✔ |
| 2:55 | Back to both windows. | "One system: honest estimates for customers, and a console where every fix reaches them right away." |

## If a judge asks to try something

- **Fewer crews:** on CIR2, set crews to **1** and update. Maria's estimate moves out by days. Click **Undo** in *Activity* to put it back.
- **Log new damage:** pick a device and damage type, click **Log damage**. Customers fed by it stay out until someone clicks **Mark repaired**.
- **Other customers:** sign in as **Dev**. He's on a circuit with no storm history, so he gets the honest fallback ("not enough history to predict this reliably"). ✔ **Sam** has an insulin fridge and isn't signed up for alerts.
- **Undo anything:** every console action has an **Undo** in *Activity*.

## Numbers cheat sheet ✔

| | |
|---|---|
| Data | 110 circuits · 117,595 homes · 1,290 damage records used · 11 storms (Oct 2023 – Dec 2025) |
| Maria | Elevated risk, medium confidence · 13–80 h if out · out Mon 8pm → back Wed 8am (36 h) · circuit CIR2, 7 crews recorded |
| Storm hour 31 | 13,817 customers out · 490 open repairs · 46 circuits |
| Honesty (each storm left out, 488 outages) | first model 18% → now **81%** inside the range · too optimistic 75% → 5% · typical window ~5 days |
| Feb 2025 storm alone | 82% (448 outages) |
| Known miss | Jan 2024: 0 of 6 |
| Typical wait for a crew | small storms ~58 h, big storm ~33 h, repair work itself ~2 h |

## Likely judge questions

- **"Why are the ranges so wide?"** "Because that's the truth in this data. Repairs wait one to five days for a crew, and the records only have dates, not times. A narrow range would be wrong most of the time. Ours is right about 4 times in 5. And when a crew confirms a time, customers see that instead."
- **"How do you stop the AI from making things up?"** "It only words the answer. Every number comes from our engines, and if it says a number they didn't give it, we throw the answer away and send a fixed one. Safety messages never go through the AI."
- **"What about privacy?"** "The customer side and the AI only see a filtered summary of that one customer's own data: no device IDs, addresses, coordinates or crew details. Those stay in the staff console. Medical needs are opt-in only."
- **"What happens to SMS customers?"** "The same: when ops changes something, customers signed up for texts get an update automatically, with the reason."
- **"What would you do next?"** "Real weather per storm (wind is estimated from damage volume right now), live texting through Twilio, staff sign-in on the console, and AEP's full storm history to tighten the big-storm ranges."

## If something breaks

- **Page won't load:** in the terminal, press Control-C, then start the server again (command above).
- **Assistant is slow or errors:** skip to the safety-word message (instant, no AI), then move on.
- **The console state is messy:** click **Reset demo**, sign Maria in again, and start over.
- **Anything else:** switch to the backup video and keep talking over it.
