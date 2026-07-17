"""
walk_forward.py
---------------
Year-by-year forward performance of the production strategy.

The production strategy (backtest.py) has no IS-tuned parameters — they are
hard-coded defaults — so 'walk-forward' here is the equivalent diagnostic:
how stable is the strategy's edge across non-overlapping forward years?

Outputs:
  - Per-calendar-year CAGR / Sharpe / MaxDD for strategy and benchmarks
  - Cumulative-from-year-N forward Sharpes
  - Rolling 252-day Sharpe time series

This is the "six forward observations" diagnostic asked for as a remedy to
the original single IS/OOS cut.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    Params, PRICES_PATH,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    benchmark_hodl, benchmark_equal_weight, benchmark_60_40_btc_eth,
    summary_stats,
)


def run_production(close: pd.DataFrame, volume: pd.DataFrame, p: Params) -> dict:
    """Reproduce the production pipeline end-to-end."""
    if p.use_liquidity_gate:
        mask = investability_mask_liquidity(
            close, volume,
            lookback_d=p.liquidity_lookback_d,
            min_adv_usd=p.liquidity_min_adv_usd,
            min_history_days=p.liquidity_min_history_days,
        )
    else:
        mask = (close.notna())
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    target_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry_trend = (
        per_coin_trend_entry_mask(close, p.per_coin_trend_window)
        if p.use_per_coin_trend else None
    )
    mom = momentum_score(close, p.momentum_lookbacks_d, mask)
    if entry_trend is not None:
        mom = mom.where(entry_trend)
    weights_rank = rank_top_n(mom, p.rank_top_n)
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday,
                                    single_name_cap=p.single_name_cap)
    exit_mask = (
        per_coin_trend_exit_mask(close, p.per_coin_trend_window)
        if p.use_daily_trend_exit else None
    )
    return run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )


def per_year_stats(eq: pd.Series, years: list[int]) -> pd.DataFrame:
    rows = []
    for y in years:
        sub = eq.loc[f"{y}-01-01":f"{y}-12-31"]
        if len(sub) < 20:
            continue
        s = summary_stats(sub)
        rows.append({
            "year": y, "n_obs": len(sub),
            "CAGR": s["cagr"], "Sharpe": s["sharpe"], "MaxDD": s["max_dd"],
        })
    return pd.DataFrame(rows).set_index("year")


def cumulative_forward_sharpe(eq: pd.Series, anchor_years: list[int]) -> pd.DataFrame:
    """For each anchor year, compute the Sharpe over (anchor_year-01-01, end_of_sample)."""
    rows = []
    end = eq.index[-1]
    for y in anchor_years:
        start = pd.Timestamp(f"{y}-01-01")
        if start >= end:
            continue
        sub = eq.loc[start:end]
        if len(sub) < 30:
            continue
        s = summary_stats(sub)
        rows.append({
            "from_year": y,
            "to": end.date().isoformat(),
            "n_years": (end - start).days / 365.25,
            "CAGR": s["cagr"], "Sharpe": s["sharpe"], "MaxDD": s["max_dd"],
        })
    return pd.DataFrame(rows).set_index("from_year")


def main() -> int:
    p = Params()
    print("Loading prices and running production strategy ...")
    close, volume = load_prices(PRICES_PATH)
    result = run_production(close, volume, p)
    eq = result["equity"]

    print("Building benchmarks ...")
    bm_btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    if p.use_liquidity_gate:
        mask = investability_mask_liquidity(
            close, volume,
            lookback_d=p.liquidity_lookback_d,
            min_adv_usd=p.liquidity_min_adv_usd,
            min_history_days=p.liquidity_min_history_days,
        )
    else:
        mask = close.notna()
    bm_ew = benchmark_equal_weight(close, mask).reindex(eq.index).ffill()
    bm_6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    years = list(range(2018, 2027))

    # --------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("PER-YEAR FORWARD PERFORMANCE")
    print("=" * 78)
    print("\nStrategy:")
    print(per_year_stats(eq, years).to_string(
        float_format=lambda x: f"{x:>7.1%}" if abs(x) < 100 else f"{x:>7.1f}"))

    print("\nBTC HODL:")
    print(per_year_stats(bm_btc, years).to_string(
        float_format=lambda x: f"{x:>7.1%}" if abs(x) < 100 else f"{x:>7.1f}"))

    print("\nEqual-weight investable (rolling liquidity):")
    print(per_year_stats(bm_ew, years).to_string(
        float_format=lambda x: f"{x:>7.1%}" if abs(x) < 100 else f"{x:>7.1f}"))

    print("\n60/40 BTC/ETH:")
    print(per_year_stats(bm_6040, years).to_string(
        float_format=lambda x: f"{x:>7.1%}" if abs(x) < 100 else f"{x:>7.1f}"))

    # --------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("FORWARD-FROM-YEAR-N: CUMULATIVE PERFORMANCE")
    print("(everything you would have made from start-of-year-N to today)")
    print("=" * 78)
    anchors = list(range(2019, 2027))
    print("\nStrategy:")
    print(cumulative_forward_sharpe(eq, anchors).to_string(
        float_format=lambda x: f"{x:>7.2f}" if abs(x) < 10 else f"{x:>7.1f}"))
    print("\n60/40 BTC/ETH:")
    print(cumulative_forward_sharpe(bm_6040, anchors).to_string(
        float_format=lambda x: f"{x:>7.2f}" if abs(x) < 10 else f"{x:>7.1f}"))

    # --------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("ROLLING 252d SHARPE  (every quarter-end shown)")
    print("=" * 78)
    daily_ret = eq.pct_change().fillna(0.0)
    roll_mean = daily_ret.rolling(252).mean() * 365
    roll_vol = daily_ret.rolling(252).std() * np.sqrt(365)
    rolling_sharpe = (roll_mean / roll_vol).dropna()
    # Sample at quarter-ends
    q_idx = rolling_sharpe.resample("QE").last().dropna()
    print()
    print(f"  {'date':<12} {'rolling 252d Sharpe':>22}")
    for d, v in q_idx.items():
        print(f"  {d.date()!s:<12} {v:>22.2f}")

    summary_stat_rs = {
        "min": rolling_sharpe.min(),
        "25%": rolling_sharpe.quantile(0.25),
        "median": rolling_sharpe.median(),
        "75%": rolling_sharpe.quantile(0.75),
        "max": rolling_sharpe.max(),
        "% positive": (rolling_sharpe > 0).mean(),
        "% > 1.0": (rolling_sharpe > 1.0).mean(),
    }
    print("\n  Rolling 252d Sharpe distribution:")
    for k, v in summary_stat_rs.items():
        suffix = "%" if "%" in k else ""
        print(f"    {k:<12} {v:>6.2f}{suffix}")

    # --------------------------------------------------------------------
    print("\n" + "=" * 78)
    print("BOOTSTRAP CONFIDENCE INTERVAL ON SHARPE  (block bootstrap)")
    print("=" * 78)
    daily = daily_ret.dropna().values
    n = len(daily)
    block_size = 21  # ~1 month blocks
    n_blocks_per_sample = n // block_size
    rng = np.random.default_rng(seed=42)
    boot_sharpes = []
    n_boot = 2000
    for _ in range(n_boot):
        starts = rng.integers(0, n - block_size, size=n_blocks_per_sample)
        sample = np.concatenate([daily[s:s + block_size] for s in starts])
        mean = sample.mean() * 365
        vol = sample.std() * np.sqrt(365)
        boot_sharpes.append(mean / vol if vol > 0 else 0.0)
    boot_sharpes = np.array(boot_sharpes)
    print(f"\n  {n_boot} bootstrap resamples, block size = {block_size} days")
    print(f"  Sharpe distribution:")
    for q in [0.05, 0.25, 0.50, 0.75, 0.95]:
        print(f"    p{int(q*100):<3}: {np.quantile(boot_sharpes, q):.2f}")
    print(f"  Mean: {boot_sharpes.mean():.2f}   "
          f"Std: {boot_sharpes.std():.2f}   "
          f"P(Sharpe > 0): {(boot_sharpes > 0).mean():.1%}   "
          f"P(Sharpe > 0.8): {(boot_sharpes > 0.8).mean():.1%}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
