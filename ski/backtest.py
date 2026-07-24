"""Point-in-time backtest harness for the Trip Predictor.

THE LEAK PROBLEM this module exists to prevent
----------------------------------------------
Two places in the scoring stack legitimately read data *after* a given date,
and both would silently invalidate a backtest if used naively:

1. `pipeline.retro_incoming_storms` grades the snow that fell in the window
   AFTER `as_of` -- by design, because the retrospective-history feature answers
   "was that a good weekend", not "could we have predicted it". So
   `mountain_scorecard(retro=True)` must NEVER be used to build the PREDICTION
   side of a backtest. It is fine (and is what we want) on the OUTCOME side.

2. `trip.climatology` has no internal date cutoff -- it aggregates whatever
   observation frame it is handed, including water years AFTER the simulated
   date. Nothing in the module is wrong; point-in-time discipline is the
   CALLER's responsibility. That is what `obs_asof` + `assert_no_leak` enforce.

The rule this module encodes: at simulated date T, the prediction may use only
observations timestamped <= T; the outcome may use only observations in a
forward window strictly after T.
"""

from __future__ import annotations

from datetime import date

import pandas as pd


class LeakageError(AssertionError):
    """Raised when a frame that should be point-in-time contains post-T rows."""


def obs_asof(obs: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Observations strictly available at `as_of` (date <= as_of).

    The single choke point for point-in-time discipline: every prediction-side
    input must pass through here."""
    if obs is None or obs.empty:
        return obs
    return obs[obs["date"] <= pd.Timestamp(as_of)].copy()


def assert_no_leak(obs: pd.DataFrame, as_of: date, label: str = "prediction") -> None:
    """Hard assertion that `obs` holds nothing after `as_of`.

    Called on every prediction-side frame in the backtest loop. A failure means
    the harness is measuring its own foreknowledge, so it raises rather than
    warns."""
    if obs is None or obs.empty:
        return
    latest = obs["date"].max()
    if pd.notna(latest) and latest > pd.Timestamp(as_of):
        raise LeakageError(
            f"{label} frame leaks future data: max date {latest.date()} > as_of {as_of}")


def outcome_window(obs: pd.DataFrame, as_of: date, days: int) -> pd.DataFrame:
    """The OUTCOME frame: observations in (as_of, as_of + days].

    Deliberately forward-looking -- this is what actually materialized, the
    thing we are scoring the prediction against."""
    if obs is None or obs.empty:
        return obs
    start = pd.Timestamp(as_of)
    end = start + pd.Timedelta(days=days)
    return obs[(obs["date"] > start) & (obs["date"] <= end)].copy()
