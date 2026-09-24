"""Crypto scanner — indicator, build and page-guard tests.

Synthetic in-memory panels only: no network and no real prices.parquet. The
real-data build is exercised by the daily workflow, where run_scanner.py's
own guards (cross-sectional invariants, naive-recompute divergence) abort
the build on a wrong panel.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_scanner_page as bsp
import run_scanner as rs
import scanner_indicators as si

TEMPLATE = Path(__file__).resolve().parent.parent / "scanner_template.html"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _walk(n: int, seed: int, drift: float = 0.0005, start: float = 100.0) -> pd.DataFrame:
    """Daily OHLCV random walk on a continuous UTC calendar."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    rets = drift + rng.normal(0, 0.03, n)
    close = start * np.exp(np.cumsum(rets))
    high = close * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    vol = rng.uniform(1e5, 2e5, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol}, index=idx)


def _geometric(n: int, daily: float, start: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    close = start * (1 + daily) ** np.arange(n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": np.full(n, 1e5)}, index=idx)


def _write_parquet(path: Path, frames: dict[str, pd.DataFrame]) -> None:
    parts = []
    for sym, f in frames.items():
        g = f.reset_index().rename(columns={"index": "date"})
        g["symbol"] = sym
        parts.append(g)
    pd.concat(parts, ignore_index=True).to_parquet(path)


# --------------------------------------------------------------------------
# calendar conversion — the one deliberate change from the ETF module
# --------------------------------------------------------------------------
def test_calendar_equivalent_constants():
    assert si.DAYS_YEAR == 365
    assert si.RANK_HORIZONS == (30, 91, 182, 365)
    assert si.MOMENTUM_SKIP == 30
    assert si.PCTL_WINDOW == 2 * si.DAYS_YEAR
    assert si.MIN_PCTL_OBS == si.DAYS_YEAR
    assert si.RANK_DELTA_LOOKBACK == 28
    # bar-count conventions are NOT converted
    assert (si.MA_MID, si.MA_LONG, si.RSI_PERIOD, si.ATR_PERIOD) == (50, 200, 14, 14)


def test_realised_vol_annualises_by_sqrt_365():
    f = _walk(60, seed=1)
    logret = np.log(f["close"] / f["close"].shift(1))
    expected = logret.iloc[-20:].std(ddof=1) * math.sqrt(365)
    assert si.realised_vol(f["close"]).iloc[-1] == pytest.approx(expected, rel=1e-12)


# --------------------------------------------------------------------------
# vectorised vs naive
# --------------------------------------------------------------------------
@pytest.mark.parametrize("seed", [3, 11, 29])
def test_vectorised_matches_naive(seed):
    f = _walk(400, seed=seed)
    c = f["close"].tolist()
    assert si.sma(f["close"], 20).iloc[-1] == pytest.approx(si.naive_sma_latest(c, 20), rel=1e-12)
    assert float(si.rsi(f["close"]).iloc[-1]) == pytest.approx(si.naive_rsi_latest(c), rel=1e-9)
    assert si.atr_pct(f["high"], f["low"], f["close"]) == pytest.approx(
        si.naive_atr_pct_latest(f["high"].tolist(), f["low"].tolist(), c), rel=1e-9)


def test_momentum_and_52w_on_known_series():
    f = _geometric(400, 0.001)
    c = f["close"]
    assert si.momentum_12_1(c) == pytest.approx(c.iloc[-31] / c.iloc[-366] - 1, rel=1e-12)
    assert si.vs_52w_high(c) == 0.0                      # monotone rise: fresh high
    assert si.total_return(c, 30) == pytest.approx(1.001 ** 30 - 1, rel=1e-9)


def test_trend_state_precedence():
    assert si.trend_state(_geometric(300, 0.002)["close"]) == si.TREND_STRONG_UP
    assert si.trend_state(_geometric(300, -0.002)["close"]) == si.TREND_STRONG_DOWN
    assert si.trend_state(_geometric(150, 0.002)["close"]) is None   # MA200 not warm


def test_relative_strength_aligns_on_date_not_position():
    coin = _geometric(100, 0.002)["close"]
    btc = _geometric(100, 0.001)["close"]
    expected = math.log(coin.iloc[-1] / coin.iloc[-31]) - math.log(btc.iloc[-1] / btc.iloc[-31])
    assert si.relative_strength_1m(coin, btc) == pytest.approx(expected, rel=1e-12)
    # A coin that stopped updating two days ago is compared with BTC over ITS
    # 30 days, not over BTC's latest 30.
    stale = coin.iloc[:-2]
    exp_stale = (math.log(stale.iloc[-1] / stale.iloc[-31])
                 - math.log(btc.loc[stale.index[-1]] / btc.loc[stale.index[-31]]))
    assert si.relative_strength_1m(stale, btc) == pytest.approx(exp_stale, rel=1e-12)
    # A benchmark missing the window's start date yields NaN, not a mismatch.
    assert math.isnan(si.relative_strength_1m(coin, btc.drop(coin.index[-31])))


def test_percentile_withheld_below_min_obs_and_truncated_flag():
    short = pd.Series(np.arange(200, dtype=float))
    assert math.isnan(si.percentile_of_latest(short).value)
    mid = pd.Series(np.arange(500, dtype=float))
    p = si.percentile_of_latest(mid)
    assert p.truncated and p.n_obs == 500 and p.value == pytest.approx(100 * 499.5 / 500)


def test_rank_is_permutation_and_short_history_unrankable():
    returns = pd.DataFrame(
        {30: [0.1, 0.2, np.nan, 0.05], 91: [0.3, 0.1, 0.2, 0.0],
         182: [0.5, np.nan, 0.1, 0.2], 365: [np.nan, 0.4, 0.3, 0.1]},
        index=["A", "B", "C", "D"])
    r = si.rank_from_horizon_returns(returns)
    assert sorted(r.ranks.tolist()) == [1, 2, 3]
    assert r.unrankable == ["C"]
    assert bool(r.truncated["A"]) and bool(r.truncated["B"]) and not bool(r.truncated["D"])


# --------------------------------------------------------------------------
# date edge cases (vault rule: one month boundary, one year boundary)
# --------------------------------------------------------------------------
def test_days_behind_month_boundary():
    assert rs.days_behind(pd.Timestamp("2026-09-29"), pd.Timestamp("2026-10-02")) == 3


def test_days_behind_year_boundary():
    assert rs.days_behind(pd.Timestamp("2025-12-30"), pd.Timestamp("2026-01-02")) == 3


def test_days_behind_leap_february():
    assert rs.days_behind(pd.Timestamp("2028-02-28"), pd.Timestamp("2028-03-01")) == 2


# --------------------------------------------------------------------------
# end-to-end build on a synthetic parquet
# --------------------------------------------------------------------------
@pytest.fixture
def synthetic(tmp_path):
    n = 800
    frames = {sym: _walk(n, seed=i, drift=0.0003 * i)
              for i, sym in enumerate(["BTC", "ETH", "SOL", "ADA", "XRP", "DOGE"])}
    # A frozen ticker in the parquet must never become a row.
    frames["LUNA"] = _walk(300, seed=99)
    prices = tmp_path / "prices.parquet"
    _write_parquet(prices, frames)
    last = frames["BTC"].index[-1]
    dash = tmp_path / "dashboard_data.json"
    dash.write_text(json.dumps({
        "meta": {"version": "v3.2"},
        "monitor": {
            "as_of": last.strftime("%Y-%m-%d"), "breadth": 0.6, "exposure": 0.5,
            "tier_label": "50% — half risk", "cash_weight": 0.5,
            "holdings": [{"coin": "SOL", "weight": 0.25}, {"coin": "ETH", "weight": 0.25}],
            "investable_names": ["BTC", "ETH", "SOL"], "last_rebal": "2025-03-10",
        },
    }), encoding="utf-8")
    today = last + pd.Timedelta(days=1)
    return prices, dash, today


def test_build_end_to_end(synthetic):
    prices, dash, today = synthetic
    payload, panel = rs.build(prices, dash, today)
    tickers = [r["ticker"] for r in payload["rows"]]
    assert "LUNA" not in tickers and "LUNA" in payload["frozen_excluded"]
    assert len(tickers) == 6
    assert sorted(r["rank"] for r in payload["rows"]) == list(range(1, 7))
    btc = next(r for r in payload["rows"] if r["ticker"] == "BTC")
    assert btc["rs_1m"] is None
    assert all(r["rs_1m"] is not None for r in payload["rows"] if r["ticker"] != "BTC")
    held = {r["ticker"] for r in payload["rows"] if r["held"]}
    assert held == {"SOL", "ETH"}
    assert {r["ticker"] for r in payload["rows"] if r["investable"]} == {"BTC", "ETH", "SOL"}
    assert payload["panel_days_behind"] == 0
    kinds = {o["kind"]: o for o in payload["overlays"]}
    assert kinds["breadth_gate"]["state"] == "PARTIAL"
    assert kinds["book"]["held"] == ["SOL", "ETH"]
    assert kinds["eth_btc"]["state"] in {"ON", "OFF"}
    assert payload["parameters_validated"] is False
    assert all(0 <= r["rv_pctl"] <= 100 for r in payload["rows"])
    hist = rs.build_history(panel)
    assert set(hist["series"]) == set(tickers)
    assert all(len(s["close"]) == len(hist["calendars"]["UTC"]) for s in hist["series"].values())


def test_build_flags_panel_behind_the_last_closed_candle(synthetic):
    prices, dash, today = synthetic
    payload, _ = rs.build(prices, dash, today + pd.Timedelta(days=3))
    assert payload["panel_days_behind"] == 3
    assert any("behind the last closed candle" in n for n in payload["data_health"]["notes"])


def test_build_without_dashboard_still_publishes(synthetic, tmp_path):
    prices, _, today = synthetic
    payload, _ = rs.build(prices, tmp_path / "missing.json", today)
    gate = next(o for o in payload["overlays"] if o["kind"] == "breadth_gate")
    assert gate["state"] is None
    assert not any(r["held"] for r in payload["rows"])


def test_stale_row_greyed_and_silent(tmp_path):
    frames = {sym: _walk(800, seed=i) for i, sym in enumerate(["BTC", "ETH", "SOL"])}
    frames["ADA"] = _walk(800, seed=7).iloc[:-5]           # five days behind
    prices = tmp_path / "p.parquet"
    _write_parquet(prices, frames)
    today = frames["BTC"].index[-1] + pd.Timedelta(days=1)
    payload, _ = rs.build(prices, tmp_path / "none.json", today)
    ada = next(r for r in payload["rows"] if r["ticker"] == "ADA")
    assert ada["stale"] and ada["sessions_behind"] == 5
    assert not any(a["ticker"] == "ADA" for a in payload["alerts"])
    # out of the cross-section: rank blank, the others rank 1..3 among themselves
    assert ada["rank"] is None and ada["rank_delta"] is None
    assert sorted(r["rank"] for r in payload["rows"] if r["rank"] is not None) == [1, 2, 3]


def test_gap_straddling_window_start_is_disclosed(tmp_path):
    # 1,000 bars, a 200-day hole, then 700 bars: the last 750 BARS reach back
    # 950 calendar days although the last 750 calendar days hold no gap.
    before = _walk(1000, seed=4)
    after_idx = pd.date_range(before.index[-1] + pd.Timedelta(days=201), periods=700, freq="D")
    after = _walk(700, seed=5).set_axis(after_idx)
    frames = {"BTC": _walk(1900, seed=1).set_axis(
        pd.date_range(before.index[0], after_idx[-1], freq="D")[-1900:]),
        "SOL": pd.concat([before, after])}
    prices = tmp_path / "p.parquet"
    _write_parquet(prices, frames)
    payload, _ = rs.build(prices, tmp_path / "none.json", after_idx[-1] + pd.Timedelta(days=1))
    assert any(n.startswith("SOL: 200 missing day(s)") for n in payload["data_health"]["notes"])


def test_gap_inside_window_is_disclosed(tmp_path):
    frames = {sym: _walk(800, seed=i) for i, sym in enumerate(["BTC", "ETH", "SOL"])}
    frames["SOL"] = frames["SOL"].drop(frames["SOL"].index[-100:-95])   # 5-day hole
    prices = tmp_path / "p.parquet"
    _write_parquet(prices, frames)
    today = frames["BTC"].index[-1] + pd.Timedelta(days=1)
    payload, _ = rs.build(prices, tmp_path / "none.json", today)
    notes = payload["data_health"]["notes"]
    assert any(n.startswith("SOL: 5 missing day(s)") for n in notes)
    assert not any(n.startswith("BTC:") for n in notes)


def test_rank_crossing_fires_on_entry_and_exit():
    # Six coins on steady trends, the weakest first. On the final day the
    # weakest jumps to the top; the one it displaces from the top 5 leaves.
    n = 500
    rates = {"A": 0.0060, "B": 0.0050, "C": 0.0040, "D": 0.0030, "E": 0.0020, "F": 0.0001}
    panel = {t: rs.CoinData(t, _geometric(n, g)) for t, g in rates.items()}
    f = panel["F"].frame.copy()
    f.iloc[-1, f.columns.get_loc("close")] *= 60.0
    panel["F"] = rs.CoinData("F", f)
    returns = pd.DataFrame({t: rs.horizon_returns(d) for t, d in panel.items()}).T
    now = si.rank_from_horizon_returns(returns)
    assert int(now.ranks["F"]) <= rs.RANK_CROSS_CUT
    alerts = rs.rank_crossings(panel, now)
    labels = {(a.ticker, a.label) for a in alerts}
    assert ("F", f"Entered the top {rs.RANK_CROSS_CUT}") in labels
    assert ("E", f"Left the top {rs.RANK_CROSS_CUT}") in labels


def test_no_crossing_when_inside_the_cut_on_any_prior_day():
    """Kills confirm=1 and all->any: F was inside the cut 7 days ago (a
    one-day spike), outside on days 1-6, and inside again today. The rule
    needs it OUTSIDE on every one of the 7 prior days, so nothing fires."""
    n = 500
    rates = {"A": 0.0060, "B": 0.0050, "C": 0.0040, "D": 0.0030, "E": 0.0020, "F": 0.0001}
    panel = {t: rs.CoinData(t, _geometric(n, g)) for t, g in rates.items()}
    f = panel["F"].frame.copy()
    col = f.columns.get_loc("close")
    f.iloc[-8, col] *= 60.0      # the bar that is "today" at offset 7
    f.iloc[-1, col] *= 60.0      # today
    panel["F"] = rs.CoinData("F", f)
    returns = pd.DataFrame({t: rs.horizon_returns(d) for t, d in panel.items()}).T
    now = si.rank_from_horizon_returns(returns)
    assert int(now.ranks["F"]) <= rs.RANK_CROSS_CUT
    assert not any(a.ticker == "F" for a in rs.rank_crossings(panel, now))


def test_vs_52w_uses_365_days_not_252():
    # Peak 300 days ago, then a steady decline: a 252-bar window would miss it.
    up = _geometric(400, 0.004)["close"]
    down = up.iloc[-1] * (1 - 0.001) ** np.arange(1, 301)
    close = pd.Series(np.r_[up.to_numpy(), down],
                      index=pd.date_range("2023-01-01", periods=700, freq="D"))
    assert si.vs_52w_high(close) == pytest.approx(close.iloc[-1] / up.iloc[-1] - 1, rel=1e-12)
    data = rs.CoinData("X", pd.DataFrame({"close": close}))
    assert not any(a.kind == "52w" and "low" not in a.label for a in rs.build_alerts(data, {}))


def test_sigma_yardstick_excludes_today():
    base = _geometric(260, 0.0)["close"].to_numpy()
    rets = [0.01 if i % 2 == 0 else -0.01 for i in range(20)] + [0.05]
    tail = base[-1] * np.cumprod(1 + np.array(rets))
    close = pd.Series(np.r_[base, tail],
                      index=pd.date_range("2023-01-01", periods=281, freq="D"))
    sigma_prior = pd.Series(rets[:-1]).std(ddof=1)            # the 20 before today
    alerts = [a for a in rs.build_alerts(rs.CoinData("X", pd.DataFrame({"close": close})), {})
              if a.kind == "sigma_move"]
    assert len(alerts) == 1
    assert alerts[0].label == f"{0.05 / sigma_prior:.1f}-sigma move"   # 4.9, not the inflated figure


def test_percentile_is_nan_when_latest_bar_missing():
    s = pd.Series(np.r_[np.arange(800, dtype=float), np.nan])
    assert math.isnan(si.percentile_of_latest(s).value)


def test_trend_withheld_until_slope_is_warm():
    assert si.trend_state(_geometric(210, -0.002)["close"]) is None
    assert si.trend_state(_geometric(221, -0.002)["close"]) == si.TREND_STRONG_DOWN


# --------------------------------------------------------------------------
# gate basis: latest-close tier and last Monday read are separate readings
# --------------------------------------------------------------------------
def test_gate_chip_separates_latest_close_from_monday_read():
    # Python weekday(): Monday = 0. 2026-09-21 is a Monday, 2026-09-23 a Wednesday.
    assert pd.Timestamp("2026-09-21").weekday() == 0
    assert pd.Timestamp("2026-09-23").weekday() == 2
    history = {"dates": ["2026-09-20", "2026-09-21", "2026-09-22", "2026-09-23"],
               "exposure": [0.6, 0.6, 1.0, 1.0], "breadth": [0.55, 0.58, 0.9, 1.0]}
    monday = rs.last_rebalance_read(history, "2026-09-23", 0)
    assert monday == {"date": "2026-09-21", "exposure": 0.6, "breadth": 0.58}
    monitor = {"as_of": "2026-09-23", "breadth": 1.0, "exposure": 1.0, "holdings": [],
               "monday_read": monday}
    gate = next(c for c in rs.overlay_chips(monitor, {}) if c["kind"] == "breadth_gate")
    assert gate["state"] == "RISK_ON" and gate["monday_state"] == "PARTIAL"
    assert gate["differs_from_monday"] and "differs" in gate["label"]
    assert "latest close" in gate["label"] and "last Monday read (2026-09-21)" in gate["label"]


def test_monday_read_across_month_and_year_boundaries():
    # month boundary: Wednesday 2026-10-01 reads Monday 2026-09-28
    h = {"dates": ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01"],
         "exposure": [0.3, 0.3, 0.6, 0.6], "breadth": [0.3] * 4}
    assert rs.last_rebalance_read(h, "2026-10-01", 0)["date"] == "2026-09-28"
    # year boundary: Friday 2027-01-01 reads Monday 2026-12-28
    h2 = {"dates": [d.strftime("%Y-%m-%d") for d in pd.date_range("2026-12-26", "2027-01-01")],
          "exposure": [1.0] * 7, "breadth": [1.0] * 7}
    assert pd.Timestamp("2026-12-28").weekday() == 0
    assert rs.last_rebalance_read(h2, "2027-01-01", 0)["date"] == "2026-12-28"


# --------------------------------------------------------------------------
# the date-indexed guard can fail
# --------------------------------------------------------------------------
@pytest.mark.parametrize("field, bump", [("mom_12_1", 0.01), ("vs_52w_high", -0.01),
                                         ("rs_1m", 0.01), ("rank_delta", 1)])
def test_date_indexed_guard_catches_a_tampered_value(synthetic, field, bump):
    prices, dash, today = synthetic
    payload, panel = rs.build(prices, dash, today)
    rows = json.loads(json.dumps(payload["rows"]))
    target = next(r for r in rows if r["ticker"] != "BTC" and r[field] is not None)
    target[field] += bump
    bench = panel["BTC"].close
    with pytest.raises(rs.ScannerBuildError, match="date-indexed"):
        rs.assert_date_indexed_recompute(panel, rows, bench, set())


# --------------------------------------------------------------------------
# page builder guards and prose pinned to constants
# --------------------------------------------------------------------------
def test_page_guard_rejects_frozen_leak_and_validated_flag(synthetic):
    prices, dash, today = synthetic
    payload, _ = rs.build(prices, dash, today)
    bsp.assert_payload_usable(payload)
    leaked = json.loads(json.dumps(payload))
    leaked["rows"][0]["ticker"] = "LUNA"
    with pytest.raises(bsp.ScannerPageError, match="frozen"):
        bsp.assert_payload_usable(leaked)
    claimed = dict(payload, parameters_validated=True)
    with pytest.raises(bsp.ScannerPageError, match="parameters_validated"):
        bsp.assert_payload_usable(claimed)


def test_inject_escapes_script_close():
    out = bsp.inject(
        "x\n// __SCANNER_DATA_START__\nconst SCANNER_DATA_INLINE = null;\n// __SCANNER_DATA_END__\ny",
        {"k": "</script>"})
    assert "</script>" not in out and "<\\/script>" in out


def test_guide_prose_matches_frozen_thresholds():
    """The guide quotes thresholds in prose; pin them to the constants."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert rs.ALERT_VOLUME_MULTIPLE == 3.0 and "above 3×" in html
    assert rs.SQUEEZE_RV_PCTL == 25.0 and "RV below p25" in html
    assert rs.SQUEEZE_BBW_PCTL == 10.0 and "BBW below p10" in html
    assert rs.SQUEEZE_RELEASE_SIGMA == 1.5 and "more than 1.5" in html
    assert (rs.ALERT_RSI_HIGH, rs.ALERT_RSI_LOW) == (75.0, 25.0) and "75 / 25" in html
    assert rs.ALERT_SIGMA_MOVE == 2.0 and "beyond 2 standard deviations" in html
    assert "30/91/182/365" in html and si.RANK_HORIZONS == (30, 91, 182, 365)
    # the cell highlights in the JS mirror the same thresholds
    assert "row.vol_ratio > 3" in html and "row.rv_pctl < 25 && row.bbw_pctl < 10" in html
