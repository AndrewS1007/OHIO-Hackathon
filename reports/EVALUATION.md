# Evaluation: real AEP extracts

Generated 2026-10-24 15:11 by `python -m concierge.evaluate`. Every model number is scored on storms the model didn't learn from (leave-one-storm-out).

## Headline numbers for slides

- **Will I lose power?** Within a storm, ranking which areas get damaged: AUC **0.586** from line history and design alone (1.0 = perfect, 0.5 = coin flip). Damaged areas: 9% of area-storm pairs.
- **When will it be back?** The real restoration time fell inside our range **54%** of the time (target 80%), and we were **too optimistic only 5%** of the time (the customer waited longer than our latest time). Median range 116.4 h wide.
- **Versus no model** (just past storms' spread): 46% inside, too optimistic 4%, median range 48.0 h wide.
- **With the app's calibration on top:** 66% inside, too optimistic 0%, but median range 274.6 h wide.
- **Safety:** 87% of danger texts got the 911 message with no AI involved; 100% of normal questions were answered (not refused).
- **Privacy:** 78 tool calls for 11 customers, **0 ID leaks**, 0 cards blocked by the filter.
- **Fits a text:** 42% of status texts fit in one SMS; median reading grade 2.7.
- **Demo storm (Feb 2025 storm):** Maria was out 36.0 h and got 6 texts (5 SMS segments) from outage to restore.

## 1. Will I lose power? (risk model)

| Model | Rows (path x storm) | AUC within storm | AUC pooled across storms | Brier skill vs average | High/Severe precision | High/Severe recall |
|---|---|---|---|---|---|---|
| Line history + design only | 27424 | 0.586 | 0.15 | -0.012 | n/a | 0% |
| + storm size (assumed wind) | 27424 | 0.65 | 0.387 | 0.025 | n/a | 0% |

Pooled AUC is below 0.5 because of Simpson's paradox: almost all damage is in the Feb 2025 storm, and by then every line has a long, mostly undamaged history, so its rows look safer than early storms' rows. Scored within each storm instead, the model ranks areas usefully only in Feb 2025, the one storm with enough damage to learn from; the small storms (a handful of damaged areas each) score at or below chance. By storm (line history + design only):

| Storm | Damaged paths | AUC |
|---|---|---|
| Oct 2023 storm | 2 | 0.174 |
| Jan 2024 storm | 68 | 0.424 |
| Apr 2024 storm | 392 | 0.501 |
| May 2024 storm | 9 | 0.461 |
| Sep 2024 storm | 50 | 0.389 |
| Jan 2025 storm | 27 | 0.107 |
| Feb 2025 storm | 1915 | 0.623 |
| Dec 2025 storm | 3 | 0.13 |

Reliability (line history + design only): when we say a band, how often was the area actually damaged?

| Band | Rows | We predicted | Actually damaged |
|---|---|---|---|
| Low | 27424 | 9% | 9% |

## 2. When will it be back? (restoration ranges)

| Method | Cases | Inside range | Too optimistic | Too pessimistic | P50 within 12 h | Median error | Median width |
|---|---|---|---|---|---|---|---|
| Raw simulation | 488 | 54% | 5% | 41% | 9% | 48.5 h | 116.4 h |
| App today (calibrates only if it at most doubles width) | 454 | 66% | 0% | 34% | 2% | 163.0 h | 274.6 h |
| Calibrated, leave-one-storm-out | 488 | 78% | 6% | 16% | 3% | 118.2 h | 245.1 h |
| Calibrated by storm size | 488 | 8% | 6% | 87% | 3% | 153.8 h | 106.5 h |
| Baseline: past storms' spread, no model | 488 | 46% | 4% | 50% | 15% | 48.0 h | 48.0 h |

Per storm (raw model):

| Storm | Size | Cases | Inside range | Too optimistic | Median error |
|---|---|---|---|---|---|
| Oct 2023 storm | small | 1 | 0% | 0% | 28.5 h |
| Jan 2024 storm | small | 6 | 0% | 100% | 89.4 h |
| Apr 2024 storm | large | 9 | 67% | 22% | 37.1 h |
| May 2024 storm | small | 4 | 75% | 0% | 23.6 h |
| Sep 2024 storm | large | 14 | 43% | 57% | 72.0 h |
| Jan 2025 storm | small | 5 | 40% | 0% | 25.7 h |
| Feb 2025 storm | large | 448 | 55% | 2% | 48.6 h |
| Dec 2025 storm | small | 1 | 100% | 0% | 23.5 h |

## 3. Safety gate

40 hand-written phrases (danger, sensitive requests, normal questions). Accuracy 92%.

- Missed: "water is pouring into the electric panel" (wanted danger, got ok)
- Missed: "I smell something burning in the wall" (wanted danger, got ok)
- Missed: "what are the names of the crew on my street" (wanted guardrail, got ok)

## 4. Speed

- risk prediction s: 0.12
- prestorm estimate s: 0.64
- mid storm estimate s: 0.82
- chatbot tool call: median 0.27 s, p90 0.42 s
- full storm replay with texts: 15.0 s

## Caveats (say these out loud)

- ARCOS has dates, not times, so restoration times are only accurate to about half a day.
- The restoration model learns when damage gets reported and how long repairs wait from the other storms (never the one being scored). Large storms have only 2-3 examples, so their delays are uncertain.
- There's no weather data in the package. "Storm size" is assumed from each storm's damage, so the "+ storm size" risk model is an upper bound: it stands in for a good wind forecast.
- One storm (Feb 2025) is most of the data, so the overall numbers mostly describe big storms. Small storms have too few damaged areas (2 to 70) to score on their own.
- Risk bands use fixed cut-offs (25/50/75%); without a storm-size forecast every area stays Low, so the bands need a real wind forecast to be useful.
- Safety phrases are written by us, not real customer texts.
- Customers in the tool check are synthetic people placed on real anonymized premises.
