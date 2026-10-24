"""The scoring math behind reports/EVALUATION.md."""
import numpy as np
import pandas as pd

from concierge import evaluate
from concierge.data import get_dataset


def test_restore_scores_count_inside_and_broken_promises():
    df = pd.DataFrame({"p10": [1, 1, 1, 1], "p50": [5, 5, 5, 5], "p90": [10, 10, 10, 10], "actual": [0.5, 5, 9, 30]})
    s = evaluate._restore_scores(df)
    assert s["coverage"] == 0.5 and s["broken_promises"] == 0.25 and s["too_pessimistic"] == 0.25
    assert s["p50_within_12h"] == 0.75 and s["median_range_width_h"] == 9


def test_risk_scores_beat_the_average_only_when_informative():
    df = pd.DataFrame({"y": [0, 0, 1, 1], "customers": [1, 1, 1, 1]})
    base = np.full(4, 0.5)
    good = evaluate._risk_scores(df, np.array([0.1, 0.2, 0.8, 0.9]), base)
    assert good["auc"] == 1.0 and good["brier_skill"] > 0
    assert evaluate._risk_scores(df, base, base)["brier_skill"] == 0


def test_safety_eval_reports_misses():
    r = evaluate.safety_eval()
    assert r["phrases"] == len(evaluate.SAFETY_SET)
    assert r["accuracy"] == round(1 - len(r["misses"]) / r["phrases"], 3)
    assert r["normal_questions_answered"] >= 0.9  # false refusals are what customers notice first


def test_storm_sizes_label_every_storm():
    ds = get_dataset()
    sizes = evaluate.storm_sizes(ds)
    assert set(sizes) == set(ds.storms.event_id) and set(sizes.values()) <= {"large", "small"}
