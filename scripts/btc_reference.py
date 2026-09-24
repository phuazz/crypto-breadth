"""
btc_reference.py
----------------
Self-built replicas of three published Bitcoin reference charts, for display
on the dashboard's "BTC reference" tab. REFERENCE ONLY: none of these series
feeds the engine, the gate, the digest or any sizing decision.

  Z-score    63-day MA of BTC, z-scored over 15 observations, with a
             reconstructed bracket-reversal state (bullish / neutral / bearish).
  MA spreads % spread of the 25-day MA over the 100-day, and the 50-day over
             the 200-day.
  Seasonality  calendar-month BTC returns on the Binance window.

Built from data/prices.parquet (Binance BTCUSDT) only. The vendor's own series
is licensed for internal use and is never read here.

Calendar: the vendor builds on business-day bars, and the replica only tracks
it on the same basis (weekday rebuild corr 0.980 vs 0.718 on the 7-day
calendar, 2026-09-24 check). So the indicators are computed on WEEKDAY bars
(UTC dates, Monday-Friday) and the state is carried over the weekend.

Discovery-grade review, 2026-09-24 (not pre-registered, nothing logged in
results/trial_registry.jsonl): no arm built from these improved the v3.2 engine
beyond a timing-shuffled placebo. That is why they are display-only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ZS_MA = 63
ZS_ZWIN = 15
# Brackets read off the vendor chart, not published numerically.
ZS_UPPER = 1.15
ZS_LOWER = -1.25
CROSSES = ((25, 100), (50, 200))

STATE_CODES = {"B": 1, "N": 0, "R": -1}


def weekday_bars(px: pd.Series) -> pd.Series:
    """Keep Monday-Friday bars (pandas weekday: Monday=0 ... Sunday=6)."""
    px = px.dropna()
    return px[px.index.weekday < 5]


def ma_zscore(px: pd.Series, ma: int = ZS_MA, zwin: int = ZS_ZWIN) -> pd.Series:
    """z-score of the `ma`-bar simple moving average over its last `zwin`
    values (sample standard deviation, ddof=1)."""
    m = px.rolling(ma).mean()
    return (m - m.rolling(zwin).mean()) / m.rolling(zwin).std()


def bracket_states(z: pd.Series, upper: float = ZS_UPPER,
                   lower: float = ZS_LOWER) -> pd.Series:
    """Reconstructed three-state rule. Each state holds until the next event:
      - z crosses UP through the lower bracket   -> "B" (bullish)
      - z crosses DOWN through the upper bracket -> "R" (bearish)
      - z crosses DOWN through the lower bracket -> "N" (neutral)
    Crossing up through the upper bracket changes nothing. Undefined z (warm-up)
    gives NaN; the first defined bar starts neutral.

    Fitted to the vendor's published time shares (bullish 45.1% exact; neutral
    33.4% vs 30.2%; bearish 21.5% vs 24.7%). It is an inference, not their rule.
    """
    out = pd.Series(np.nan, index=z.index, dtype=object)
    cur, prev = None, np.nan
    for dt, v in z.items():
        if np.isnan(v):
            prev = v
            continue
        if cur is None:
            cur = "N"
        elif not np.isnan(prev):
            if prev <= lower < v:
                cur = "B"
            elif prev >= upper > v:
                cur = "R"
            elif prev >= lower > v:
                cur = "N"
        out[dt] = cur
        prev = v
    return out


def ma_spread(px: pd.Series, fast: int, slow: int) -> pd.Series:
    """Fast MA as a % over the slow MA (0.05 = fast 5% above slow)."""
    return px.rolling(fast).mean() / px.rolling(slow).mean() - 1.0


def state_returns(px_daily: pd.Series, state_daily: pd.Series) -> list[dict]:
    """Annualised BTC return while in each state, vendor-table style
    (% gain per annum while the state is in force, and % of time), on the
    7-day daily series. The state known at close T earns the T -> T+1 return,
    so there is no same-bar look-ahead. Geometric: prod(1+r) ** (365/n) - 1.
    Last row is buy-and-hold over the same bars."""
    r = px_daily.pct_change().shift(-1)
    df = pd.DataFrame({"r": r, "s": state_daily}).dropna()
    rows = []
    for s in sorted(df["s"].unique(), key=lambda k: str(k)):
        x = df.loc[df["s"] == s, "r"]
        rows.append({"state": s, "n_days": int(len(x)),
                     "pct_time": float(len(x) / len(df)),
                     "gain_pa": float(np.prod(1 + x) ** (365 / len(x)) - 1)})
    rows.append({"state": "all", "n_days": int(len(df)), "pct_time": 1.0,
                 "gain_pa": float(np.prod(1 + df["r"]) ** (365 / len(df)) - 1)})
    return rows


def monthly_seasonality(px_daily: pd.Series) -> dict:
    """Calendar-month BTC returns from month-end closes. The first and last
    months are dropped when incomplete, so a partial month never enters a mean.
    Months are 1-indexed (Python). t is mean / (sd / sqrt(n))."""
    px = px_daily.dropna()
    # Last close of each calendar month. pct_change leaves the first month NaN
    # (no prior month-end), which is exactly the partial month to drop.
    me = px.groupby([px.index.year, px.index.month]).tail(1)
    last = px.index[-1]
    if last != last + pd.offsets.MonthEnd(0):   # current month still open
        me = me[me.index < last.replace(day=1)]
    rets = me.pct_change().dropna()
    out = {"month": [], "n": [], "mean": [], "median": [], "pct_pos": [], "t": []}
    for m in range(1, 13):
        x = rets[rets.index.month == m]
        out["month"].append(m)
        out["n"].append(int(len(x)))
        if len(x) == 0:
            for k in ("mean", "median", "pct_pos", "t"):
                out[k].append(None)
            continue
        sd = x.std()
        out["mean"].append(round(float(x.mean()), 4))
        out["median"].append(round(float(x.median()), 4))
        out["pct_pos"].append(round(float((x > 0).mean()), 4))
        out["t"].append(round(float(x.mean() / (sd / np.sqrt(len(x)))), 2)
                        if len(x) > 1 and sd > 0 else None)
    out["first_month"] = str(rets.index[0].date())[:7]
    out["last_month"] = str(rets.index[-1].date())[:7]
    out["n_months"] = int(len(rets))
    out["max_abs_t"], out["p_max_t"] = _max_t_shuffle(rets)
    return out


def _month_t(vals: np.ndarray, months: np.ndarray) -> float:
    best = 0.0
    for m in range(1, 13):
        x = vals[months == m]
        if len(x) > 1 and x.std(ddof=1) > 0:
            best = max(best, abs(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))))
    return best


def _max_t_shuffle(rets: pd.Series, n_perm: int = 2000, seed: int = 20260924):
    """Is the most extreme month more extreme than chance? Shuffle the month
    labels across the same returns and ask how often the largest |t| of any
    month is at least the observed one. This corrects for having looked at
    twelve months and picked the best, which a single-month t does not."""
    if len(rets) < 24:
        return None, None
    vals, months = rets.values, rets.index.month.values
    obs = _month_t(vals, months)
    rng = np.random.default_rng(seed)
    hits = sum(_month_t(vals, rng.permutation(months)) >= obs for _ in range(n_perm))
    return round(float(obs), 2), round(float((hits + 1) / (n_perm + 1)), 3)


def _r(v, nd):
    return None if v is None or (isinstance(v, float) and np.isnan(v)) else round(float(v), nd)


def build_payload(btc_daily: pd.Series) -> dict:
    """Everything the BTC reference tab draws, from the 7-day Binance series."""
    btc_daily = btc_daily.dropna()
    wk = weekday_bars(btc_daily)
    z = ma_zscore(wk)
    st = bracket_states(z)
    spreads = {f"{f}_{s}": ma_spread(wk, f, s) for f, s in CROSSES}

    # Weekday states carried over the weekend onto the 7-day series.
    st_daily = st.reindex(btc_daily.index).ffill()
    cross_daily = {k: (v > 0).map({True: "above", False: "below"})
                   .where(v.notna()).reindex(btc_daily.index).ffill()
                   for k, v in spreads.items()}

    idx = wk.index
    latest = z.last_valid_index()
    payload = {
        "meta": {
            "as_of": str(idx[-1].date()),
            "sample_start": str(btc_daily.index[0].date()),
            "zs": {"ma": ZS_MA, "zwin": ZS_ZWIN,
                    "upper": ZS_UPPER, "lower": ZS_LOWER},
            "crosses": [list(c) for c in CROSSES],
        },
        "dates": [str(d.date()) for d in idx],
        "btc": [_r(v, 2) for v in wk.values],
        "z": [_r(v, 3) for v in z.values],
        "state": [None if not isinstance(v, str) else v for v in st.values],
        "spread": {k: [_r(x, 4) for x in v.values] for k, v in spreads.items()},
        "latest": {
            "date": str(latest.date()) if latest is not None else None,
            "z": _r(z.loc[latest], 3) if latest is not None else None,
            "state": st.loc[latest] if latest is not None else None,
            "spread": {k: _r(v.loc[idx[-1]], 4) for k, v in spreads.items()},
        },
        "stats": {
            "zs": state_returns(btc_daily, st_daily),
            **{k: state_returns(btc_daily, v) for k, v in cross_daily.items()},
        },
        "seasonality": monthly_seasonality(btc_daily),
    }
    return payload
