# Demo script: Built for the Dark (≈3 minutes)

Runs on the **real AEP data**, replaying the **Feb 2025 storm**. Every number below was checked against the running app on Oct 24. If the data or model changes, re-check the ones marked ✔.

## Before judges arrive (5 min)

1. In your terminal, in the project folder: `git pull`, then start the server:
   `.venv/bin/uvicorn concierge.api:app --port 8000`
   With a Claude key, start it with `export ANTHROPIC_API_KEY=...` first. Wait for `Application startup complete`.
2. Open http://localhost:8000 and check:
   - Top badge says **Real AEP data · replaying the Feb 2025 storm**.
   - Phone panel badge says **AI on** (with a Claude key) or **Keyword mode** (without). Know which one you have.
   - "Viewing as" is **Maria (CPAP)**, and the clock is on **Night before**.
3. Browser zoom ~110% so the back row can read it. Close other tabs. Turn off notifications.
4. Have the backup video open in another window (see "If something breaks").

Refresh the page to reset everything between runs.

## The script

| Time | Do | Say |
|---|---|---|
| 0:00 | Nothing yet. | "Most outage tools stop working the moment you need them: the power's out, the Wi-Fi's gone, the phone's at 30%. We built an outage assistant for **the dark**: plain text messages, honest estimates, and a person on the line when it matters." |
| 0:15 | Point at "Right now for Maria". | "This is Maria. She uses a CPAP machine with an 8-hour battery. **She told us that.** AEP's data has a life-support flag, and we deliberately never read it." |
| 0:25 | Point at the three boxes, then her first text in the phone. | "It's the night before a 75-mph storm. Her risk is **Elevated** (48%), medium confidence. Reasons: the winds and a long line. If she loses power: **13 to 80 hours**. ✔ Facts, estimates and actions are always kept separate, and the text fits in one SMS." |
| 0:45 | Click **Power goes out**. | "Monday 8pm: her power goes out. This is the real Feb 2025 storm, replayed hour by hour, using only what crews had reported at each moment." |
| 0:55 | Point at the phone's new text, then the 📞 notice under it, then the family tree. | "She gets one text: confirmed, a range, and one thing to do: switch the CPAP to battery. Her outage could run past her 8-hour battery, so the system **places a check-in call** and alerts our outreach team. That's the AI knowing when to hand off to a person. ✔ And here's *why* she's out: a broken pole on the main line." |
| 1:15 | Click **Estimate changes**. | "Tuesday 9am, crews fix that pole. Her estimate updates, and the text says **why**." ✔ |
| 1:25 | Drag the slider to about Tuesday 8pm (or play ▶ briefly). | "Tuesday 8pm, crews find a *second* problem on her street. She gets told that too. Over 36 hours she gets **four texts**, each with a reason. Not one every hour." ✔ |
| 1:40 | Click **Power back**. Point at the chart. | "Wednesday 8am, power's back. The shaded band is our window over time; the dashed line is when power really came back. Our very first text said Tuesday morning to Sunday. It landed **inside**." ✔ |
| 1:55 | Type into the phone: **"My CPAP lasts 8 hours. What should I do?"** | With **AI on**: "The concierge is Claude, but it never makes up a number. It calls our engines, and **every number it says is checked against them**. Here's the trace of what it checked." Open "How I answered". In **Keyword mode**: "It falls back to fixed answers when there's no AI connection, so it still works in a blackout." |
| 2:10 | Type: **"there's a wire sparking in my yard"** | "Safety words skip the AI entirely. A fixed, approved message: stay 35 feet away, call 911, and a person is alerted." |
| 2:20 | Scroll to **Honesty scorecard**. | "How do we know the estimates are any good? We scored every past storm with a model that never saw it. Our first model was right **18%** of the time and too optimistic, because it assumed crews start right away. The records showed repairs wait a day and a half for a crew. We learned that wait from past storms. Now it's right **81%** of the time, close to our 80% target." ✔ |
| 2:40 | Point at **Storm by storm**. | "And we show where we're still wrong: the January 2024 storm. Six outages, five-day waits. We didn't see that coming, and we say so." ✔ |
| 2:50 | Back to the top. | "Built for the dark: it works by text, tells the truth about what it doesn't know, and calls a person when it matters." |

## What-if, if a judge asks to try something

- Set the clock to **Power goes out**, then click crews **"1"**: Maria's likely restore moves about **3 days later**. ✔ Click **"As recorded"** to undo.
- The **extra broken poles** slider barely moves it on real data. Say why: "Repair work is short. Most of the time is waiting for a crew. That's what the records taught us."
- Click any block on the map to view the storm as that neighborhood. **Dev** (in "Viewing as") lives on a circuit with no storm history and gets the honest fallback: "We don't have enough history for your area to predict this reliably." ✔

## Numbers cheat sheet ✔

| | |
|---|---|
| Data | 110 circuits · 117,595 homes · 1,290 damage records used · 11 storms (Oct 2023 – Dec 2025) |
| Maria | Elevated risk (48%), medium confidence · 13–80 h if out · out Mon 8pm → back Wed 8am (36 h) · 4 texts |
| Honesty (leave-one-storm-out, 488 outages) | first model 18% → now **81%** inside the range · too optimistic 75% → 5% · typical window ~5 days |
| Feb 2025 storm alone | 82% (448 outages) |
| Known miss | Jan 2024: 0 of 6 (~5-day waits) |
| Typical wait for a crew | small storms ~58 h, big storm ~33 h, repair work itself ~2 h |

## Likely judge questions

- **"Why are the ranges so wide?"** "Because that's the truth in this data. Repairs wait one to five days for a crew, and the records only have dates, not times. A narrow range would be wrong most of the time. Ours is right about 4 times in 5."
- **"How do you stop the AI from making things up?"** "It only words the answer. Every number comes from our engines, and if it says a number they didn't give it, we throw the answer away and send a fixed one. Safety messages never go through the AI."
- **"What about privacy?"** "The AI only sees a filtered summary of this one customer's own data: no IDs, addresses, coordinates or crew details. Medical needs are opt-in only. Hospitals are only ever mentioned as 'a critical facility on your circuit'."
- **"Does this work without internet?"** "The customer side is plain SMS and voice, so any phone works with no data. The chat falls back to fixed answers if the AI can't be reached."
- **"What would you do next?"** "Real weather per storm (wind is estimated from damage volume right now), live texting through Twilio, and AEP's full storm history to tighten the big-storm ranges."
- **"Why CPAP?"** "It's our example of a declared need with a deadline: a battery. The same logic covers oxygen, insulin and sump pumps. Sam, our second customer, has an insulin fridge."

## If something breaks

- **Page won't load:** in the terminal, press Control-C, then start the server again (command above). Startup takes a minute the first time.
- **Chat is slow or errors:** skip to the safety-word message (instant, no AI), then move on.
- **Anything else:** switch to the backup video and keep talking over it.
