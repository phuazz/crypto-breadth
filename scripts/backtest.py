"""
backtest.py
-----------
Breadth-gate + ranked-sizing crypto strategy backtest.

Pipeline (linear, no look-ahead):
  1. Load prices.parquet
  2. Build daily wide-format close panel
  3. Compute breadth signal: pct of investable universe > 50d MA
  4. Map breadth → regime tier → target gross exposure (graduated allocation)
  5. Compute momentum ranks across multiple lookbacks
  6. Run walk-forward backtest:
       - In-sample window: 2018-01-01 → 2020-12-31  (parameter selection)
       - Out-of-sample:    2021-01-01 → today        (untouched evaluation)
  7. Produce diagnostics:
       - Equity curve vs benchmarks (BTC HODL, top-10 equal-weight, 60/40 BTC/ETH)
       - Drawdown
       - Regime-segmented Sharpe (2018 bear / 2019 / 2020-21 / 2022 / 2023-25)
       - Entry-point overlay (flat-or-negative 60d windows)
       - Turnover and fee drag
       - Vs-benchmark summary table

Hygiene:
  - Signals lagged by 1 bar. The weekly rebalance row is the Monday close
    (rebalance_weekday=0) computed on data through Monday close; the
    `shift(lag_days=1)` in run_backtest moves those target weights to
    Tuesday's row, so the trade is realised at Tuesday's close. Daily
    trend-exit overrides apply the same 1-bar lag: a close-below-MA at
    end of day T forces a sale at the close of T+1. No same-bar execution.
  - Fees: 10 bps per side (Binance VIP 0 spot).
  - Investability respected: a coin is excluded from breadth and ranking
    until its first observed Binance date.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Headless matplotlib for script use.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ----- configuration -------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = Path(__file__).resolve().parent.parent / "data"
PRICES_PATH = DATA_DIR / "prices.parquet"

# Strategy parameters (will be tuned on in-sample window only).
@dataclass
class Params:
    breadth_ma_window: int = 50           # days
    # v3.1: parameters updated after expand-window walk-forward (see
    # scripts/walk_forward_refit.py + commit 0de3ced). The IS-best config
    # was top-4 + (30, 90, 180) in all seven re-fits (2020 -> 2026), so
    # the empirical case for the change is unusually clean.
    # Prior defaults were (21, 63, 126) and rank_top_n=3.
    momentum_lookbacks_d: tuple = (30, 90, 180)   # 1m / 3m / 6m calendar days
    rank_top_n: int = 4                           # hold top-N names when "on"
    rebalance_weekday: int = 0            # 0=Monday
    fee_bps_per_side: float = 10.0

    # Graduated allocation thresholds (breadth % above MA → target exposure).
    tier_thresholds: tuple = (0.30, 0.50, 0.70)   # below/30-50/50-70/above
    tier_exposures: tuple = (0.0, 0.30, 0.60, 1.00)

    # v1: per-coin trend filter on entry (eligibility for top-N rank).
    #     A coin must be above its own MA AND its MA must be rising.
    per_coin_trend_window: int = 50
    use_per_coin_trend: bool = True

    # v2: daily-cadence per-coin trend exit (force-sell when trend breaks
    #     intra-week instead of waiting for the next weekly rebalance).
    #     Exit trigger is asymmetric — easier than entry — to avoid
    #     whipsawing on normal pullbacks. Default: close < own MA.
    use_daily_trend_exit: bool = True

    # v3: rolling-liquidity universe. A coin is investable on a given date
    #     only if it has >= min_history_days of data AND its trailing 30-day
    #     average daily $ volume exceeds min_adv_usd. Removes the survivor-
    #     bias inherent in a hand-picked fixed universe.
    use_liquidity_gate: bool = True
    liquidity_lookback_d: int = 30
    liquidity_min_adv_usd: float = 25_000_000.0
    liquidity_min_history_days: int = 90

IN_SAMPLE_END = "2020-12-31"
OUT_OF_SAMPLE_START = "2021-01-01"


# ----- data loading --------------------------------------------------------

def load_prices(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (close_wide, volume_wide) — both indexed by date, columns=symbol."""
    df = pd.read_parquet(path)
    df["date"] = pd.to_datetime(df["date"])
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    volume = df.pivot(index="date", columns="symbol", values="volume").sort_index()
    return close, volume


def investability_mask(close: pd.DataFrame) -> pd.DataFrame:
    """Simple investability: True wherever close is not NaN.

    Kept for backward-compatibility and ablation studies. Production
    pipeline uses `investability_mask_liquidity` instead — see Params.
    """
    return close.notna()


def investability_mask_liquidity(
    close: pd.DataFrame, volume: pd.DataFrame,
    *,
    lookback_d: int = 30,
    min_adv_usd: float = 25_000_000.0,
    min_history_days: int = 90,
) -> pd.DataFrame:
    """Rolling liquidity-gated investability (ex-ante deployable rule).

    A coin is investable on date T iff all three hold simultaneously:
      1. close[T] is not NaN (the pair is trading on Binance that day)
      2. The coin has at least `min_history_days` of prior observations
         (avoids picking brand-new listings on day 1 of their existence)
      3. Trailing `lookback_d`-day average daily $ volume (close * volume)
         is at least `min_adv_usd`

    The dollar-volume threshold removes coins that have shrivelled below
    "major-tier" liquidity, and lets newly-listed coins enter when they
    earn their place rather than at arbitrary universe-construction time.
    Lookback ADV uses min_periods = lookback_d // 2 so the test starts a
    little earlier than a strict full-window requirement.
    """
    has_data = close.notna()
    # Per-coin cumulative count of observed days, used for age check.
    age_days = has_data.astype(int).cumsum()
    has_history = age_days >= min_history_days
    # Daily dollar volume = close * volume (volume is in base-asset units).
    dollar_volume = close * volume
    adv = dollar_volume.rolling(
        lookback_d, min_periods=max(5, lookback_d // 2)
    ).mean()
    has_liquidity = (adv >= min_adv_usd).fillna(False)
    return (has_data & has_history & has_liquidity).fillna(False)


# ----- signals -------------------------------------------------------------

def breadth_pct_above_ma(close: pd.DataFrame, window: int, mask: pd.DataFrame) -> pd.Series:
    """Daily % of INVESTABLE universe trading above its `window`-day MA.

    Denominator is # investable on that day, not len(universe). This is the
    survivorship-bias fix: SOL has no breadth contribution before its listing.
    """
    ma = close.rolling(window=window, min_periods=window).mean()
    above = (close > ma) & mask
    n_investable = mask.sum(axis=1).replace(0, np.nan)
    # Only count "above MA" where MA itself is defined (need full window).
    n_with_ma = (ma.notna() & mask).sum(axis=1).replace(0, np.nan)
    return (above.sum(axis=1) / n_with_ma).rename("breadth_pct")


def breadth_to_tier(breadth: pd.Series, thresholds: tuple, exposures: tuple) -> pd.Series:
    """Map breadth % to target gross exposure via graduated tiers."""
    tiers = pd.Series(exposures[0], index=breadth.index, name="target_exposure")
    for i, thr in enumerate(thresholds):
        tiers = tiers.where(breadth < thr, exposures[i + 1])
    # Where breadth is NaN (early period before MA defined), force 0 exposure.
    tiers = tiers.where(breadth.notna(), 0.0)
    return tiers


def momentum_score(close: pd.DataFrame, lookbacks: tuple, mask: pd.DataFrame) -> pd.DataFrame:
    """Composite momentum score: average of risk-adjusted returns across lookbacks.

    For each lookback L:
        score_L = (close_t / close_{t-L} - 1) / rolling_std(daily_returns, L)

    Composite = mean across lookbacks. Uninvestable cells → NaN.
    """
    daily_ret = close.pct_change()
    parts = []
    for L in lookbacks:
        ret_L = close.pct_change(L)
        vol_L = daily_ret.rolling(L, min_periods=max(10, L // 2)).std() * np.sqrt(L)
        score_L = (ret_L / vol_L).where(vol_L > 0)
        parts.append(score_L)
    composite = sum(parts) / len(parts)
    return composite.where(mask)


def per_coin_trend_entry_mask(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Eligibility mask for top-N rank (entry filter).

    True iff close > own MA AND MA is rising. This is strict: a coin must
    be in a confirmed uptrend, not just bouncing off the MA. Used to gate
    momentum scores before ranking.
    """
    ma = close.rolling(window, min_periods=window).mean()
    above_ma = close > ma
    ma_rising = ma.diff() > 0
    return (above_ma & ma_rising).fillna(False)


def per_coin_trend_exit_mask(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Daily exit trigger (asymmetric, looser than entry).

    True where the trend is BROKEN for that coin on that date — currently
    defined as close < own MA. Looser than the entry filter so we do not
    whipsaw out on normal pullbacks above the MA.

    The mask is read at end-of-day T to force-sell at end-of-day T+1
    (1-bar lag, same hygiene as the rest of the pipeline).
    """
    ma = close.rolling(window, min_periods=window).mean()
    return (close < ma).fillna(False)


def rank_top_n(score: pd.DataFrame, n: int) -> pd.DataFrame:
    """For each date, weight = 1/n for top-n by score, else 0.

    Ties broken by symbol name (deterministic). NaN scores excluded from ranking.
    """
    weights = pd.DataFrame(0.0, index=score.index, columns=score.columns)
    for dt, row in score.iterrows():
        valid = row.dropna()
        if len(valid) == 0:
            continue
        # If fewer than n investable names, equal-weight whatever is available.
        actual_n = min(n, len(valid))
        top = valid.nlargest(actual_n).index
        weights.loc[dt, top] = 1.0 / actual_n
    return weights


# ----- portfolio construction & backtest -----------------------------------

def build_target_weights(
    rank_weights: pd.DataFrame,
    target_exposure: pd.Series,
    rebalance_weekday: int,
) -> pd.DataFrame:
    """Combine ranking (per-name) with gating (gross exposure), then restrict
    rebalancing to specified weekday. Between rebalances, weights drift with
    prices (handled in the backtest loop, not here)."""
    # Scale each row of ranks by that day's target exposure.
    scaled = rank_weights.mul(target_exposure, axis=0)
    # Keep only rebalance days; other days = NaN (will be forward-filled in loop).
    # pandas 3.0 no longer broadcasts a 1D bool against a DataFrame in .where(),
    # so do the mask explicitly via .loc.
    is_rebal_row = scaled.index.weekday == rebalance_weekday
    out = scaled.copy()
    out.loc[~is_rebal_row, :] = np.nan
    return out


def run_backtest(
    close: pd.DataFrame,
    target_weights_on_rebal: pd.DataFrame,
    fee_bps_per_side: float,
    lag_days: int = 1,
    daily_exit_mask: pd.DataFrame | None = None,
) -> dict:
    """Linear daily backtest with weekly rebalances and optional daily trend exits.

    On each rebalance day, target weights from the PREVIOUS bar (lag_days=1)
    are realised at that day's close. Between rebalances, position values
    drift with prices.

    `daily_exit_mask` is optional. When provided, it is a DataFrame (dates x
    symbols) of booleans where True at (T, j) means "trend broken for coin j
    by end of day T." With the standard 1-bar lag, this triggers a force-sell
    of coin j at end of day T+1 (before any weekly rebalance on the same day).

    Returns dict with equity curve, daily returns, turnover, fees paid.
    """
    daily_ret = close.pct_change().fillna(0.0)
    dates = close.index
    symbols = close.columns

    # Lag the rebalance signal by `lag_days` (we observe signal on T, trade on T+lag).
    rebal_lagged = target_weights_on_rebal.shift(lag_days)
    # Same lag applied to the daily exit mask, when present.
    exit_lagged = (
        daily_exit_mask.shift(lag_days) if daily_exit_mask is not None else None
    )

    weights = pd.DataFrame(0.0, index=dates, columns=symbols)
    cash_weight = pd.Series(1.0, index=dates)
    turnover = pd.Series(0.0, index=dates)
    fee_drag = pd.Series(0.0, index=dates)
    equity = pd.Series(1.0, index=dates)
    daily_exit_count = pd.Series(0, index=dates)
    rebal_executed = pd.Series(False, index=dates)

    current_w = pd.Series(0.0, index=symbols)
    current_cash = 1.0

    for i, dt in enumerate(dates):
        # Drift positions with today's returns BEFORE any rebalance.
        # (Cash stays at 1x; risky positions move with daily_ret.)
        if i > 0:
            r = daily_ret.iloc[i].fillna(0.0)
            new_values = current_w * (1.0 + r)
            current_w = new_values
            # Cash unchanged (assume 0% on idle USDT for simplicity).

        # Normalise: total portfolio value = sum(risky) + cash.
        gross = current_w.sum() + current_cash
        if gross > 0:
            current_w = current_w / gross
            current_cash = current_cash / gross
        equity.iloc[i] = equity.iloc[i - 1] * gross if i > 0 else 1.0

        # Daily trend-exit check (runs BEFORE any weekly rebalance on the
        # same day so the rebal target accounts for the post-exit weights).
        if exit_lagged is not None and dt in exit_lagged.index:
            exits_today = exit_lagged.loc[dt]
            if isinstance(exits_today, pd.Series) and exits_today.any():
                # Align mask to symbol index; force-sell coins where exit is
                # flagged AND we currently hold a positive weight.
                exits_aligned = exits_today.reindex(symbols, fill_value=False).astype(bool)
                holding = current_w > 0
                to_exit = exits_aligned & holding
                if to_exit.any():
                    sold = current_w[to_exit].sum()
                    turnover.iloc[i] += sold
                    fee = sold * (fee_bps_per_side / 10_000.0)
                    fee_drag.iloc[i] += fee
                    equity.iloc[i] = equity.iloc[i] * (1.0 - fee)
                    current_cash += sold
                    current_w.loc[to_exit] = 0.0
                    daily_exit_count.iloc[i] = int(to_exit.sum())

        # Rebalance if we have a target for today.
        if dt in rebal_lagged.index and rebal_lagged.loc[dt].notna().any():
            target = rebal_lagged.loc[dt].fillna(0.0)
            target_cash = 1.0 - target.sum()
            # Turnover = sum of absolute weight changes (one-sided).
            trades = (target - current_w).abs().sum()
            turnover.iloc[i] += trades
            fees = trades * (fee_bps_per_side / 10_000.0)
            fee_drag.iloc[i] += fees
            equity.iloc[i] = equity.iloc[i] * (1.0 - fees)
            current_w = target.copy()
            current_cash = max(0.0, target_cash)
            rebal_executed.iloc[i] = True

        weights.iloc[i] = current_w

    daily_strat_ret = equity.pct_change().fillna(0.0)

    return {
        "equity": equity,
        "weights": weights,
        "daily_ret": daily_strat_ret,
        "turnover": turnover,
        "fee_drag": fee_drag,
        "daily_exit_count": daily_exit_count,
        "rebal_executed": rebal_executed,
    }


# ----- benchmarks ----------------------------------------------------------

def benchmark_hodl(close: pd.DataFrame, symbol: str) -> pd.Series:
    """Single-asset buy-and-hold equity curve, base 1.0 on first valid date."""
    px = close[symbol].dropna()
    return (px / px.iloc[0]).rename(f"hodl_{symbol}")


def benchmark_equal_weight(close: pd.DataFrame, mask: pd.DataFrame, fee_bps_per_side: float = 10.0) -> pd.Series:
    """Equal-weight investable universe, monthly rebalance, with fees."""
    daily_ret = close.pct_change().fillna(0.0)
    # Investable count per day.
    n_inv = mask.sum(axis=1).replace(0, np.nan)
    # Equal weight where investable.
    target_w = mask.div(n_inv, axis=0).fillna(0.0)
    # Rebalance monthly on the first observed day of each calendar month.
    # The previous Period.diff/Timedelta chain mis-typed under pandas 3.0
    # (overflow on scalar multiply) and produced an unreliable mask. Since
    # the panel is daily-continuous, is_month_start is the exact rebal flag.
    is_rebal = pd.Series(close.index.is_month_start, index=close.index)
    is_rebal.iloc[0] = True

    equity = pd.Series(1.0, index=close.index)
    current_w = pd.Series(0.0, index=close.columns)
    for i, dt in enumerate(close.index):
        if i > 0:
            r = daily_ret.iloc[i].fillna(0.0)
            new_values = current_w * (1.0 + r)
            gross = new_values.sum()
            equity.iloc[i] = equity.iloc[i - 1] * (1.0 + (current_w * r).sum())
            current_w = new_values / gross if gross > 0 else new_values
        if is_rebal.iloc[i]:
            target = target_w.iloc[i]
            trades = (target - current_w).abs().sum()
            fees = trades * (fee_bps_per_side / 10_000.0)
            equity.iloc[i] = equity.iloc[i] * (1.0 - fees)
            current_w = target.copy()
    return equity.rename("equal_weight_top10")


def benchmark_60_40_btc_eth(close: pd.DataFrame) -> pd.Series:
    """60% BTC / 40% ETH, monthly rebalance, with fees."""
    sub = close[["BTC", "ETH"]].dropna()
    daily_ret = sub.pct_change().fillna(0.0)
    target_w = pd.Series({"BTC": 0.60, "ETH": 0.40})
    equity = pd.Series(1.0, index=sub.index)
    current_w = pd.Series(0.0, index=sub.columns)
    # Same fix as in benchmark_equal_weight: use is_month_start for monthly rebals.
    is_rebal = pd.Series(sub.index.is_month_start, index=sub.index)
    is_rebal.iloc[0] = True
    for i, dt in enumerate(sub.index):
        if i > 0:
            r = daily_ret.iloc[i]
            equity.iloc[i] = equity.iloc[i - 1] * (1.0 + (current_w * r).sum())
            new_values = current_w * (1.0 + r)
            gross = new_values.sum()
            current_w = new_values / gross if gross > 0 else new_values
        if is_rebal.iloc[i]:
            trades = (target_w - current_w).abs().sum()
            equity.iloc[i] = equity.iloc[i] * (1.0 - trades * 10.0 / 10_000.0)
            current_w = target_w.copy()
    return equity.rename("60_40_btc_eth").reindex(close.index).ffill().fillna(1.0)


# ----- diagnostics ---------------------------------------------------------

def summary_stats(equity: pd.Series, freq: int = 365) -> dict:
    ret = equity.pct_change().dropna()
    if len(ret) < 2:
        return {"cagr": np.nan, "vol": np.nan, "sharpe": np.nan, "max_dd": np.nan}
    n_years = (equity.index[-1] - equity.index[0]).days / 365.25
    # CAGR must use the wealth RATIO from start to end of the slice.
    # Using equity.iloc[-1] alone is only correct when the slice begins at 1.0
    # and silently misreports every sub-window.
    if n_years > 0 and equity.iloc[0] > 0:
        cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / n_years) - 1.0
    else:
        cagr = np.nan
    vol = ret.std() * np.sqrt(freq)
    sharpe = (ret.mean() * freq) / vol if vol > 0 else np.nan
    dd = (equity / equity.cummax() - 1.0).min()
    return {"cagr": cagr, "vol": vol, "sharpe": sharpe, "max_dd": dd}


def regime_segments() -> list[tuple[str, str, str]]:
    """Hard-coded crypto regime windows for segmented reporting."""
    return [
        ("2018_bear", "2018-01-01", "2018-12-31"),
        ("2019_recovery", "2019-01-01", "2019-12-31"),
        ("2020_21_bull", "2020-01-01", "2021-12-31"),
        ("2022_bear", "2022-01-01", "2022-12-31"),
        ("2023_25_recovery", "2023-01-01", "2025-12-31"),
        ("2026_ytd", "2026-01-01", "2030-12-31"),
    ]


def entry_point_overlay(equity: pd.Series, flat_days: int = 60) -> pd.Series:
    """Mark dates where strategy has been flat-or-negative for >= flat_days.

    These are the realistic deployment points. Returns a boolean Series.
    """
    rolling_max = equity.rolling(flat_days, min_periods=flat_days).max()
    is_flat = equity <= rolling_max.shift(0)  # at or below the recent max
    # Stricter: equity today <= equity `flat_days` ago.
    is_flat_strict = equity <= equity.shift(flat_days)
    return is_flat_strict.fillna(False)


# ----- plotting ------------------------------------------------------------

def plot_diagnostics(
    strat_equity: pd.Series,
    benchmarks: dict[str, pd.Series],
    entry_points: pd.Series,
    out_path: Path,
) -> None:
    fig, axes = plt.subplots(3, 1, figsize=(12, 12), sharex=True)

    # 1. Equity curves on log scale.
    ax = axes[0]
    ax.plot(strat_equity.index, strat_equity.values, label="Strategy", linewidth=1.6, color="#111")
    for name, eq in benchmarks.items():
        ax.plot(eq.index, eq.values, label=name, linewidth=1.0, alpha=0.7)
    ax.set_yscale("log")
    ax.set_title("Equity curves (log scale)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)

    # 2. Drawdown.
    ax = axes[1]
    dd = strat_equity / strat_equity.cummax() - 1.0
    ax.fill_between(dd.index, dd.values, 0, color="#c0392b", alpha=0.4)
    ax.plot(dd.index, dd.values, color="#c0392b", linewidth=0.8)
    ax.set_title("Strategy drawdown")
    ax.set_ylabel("DD")
    ax.grid(True, alpha=0.3)

    # 3. Entry-point overlay: highlight flat-or-negative 60d windows.
    ax = axes[2]
    ax.plot(strat_equity.index, strat_equity.values, color="#111", linewidth=1.2)
    ax.fill_between(
        strat_equity.index, 0, strat_equity.max() * 1.05,
        where=entry_points.values, color="#27ae60", alpha=0.15,
        label="Realistic deploy windows (flat 60d+)",
    )
    ax.set_yscale("log")
    ax.set_title("Entry-point overlay")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=110)
    plt.close()


# ----- main pipeline -------------------------------------------------------

def main() -> int:
    p = Params()

    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)
    if p.use_liquidity_gate:
        mask = investability_mask_liquidity(
            close, volume,
            lookback_d=p.liquidity_lookback_d,
            min_adv_usd=p.liquidity_min_adv_usd,
            min_history_days=p.liquidity_min_history_days,
        )
        gate_desc = (f"liquidity-gated: ADV >= ${p.liquidity_min_adv_usd / 1e6:.0f}M "
                     f"over {p.liquidity_lookback_d}d, history >= {p.liquidity_min_history_days}d")
    else:
        mask = investability_mask(close)
        gate_desc = "simple (close.notna)"
    print(f"  panel: {close.shape[0]} dates × {close.shape[1]} symbols")
    print(f"  candidate universe: {list(close.columns)}")
    print(f"  investability rule: {gate_desc}")
    print(f"  date range: {close.index.min().date()} → {close.index.max().date()}")
    # Diagnostic: # investable per year-end
    print("  investable count over time:")
    for y in [2018, 2020, 2022, 2024, 2026]:
        candidates = mask.index[mask.index.year == y]
        if len(candidates) > 0:
            d = candidates[len(candidates) // 2]
            n = int(mask.loc[d].sum())
            inv_names = sorted(mask.columns[mask.loc[d]].tolist())
            print(f"    {d.date()}: {n} investable -> {inv_names}")

    print("\nComputing breadth ...")
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    target_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    print(f"  breadth pct distribution: "
          f"min={breadth.min():.2f}, median={breadth.median():.2f}, max={breadth.max():.2f}")
    print(f"  target exposure distribution: "
          f"mean={target_exposure.mean():.2f}, "
          f"pct_full_risk={(target_exposure == 1.0).mean():.2%}, "
          f"pct_cash={(target_exposure == 0.0).mean():.2%}")

    # v1: per-coin trend entry filter — eligibility for top-N rank.
    if p.use_per_coin_trend:
        entry_trend = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
        print(f"\nPer-coin trend entry filter: median % eligible across universe = "
              f"{entry_trend.mean(axis=1).median():.1%}")
    else:
        entry_trend = None

    print("\nComputing momentum ranks ...")
    mom = momentum_score(close, p.momentum_lookbacks_d, mask)
    if entry_trend is not None:
        mom = mom.where(entry_trend)
    weights_rank = rank_top_n(mom, p.rank_top_n)

    print("\nBuilding target weights (weekly rebalance on Mon) ...")
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday)

    # v2: daily-cadence per-coin trend exit (lagged 1 bar inside run_backtest).
    if p.use_daily_trend_exit:
        exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
        print(f"Daily trend exit mask: median % universe in exit state = "
              f"{exit_mask.mean(axis=1).median():.1%}")
    else:
        exit_mask = None

    print("\nRunning backtest ...")
    result = run_backtest(
        close, target_w, p.fee_bps_per_side,
        lag_days=1, daily_exit_mask=exit_mask,
    )
    eq = result["equity"]
    print(f"  final equity: {eq.iloc[-1]:.2f}x")
    print(f"  total fees paid: {result['fee_drag'].sum():.4f} ({result['fee_drag'].sum() * 100:.2f}%)")
    print(f"  avg annual turnover: {result['turnover'].sum() / ((eq.index[-1] - eq.index[0]).days / 365.25):.2f}x")
    if "daily_exit_count" in result:
        n_exits = int(result["daily_exit_count"].sum())
        n_exit_days = int((result["daily_exit_count"] > 0).sum())
        print(f"  daily trend exits fired: {n_exits} total over {n_exit_days} days "
              f"(avg {n_exits / max(n_exit_days, 1):.2f} coins per exit day)")

    print("\nBuilding benchmarks ...")
    bm_btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    bm_ew = benchmark_equal_weight(close, mask)
    bm_6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    print("\n=== Full-sample stats ===")
    print(f"{'series':<20} {'CAGR':>8} {'Vol':>8} {'Sharpe':>8} {'MaxDD':>8}")
    for name, series in [
        ("strategy", eq),
        ("btc_hodl", bm_btc),
        ("equal_weight_top10", bm_ew),
        ("60_40_btc_eth", bm_6040),
    ]:
        s = summary_stats(series)
        print(f"{name:<20} {s['cagr']:>7.1%} {s['vol']:>7.1%} {s['sharpe']:>7.2f} {s['max_dd']:>7.1%}")

    print("\n=== Regime-segmented strategy Sharpe ===")
    for label, start, end in regime_segments():
        sub = eq.loc[start:end]
        if len(sub) < 30:
            continue
        s = summary_stats(sub)
        print(f"  {label:<20} {start} → {end[:7]}  "
              f"CAGR={s['cagr']:>6.1%}  Sharpe={s['sharpe']:>5.2f}  MaxDD={s['max_dd']:>6.1%}")

    print("\n=== In-sample vs Out-of-sample (strategy and benchmarks) ===")
    is_slice = (None, IN_SAMPLE_END)
    oos_slice = (OUT_OF_SAMPLE_START, None)
    all_series = [
        ("strategy", eq),
        ("btc_hodl", bm_btc),
        ("equal_weight_top10", bm_ew),
        ("60_40_btc_eth", bm_6040),
    ]
    for slice_label, (start, end) in [
        ("in_sample_2018_2020", is_slice),
        ("out_of_sample_2021_now", oos_slice),
    ]:
        print(f"\n  [{slice_label}]  {start or 'start'} -> {end or 'end'}")
        for name, series in all_series:
            sub = series.loc[start:end] if (start or end) else series
            s = summary_stats(sub)
            print(f"    {name:<20} CAGR={s['cagr']:>7.1%}  "
                  f"Sharpe={s['sharpe']:>5.2f}  MaxDD={s['max_dd']:>6.1%}")

    print("\n=== Entry-point overlay ===")
    ep = entry_point_overlay(eq, flat_days=60)
    n_ep = ep.sum()
    print(f"  {n_ep} days flagged as realistic deploy windows ({n_ep / len(eq):.1%} of sample)")
    # Forward 12m returns from deploy dates.
    fwd_12m = eq.shift(-365) / eq - 1.0
    deploy_returns = fwd_12m[ep].dropna()
    if len(deploy_returns) > 0:
        print(f"  forward 12m return from deploy points: "
              f"median={deploy_returns.median():.1%}, "
              f"mean={deploy_returns.mean():.1%}, "
              f"hit_rate>0={(deploy_returns > 0).mean():.1%}")

    # Save outputs.
    print("\nWriting outputs ...")
    out_eq = pd.DataFrame({
        "strategy": eq,
        "btc_hodl": bm_btc,
        "equal_weight_top10": bm_ew,
        "60_40_btc_eth": bm_6040,
        "breadth_pct": breadth,
        "target_exposure": target_exposure,
        "entry_point": ep.astype(int),
    })
    out_eq.to_parquet(OUT_DIR / "backtest_equity.parquet")

    plot_diagnostics(
        eq,
        {"BTC HODL": bm_btc, "Top10 EW": bm_ew, "60/40 BTC/ETH": bm_6040},
        ep,
        OUT_DIR / "backtest_diagnostics.png",
    )
    print(f"  wrote {OUT_DIR / 'backtest_equity.parquet'}")
    print(f"  wrote {OUT_DIR / 'backtest_diagnostics.png'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
