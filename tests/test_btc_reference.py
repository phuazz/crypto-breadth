"""
BTC reference tab: the self-built replica maths (scripts/btc_reference.py).

Display-only series, but they carry numbers a reader will look at, so the
construction is pinned: the z-score plateau a steady trend produces, the
reconstructed three-state rule, the weekday calendar, the no-look-ahead
return attribution, and the month / year boundaries of the seasonality table.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from btc_reference import (bracket_states, ma_spread, ma_zscore, monthly_seasonality,
                           state_returns, weekday_bars, build_payload)


def test_weekday_bars_drop_weekends():
    idx = pd.date_range("2026-09-18", "2026-09-24", freq="D")   # Fri..Thu
    px = pd.Series(np.arange(len(idx), dtype=float) + 1, index=idx)
    wk = weekday_bars(px)
    assert list(wk.index.weekday) == [4, 0, 1, 2, 3]   # Fri, Mon..Thu
    assert pd.Timestamp("2026-09-19") not in wk.index   # Saturday
    assert pd.Timestamp("2026-09-20") not in wk.index   # Sunday


def test_zscore_plateau_on_linear_trend():
    # A linear ramp gives a linear MA; the z-score of the newest of 15 evenly
    # spaced values is 7 / sd(0..14) = 7 / sqrt(20) = 1.565, the plateau the
    # vendor chart sits on in a steady trend.
    px = pd.Series(np.arange(300, dtype=float) + 100,
                   index=pd.bdate_range("2020-01-01", periods=300))
    z = ma_zscore(px).dropna()
    assert np.allclose(z.values, 7 / np.sqrt(20), atol=1e-9)
    zdn = ma_zscore(px[::-1].reset_index(drop=True).set_axis(px.index)).dropna()
    assert np.allclose(zdn.values, -7 / np.sqrt(20), atol=1e-9)


def test_bracket_state_machine():
    idx = pd.bdate_range("2024-01-01", periods=9)
    z = pd.Series([np.nan, 0.0, -1.5, -1.0, 1.6, 1.0, -0.5, -1.4, 1.3], index=idx)
    st = bracket_states(z, upper=1.15, lower=-1.25)
    assert pd.isna(st.iloc[0])            # warm-up
    assert list(st.iloc[1:]) == [
        "N",   # first defined bar starts neutral
        "N",   # 0.0 -> -1.5: down through lower -> neutral
        "B",   # -1.5 -> -1.0: up through lower -> bullish
        "B",   # up through upper changes nothing
        "R",   # 1.6 -> 1.0: down through upper -> bearish
        "R",   # holds
        "N",   # -0.5 -> -1.4: down through lower -> neutral
        "B",   # -1.4 -> 1.3: up through lower -> bullish
    ]


def test_state_returns_use_next_bar():
    # State known at close T earns T -> T+1 only. Price jumps +100% on day 3;
    # a state set ON day 3 must not earn that jump.
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    px = pd.Series([100.0, 100.0, 200.0, 200.0], index=idx)
    st = pd.Series(["N", "B", "R", "R"], index=idx)
    rows = {r["state"]: r for r in state_returns(px, st)}
    # "B" is held on day 2 -> earns day 2 -> 3 (+100%); "R" on day 3 earns 0.
    assert rows["B"]["n_days"] == 1 and rows["B"]["gain_pa"] > 1e6
    assert rows["R"]["n_days"] == 1 and rows["R"]["gain_pa"] == 0.0
    assert rows["N"]["gain_pa"] == 0.0


def test_ma_spread_sign():
    px = pd.Series(np.linspace(100, 200, 250), index=pd.bdate_range("2021-01-01", periods=250))
    assert (ma_spread(px, 50, 200).dropna() > 0).all()


def test_seasonality_month_and_year_boundaries():
    # Daily closes 2023-11-15 .. 2024-03-10. Month-end closes: Nov 30, Dec 31,
    # Jan 31, Feb 29 (2024 is a leap year). Expected returns: Dec (crosses the
    # year boundary from Nov 30), Jan, Feb. Nov is partial (no prior
    # month-end) and Mar is still open, so both are excluded.
    idx = pd.date_range("2023-11-15", "2024-03-10", freq="D")
    px = pd.Series(100.0, index=idx)
    px[px.index > "2023-11-30"] = 110.0     # Dec: +10%
    px[px.index > "2023-12-31"] = 99.0      # Jan: -10%
    px[px.index > "2024-01-31"] = 99.0      # Feb: 0%
    px[px.index > "2024-02-29"] = 150.0     # Mar: open month, must be ignored
    s = monthly_seasonality(px)
    n = dict(zip(s["month"], s["n"]))
    mean = dict(zip(s["month"], s["mean"]))
    assert n[12] == 1 and abs(mean[12] - 0.10) < 1e-9     # year boundary
    assert n[1] == 1 and abs(mean[1] + 0.10) < 1e-9
    assert n[2] == 1 and mean[2] == 0.0                   # leap-year month end
    assert n[11] == 0 and n[3] == 0                        # partial / open months
    assert s["first_month"] == "2023-12" and s["last_month"] == "2024-02"


def test_payload_shape_on_synthetic_series():
    idx = pd.date_range("2019-01-01", "2021-06-30", freq="D")
    rng = np.random.default_rng(0)
    px = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.03, len(idx)))), index=idx)
    p = build_payload(px)
    n = len(p["dates"])
    assert len(p["btc"]) == len(p["z"]) == len(p["state"]) == n
    assert all(len(v) == n for v in p["spread"].values())
    assert p["latest"]["state"] in {"B", "N", "R"}
    assert abs(sum(r["pct_time"] for r in p["stats"]["zs"] if r["state"] != "all") - 1) < 1e-9
