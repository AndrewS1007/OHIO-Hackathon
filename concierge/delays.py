"""Delays learned from past storms: when damage gets reported, and how long it waits for a crew.

The labor-hour physics says a pole takes ~7 crew-hours. The ARCOS history says
damage keeps being reported for days after a big storm, and a repair is
logged a day or more after its report. Two empirical distributions capture that:

  report_lags  hours from storm start to each damage report
  waits        hours from report to repair, minus the modeled work time
               (assessment, queueing behind other jobs, travel, logistics)

Both are learned per storm size ("large" storms behave differently), and a
storm can be left out so it is never used to predict itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd

from .data import Dataset

LARGE_STORM_FACTOR = 3  # a storm with 3x the median storm's damage records is "large"
MIN_SAMPLES = 20        # fewer learned values than this -> pool both sizes


@dataclass(frozen=True, eq=False)
class Delays:
    report_lags: np.ndarray  # sorted
    waits: np.ndarray        # sorted
    size: str
    storms: tuple[str, ...]  # storms it was learned from

    def survival(self, t: float) -> float:
        """Share of a storm's damage still unreported t hours after it started."""
        return float(1 - np.searchsorted(self.report_lags, t, side="right") / len(self.report_lags))

    def sample_lags_after(self, rng, now_h: float, n: int) -> np.ndarray:
        """Report times for damage not yet found by now_h."""
        later = self.report_lags[self.report_lags > now_h]
        if not len(later):
            return now_h + rng.uniform(0.5, 6, size=n)
        return rng.choice(later, size=n)

    def sample_waits(self, rng, n: int) -> np.ndarray:
        return rng.choice(self.waits, size=n)

    def sample_waits_over(self, rng, elapsed: np.ndarray) -> np.ndarray:
        """Total waits for jobs that have already waited `elapsed` hours (they're running late)."""
        lo = np.searchsorted(self.waits, elapsed, side="right")
        out = np.empty(len(elapsed))
        none = lo >= len(self.waits)
        pick = rng.integers(lo[~none], len(self.waits))
        out[~none] = self.waits[pick]
        out[none] = elapsed[none] + rng.exponential(6.0, size=int(none.sum()))
        return out


@lru_cache(maxsize=8)
def storm_sizes(ds: Dataset) -> dict[str, str]:
    n = ds.damage.groupby("event_id").size().reindex(ds.storms.event_id, fill_value=0)
    cut = LARGE_STORM_FACTOR * n.median()
    return {ev: "large" if k >= cut else "small" for ev, k in n.items()}


def size_for_wind(ds: Dataset, wind_mph: float) -> str:
    """For a forecast: the size class of the past storm with the closest wind."""
    ev = ds.storms.loc[(ds.storms.max_wind_mph - wind_mph).abs().idxmin(), "event_id"]
    return storm_sizes(ds)[ev]


@lru_cache(maxsize=64)
def learn(ds: Dataset, size: str, exclude: tuple[str, ...] = ()) -> Delays:
    sizes = storm_sizes(ds)
    starts = ds.storms.set_index("event_id").start_time
    pool = ds.damage.loc[~ds.damage.event_id.isin(exclude)]
    same = pool.loc[pool.event_id.map(sizes) == size]
    # When damage gets reported depends on storm size; how long a repair waits is pooled
    # across sizes (it varies less, and there are few large storms to learn from).
    rep = same if len(same) >= MIN_SAMPLES else pool
    lag = (rep.report_time - rep.event_id.map(starts)).dt.total_seconds() / 3600
    dmg = pool
    turnaround = (dmg.repair_complete_time - dmg.report_time).dt.total_seconds() / 3600
    mult = float(np.prod(list(ds.indirect.values())))
    work = (dmg.damage_type.map(ds.labor_hours).fillna(0) * dmg.quantity + dmg.get("extra_hours", 0.0)) * mult
    waits = (turnaround - work).clip(lower=0.0)
    return Delays(np.sort(lag.clip(lower=0).to_numpy()), np.sort(waits.to_numpy()), size,
                  tuple(sorted(set(rep.event_id))))


def for_event(ds: Dataset, event_id: str) -> Delays:
    """Delays for replaying a past storm: learned from every OTHER storm of its size."""
    return learn(ds, storm_sizes(ds)[event_id], (event_id,))


def remaining_damage(prestorm_total: float, known: int, d: Delays, t: float) -> float:
    """Expected damage on the circuit not reported yet at hour t (a nowcast).

    If a share (1 - S) of a storm's damage is usually reported by now, `known`
    reports imply about known / (1 - S) in total. Use that once enough is in,
    and never expect less than the pre-storm estimate."""
    s = d.survival(t)
    total = prestorm_total
    if 1 - s >= 0.1:
        total = max(total, known / (1 - s))
    return max(0.2, total - known) if s > 0 else 0.2
