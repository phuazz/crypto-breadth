"""Cross-sectional crypto scanner — indicator library.

Ported from breadth-thrust-etf ``scripts/scanner_indicators.py`` (the ETF
scanner, spec ``etf_scanner_spec_en.md``). The functions are unchanged; the
frozen constants are converted from the equity 252-session year to crypto's
365-day UTC calendar. The module is pure: no I/O, no logging, no network.
These functions are the part of the scanner that can be silently wrong
without anything failing, so they are isolated where a synthetic-panel test
can pin every output.

Calendar conversion (owner decision 2026-09-24: calendar-equivalent). Every
look-back that the ETF spec expresses as a span of TIME keeps the same
elapsed time, so "12-month momentum" still means twelve months:

    ETF (trading days)          crypto (calendar days, 24/7 market)
    1M / 3M / 6M / 12M  21/63/126/252   ->  30 / 91 / 182 / 365
    12-1 skip           21              ->  30
    52-week high        252             ->  365
    percentile window   504 (~2y)       ->  730
    min percentile obs  252             ->  365
    delta-R look-back   20 (~4 weeks)   ->  28
    RV annualisation    sqrt(252)       ->  sqrt(365)

Indicators whose convention is a BAR COUNT keep it, because that is how they
are quoted for crypto as for equities: MA 20/50/200, the 20-bar MA slope,
RSI 14, ATR 14, Bollinger 20/2 sigma, RV 20, volume 20. Consequence: MA200 on
a daily crypto chart spans ~6.6 months, not the ~9.5 months it spans on an
equity chart. That is the market convention and it is stated, not hidden.

Four conventions carried over from the ETF module, stated so they can be
overruled rather than discovered:

1. **MA slope estimator.** ``MA(t) - MA(t-20) > 0``, not an OLS fit.
2. **Trend-state precedence.** Strong up -> Strong down -> Range -> Up ->
   Down, so a rising tight-MA configuration reads as Strong up.
3. **Percentile convention.** Mean percentile-of-score: fraction of the
   window strictly below the current value plus half the ties, x100. The
   window includes the current observation.
4. **Composite rank with missing horizons.** A coin short of 365 days
   averages the horizons it has and is flagged truncated; a coin missing
   either of the two shortest horizons is excluded from the ranking.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Frozen parameters. Industry defaults, calendar-converted as described in
# the module docstring. NONE has been validated on this universe. Changing
# any value requires a named, pre-registered out-of-sample process — not a
# tweak because a sample day looked better.
# --------------------------------------------------------------------------
MA_SHORT = 20
MA_MID = 50
MA_LONG = 200
SLOPE_LOOKBACK = 20          # bars; MA-slope convention, kept as a bar count
RSI_PERIOD = 14
ATR_PERIOD = 14
BBW_WINDOW = 20
BBW_SIGMA = 2.0
RV_WINDOW = 20
VOL_RATIO_WINDOW = 20
DAYS_YEAR = 365              # crypto trades every UTC calendar day
PCTL_WINDOW = 730            # ~2 years, the ETF page's 504 sessions in time
MOMENTUM_SKIP = 30           # the "1" in 12-1: skip the most recent month
FLAT_MA_TOLERANCE = 0.01     # |MA50/MA200 - 1| below this reads as flat
RANK_HORIZONS = (30, 91, 182, 365)   # 1M / 3M / 6M / 12M in calendar days
MIN_RANK_HORIZONS = (30, 91)         # a row missing either is unrankable
MIN_PCTL_OBS = 365           # below this, percentiles are not published
RANK_DELTA_LOOKBACK = 28     # delta-R: four weeks, the ETF page's 20 sessions

TREND_STRONG_UP = "Strong up"
TREND_UP = "Up"
TREND_RANGE = "Range"
TREND_DOWN = "Down"
TREND_STRONG_DOWN = "Strong down"


# --------------------------------------------------------------------------
# Moving averages and trend state
# --------------------------------------------------------------------------
def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average, NaN until the window fills."""
    return close.rolling(window, min_periods=window).mean()


def slope_positive(ma: pd.Series, lookback: int = SLOPE_LOOKBACK) -> pd.Series:
    """True where the MA is above its own value ``lookback`` bars ago.

    NaN-safe: the comparison is False wherever either endpoint is missing,
    so an MA that has not yet warmed up never reads as rising.
    """
    return (ma - ma.shift(lookback)) > 0


def trend_state(close: pd.Series) -> str | None:
    """Discrete trend badge for the latest bar.

    Returns None until MA200 AND its 20-bar slope have warmed up (220 bars):
    the caller shows "—" rather than a badge on a partial window. Checking
    MA200 alone (the ETF module's rule) lets bars 200-219 read the missing
    slope as "not rising" and badge a coin Strong down by construction.
    """
    ma50 = sma(close, MA_MID)
    ma200 = sma(close, MA_LONG)
    up50 = slope_positive(ma50)
    up200 = slope_positive(ma200)

    c = close.iloc[-1]
    m50 = ma50.iloc[-1]
    m200 = ma200.iloc[-1]
    if not np.isfinite(c) or not np.isfinite(m50) or not np.isfinite(m200):
        return None
    if len(ma200) <= SLOPE_LOOKBACK or not np.isfinite(ma200.iloc[-1 - SLOPE_LOOKBACK]):
        return None

    rising = bool(up50.iloc[-1]) and bool(up200.iloc[-1])
    falling = not bool(up50.iloc[-1]) and not bool(up200.iloc[-1])

    # Precedence per convention 2 in the module docstring.
    if c > m50 > m200 and rising:
        return TREND_STRONG_UP
    if c < m50 < m200 and falling:
        return TREND_STRONG_DOWN
    flat_mas = abs(m50 / m200 - 1.0) < FLAT_MA_TOLERANCE
    between_mas = min(m50, m200) <= c <= max(m50, m200)
    if flat_mas or between_mas:
        return TREND_RANGE
    return TREND_UP if c > m200 else TREND_DOWN


def dev_from_ma(close: pd.Series, window: int = MA_LONG) -> float:
    """C / MA(window) - 1 for the latest bar ("Dev 200D")."""
    ma = sma(close, window)
    if not np.isfinite(ma.iloc[-1]):
        return float("nan")
    return float(close.iloc[-1] / ma.iloc[-1] - 1.0)


# --------------------------------------------------------------------------
# Returns and momentum
# --------------------------------------------------------------------------
def total_return(close: pd.Series, lookback: int) -> float:
    """P(t)/P(t-lookback) - 1, or NaN when the history is too short."""
    if len(close) <= lookback:
        return float("nan")
    prior = close.iloc[-1 - lookback]
    latest = close.iloc[-1]
    if not np.isfinite(prior) or not np.isfinite(latest) or prior == 0:
        return float("nan")
    return float(latest / prior - 1.0)


def momentum_12_1(close: pd.Series) -> float:
    """P(t-30)/P(t-365) - 1 — twelve-month return, last month skipped."""
    if len(close) <= DAYS_YEAR:
        return float("nan")
    recent = close.iloc[-1 - MOMENTUM_SKIP]
    old = close.iloc[-1 - DAYS_YEAR]
    if not np.isfinite(recent) or not np.isfinite(old) or old == 0:
        return float("nan")
    return float(recent / old - 1.0)


def vs_52w_high(close: pd.Series) -> float:
    """C(t) / max(C over trailing 365 days) - 1, close basis.

    Zero means a fresh closing high; never positive, because the current
    close is inside the window it is measured against.
    """
    window = close.iloc[-DAYS_YEAR:].dropna()
    if window.empty:
        return float("nan")
    peak = window.max()
    if not np.isfinite(peak) or peak == 0:
        return float("nan")
    return float(close.iloc[-1] / peak - 1.0)


def relative_strength_1m(
    close: pd.Series, benchmark: pd.Series, lookback: int = MOMENTUM_SKIP
) -> float:
    """Log-return spread versus the benchmark over ``lookback`` days.

    ln(P_coin,t / P_coin,t-n) - ln(P_bm,t / P_bm,t-n), in return units.
    The benchmark is BTC for every row. With a single benchmark this is the
    row's own 1M log return shifted by one constant, which is why
    ``run_scanner`` also emits the raw 1M return and the page can switch.

    The two series are aligned on DATE, not on position. Every live coin
    shares one UTC calendar, so they normally coincide; a coin that stopped
    updating would otherwise be compared against a different month of BTC.
    """
    if len(close) <= lookback:
        return float("nan")
    end = close.index[-1]
    start = close.index[-1 - lookback]
    if end not in benchmark.index or start not in benchmark.index:
        return float("nan")
    coin = np.log(close.iloc[-1] / close.iloc[-1 - lookback])
    bm = np.log(benchmark.loc[end] / benchmark.loc[start])
    if not np.isfinite(coin) or not np.isfinite(bm):
        return float("nan")
    return float(coin - bm)


# --------------------------------------------------------------------------
# Risk: realised vol, Bollinger bandwidth, ATR
# --------------------------------------------------------------------------
def realised_vol(close: pd.Series, window: int = RV_WINDOW) -> pd.Series:
    """Annualised std of daily log returns, sqrt(365) for a 24/7 market."""
    logret = np.log(close / close.shift(1))
    return logret.rolling(window, min_periods=window).std(ddof=1) * np.sqrt(DAYS_YEAR)


def bollinger_bandwidth(
    close: pd.Series, window: int = BBW_WINDOW, sigma: float = BBW_SIGMA
) -> pd.Series:
    """(2 * sigma * sd_of_price_levels) / MA(window) — standard Bollinger
    bandwidth on price levels, normalised by the mean."""
    sd = close.rolling(window, min_periods=window).std(ddof=1)
    mid = close.rolling(window, min_periods=window).mean()
    return (2.0 * sigma * sd) / mid


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """max(H-L, |H-C_prev|, |L-C_prev|). First bar is NaN (no prior close)."""
    prev_close = close.shift(1)
    a = high - low
    b = (high - prev_close).abs()
    c = (low - prev_close).abs()
    tr = pd.concat([a, b, c], axis=1).max(axis=1)
    tr.iloc[0] = np.nan
    return tr


def wilder_smooth(values: pd.Series, period: int) -> pd.Series:
    """Wilder's recursive smoothing, seeded by the first simple mean.

    seed = mean(first ``period`` valid observations); thereafter
    s(t) = (s(t-1) * (period - 1) + x(t)) / period. Shared by ATR and RSI.
    """
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.dropna()
    if len(valid) < period:
        return out
    seed_idx = valid.index[period - 1]
    prev = float(valid.iloc[:period].mean())
    out.loc[seed_idx] = prev
    for idx, x in valid.loc[valid.index > seed_idx].items():
        prev = (prev * (period - 1) + float(x)) / period
        out.loc[idx] = prev
    return out


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD
) -> pd.Series:
    """Average True Range, Wilder-smoothed."""
    return wilder_smooth(true_range(high, low, close), period)


def atr_pct(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = ATR_PERIOD
) -> float:
    """ATR / C for the latest bar, as a fraction (the page renders a %)."""
    a = atr(high, low, close, period)
    if not np.isfinite(a.iloc[-1]) or close.iloc[-1] == 0:
        return float("nan")
    return float(a.iloc[-1] / close.iloc[-1])


def rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder RSI.

    Degenerate cases, ordered so the both-zero case cannot fall through to
    a directional answer: no movement reads 50, gains-only 100, losses-only
    0. ``naive_rsi_latest`` implements the same order.
    """
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)
    avg_gain = wilder_smooth(gains, period)
    avg_loss = wilder_smooth(losses, period)
    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    flat = (avg_gain == 0.0) & (avg_loss == 0.0)
    return (
        out.mask(avg_loss == 0.0, 100.0)
        .mask(avg_gain == 0.0, 0.0)
        .mask(flat, 50.0)
    )


def volume_ratio(volume: pd.Series, window: int = VOL_RATIO_WINDOW) -> float:
    """V(t) / SMA(V, window) for the latest bar."""
    avg = volume.rolling(window, min_periods=window).mean()
    if not np.isfinite(avg.iloc[-1]) or avg.iloc[-1] == 0:
        return float("nan")
    return float(volume.iloc[-1] / avg.iloc[-1])


# --------------------------------------------------------------------------
# Percentiles
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Percentile:
    """A percentile reading plus the window it was computed on.

    ``truncated`` drives the "^" marker on the page: publishable, but the
    window is shorter than the frozen 730 days.
    """

    value: float
    n_obs: int
    truncated: bool


def percentile_of_score(window: np.ndarray, value: float) -> float:
    """Mean percentile-of-score: strictly-below plus half the ties, x100."""
    arr = np.asarray(window, dtype="float64")
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or not np.isfinite(value):
        return float("nan")
    below = float((arr < value).sum())
    equal = float((arr == value).sum())
    return 100.0 * (below + 0.5 * equal) / arr.size


def percentile_of_latest(
    series: pd.Series, window: int = PCTL_WINDOW, min_obs: int = MIN_PCTL_OBS
) -> Percentile:
    """Percentile of the latest value within its own trailing window.

    The value ranked is the series' LAST element, not its last non-null
    one: dropping back to the most recent valid observation would publish a
    confident percentile for a bar that is not current. Below ``min_obs``
    the reading is withheld (NaN).
    """
    if series.empty:
        return Percentile(float("nan"), 0, False)
    current = float(series.iloc[-1])
    if not np.isfinite(current):
        return Percentile(float("nan"), 0, False)
    tail = series.dropna().iloc[-window:]
    n = int(tail.size)
    if n < min_obs:
        return Percentile(float("nan"), n, True)
    return Percentile(percentile_of_score(tail.to_numpy(), current), n, n < window)


# --------------------------------------------------------------------------
# Cross-sectional rank
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RankResult:
    """Composite ranks plus the diagnostics the guard layer asserts on."""

    ranks: pd.Series           # coin -> 1..N, 1 = strongest
    scores: pd.Series          # coin -> mean cross-sectional percentile
    truncated: pd.Series       # coin -> True if any horizon was missing
    unrankable: list[str]      # coins dropped for lacking short horizons


def rank_from_horizon_returns(returns: pd.DataFrame) -> RankResult:
    """Rank a cross-section from a coin x horizon table of total returns.

    Per-horizon returns become cross-sectional percentiles before
    averaging, so one wild horizon cannot dominate the composite the way
    raw-return averaging would let it — which matters more in crypto, where
    a single 12-month return can be several thousand percent.
    """
    pctiles = returns.rank(pct=True, na_option="keep")

    have_short = pctiles[list(MIN_RANK_HORIZONS)].notna().all(axis=1)
    unrankable = sorted(pctiles.index[~have_short].tolist())

    scores = pctiles.loc[have_short].mean(axis=1, skipna=True)
    truncated = pctiles.loc[have_short].isna().any(axis=1)

    ranks = scores.rank(ascending=False, method="first").astype("int64")
    return RankResult(
        ranks=ranks.sort_values(),
        scores=scores,
        truncated=truncated,
        unrankable=unrankable,
    )


# --------------------------------------------------------------------------
# Reference implementations — the per-build divergence guard
#
# Deliberately naive: plain Python loops, no pandas rolling. run_scanner
# calls these on a rotating sample of coins each build and compares against
# the vectorised path above, to catch a rolling-window or alignment
# regression that changes every number silently.
# --------------------------------------------------------------------------
def naive_sma_latest(values: list[float], window: int) -> float:
    """Mean of the last ``window`` values, by explicit accumulation."""
    if len(values) < window:
        return float("nan")
    total = 0.0
    for v in values[-window:]:
        total += v
    return total / window


def naive_rsi_latest(values: list[float], period: int = RSI_PERIOD) -> float:
    """Wilder RSI of the final value, by explicit iteration."""
    if len(values) < period + 1:
        return float("nan")
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(change if change > 0 else 0.0)
        losses.append(-change if change < 0 else 0.0)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    if avg_gain == 0.0:
        return 0.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def naive_atr_pct_latest(
    highs: list[float], lows: list[float], closes: list[float], period: int = ATR_PERIOD
) -> float:
    """Wilder ATR / last close, by explicit iteration."""
    if len(closes) < period + 1:
        return float("nan")
    trs: list[float] = []
    for i in range(1, len(closes)):
        trs.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    if closes[-1] == 0:
        return float("nan")
    return a / closes[-1]
