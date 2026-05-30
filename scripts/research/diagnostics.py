"""
diagnostics.py
--------------
Three robustness diagnostics on the v0 strategy. This script DOES NOT modify
the production backtest — it imports strategy components from backtest.py and
re-runs them under controlled variations.

  1. GATE ABLATION
       Run the strategy with the breadth gate disabled (always 100 % gross
       whenever the momentum signal is defined). Compare full-sample, IS/OOS,
       and regime-segmented stats vs the gated production variant.
       Question: does the breadth gate actually add risk-adjusted return?

  2. FEE SENSITIVITY
       Re-run the production (gated) strategy at 10 / 25 / 50 / 75 / 100 bps
       per side. Report CAGR, Sharpe, MaxDD, total fee drag, and IS/OOS Sharpe
       at each level.
       Question: at what fee level does the strategy stop working? That sets
       the realistic capacity ceiling.

  3. PER-COIN PnL ATTRIBUTION
       Decompose strategy PnL by coin. Daily $ contribution of coin j on day t:
           contrib_{t,j} = equity_{t-1} * weight_{t-1,j} * daily_ret_{t,j}
       Sum over time → total $ PnL by coin (gross of fees). Reported in
       full-sample, in-sample, and OOS slices, with top-3 concentration.
       Question: is the strategy diversified cross-sectional alpha, or a
       concentrated long-leader trade dressed in mechanics?
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Import strategy components from the production backtest module.
# scripts/research/ -> scripts/ (one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (
    Params,
    PRICES_PATH,
    IN_SAMPLE_END,
    OUT_OF_SAMPLE_START,
    load_prices, investability_mask,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    build_target_weights, run_backtest,
    summary_stats, regime_segments,
)


# ---------------------------------------------------------------------------

def run_variant(
    close: pd.DataFrame, mask: pd.DataFrame, p: Params,
    *, gate_on: bool, fee_bps: float,
) -> dict:
    """Run a single strategy variant. Returns the full backtest result dict."""
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    if gate_on:
        target_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    else:
        # No gate: full risk whenever breadth is defined (after the MA warmup
        # period). Before that, force 0 to avoid look-ahead.
        target_exposure = pd.Series(
            np.where(breadth.notna(), 1.0, 0.0), index=breadth.index,
            name="target_exposure",
        )
    mom = momentum_score(close, p.momentum_lookbacks_d, mask)
    weights_rank = rank_top_n(mom, p.rank_top_n)
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday)
    return run_backtest(close, target_w, fee_bps, lag_days=1)


def per_coin_attribution(close: pd.DataFrame, result: dict) -> pd.DataFrame:
    """Daily dollar PnL attribution by coin.

    weights[t]   = end-of-day t allocation (post-drift, post any rebalance)
    daily_ret[t] = close[t] / close[t-1] - 1
    equity[t]    = portfolio value at end of day t

    Day-t contribution of coin j to the portfolio:
        contrib_{t,j} = equity_{t-1} * weight_{t-1,j} * daily_ret_{t,j}

    Sum over t of sum over j ≈ final_equity - 1 - total_fees, with the small
    residual coming from intraday rebalance fee timing (fees are charged on
    the rebalance day at the close, contemporaneous with the new weights).
    """
    weights = result["weights"]
    equity = result["equity"]
    daily_ret = close.pct_change().fillna(0.0)
    eq_lag = equity.shift(1).fillna(1.0)
    w_lag = weights.shift(1).fillna(0.0)
    contrib = w_lag.mul(daily_ret, axis=0).mul(eq_lag, axis=0)
    return contrib


# ---------------------------------------------------------------------------

def main() -> int:
    print("Loading prices and computing universe mask ...")
    close, _ = load_prices(PRICES_PATH)
    mask = investability_mask(close)
    p = Params()
    sample_years = (close.index[-1] - close.index[0]).days / 365.25
    print(f"  panel: {close.shape[0]} dates x {close.shape[1]} symbols "
          f"({sample_years:.1f} years)")

    # ====================================================================
    # 1. GATE ABLATION
    # ====================================================================
    print("\n" + "=" * 78)
    print("DIAGNOSTIC 1 -- GATE ABLATION")
    print("Does the breadth gate add risk-adjusted return?")
    print("=" * 78)

    gated = run_variant(close, mask, p, gate_on=True, fee_bps=p.fee_bps_per_side)
    ungated = run_variant(close, mask, p, gate_on=False, fee_bps=p.fee_bps_per_side)

    print(f"\n{'variant':<28} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>8} {'FeeDrag':>8} {'AnnTO':>7}")
    print("-" * 78)
    for label, res in [
        ("gated (production)", gated),
        ("ungated (always 100% on)", ungated),
    ]:
        eq = res["equity"]
        s = summary_stats(eq)
        fees_total = res["fee_drag"].sum()
        ann_to = res["turnover"].sum() / sample_years
        print(f"{label:<28} {s['cagr']:>7.1%} {s['sharpe']:>7.2f} "
              f"{s['max_dd']:>7.1%}  {fees_total:>7.1%} {ann_to:>6.1f}x")

    print("\nIS vs OOS:")
    for label, res in [
        ("gated (production)", gated),
        ("ungated (always 100% on)", ungated),
    ]:
        is_s = summary_stats(res["equity"].loc[:IN_SAMPLE_END])
        oos_s = summary_stats(res["equity"].loc[OUT_OF_SAMPLE_START:])
        print(f"  {label:<28} "
              f"IS  CAGR={is_s['cagr']:>6.1%} Sh={is_s['sharpe']:>5.2f}  |  "
              f"OOS CAGR={oos_s['cagr']:>6.1%} Sh={oos_s['sharpe']:>5.2f}")

    print("\nRegime breakdown (gated -- ungated):")
    print(f"  {'regime':<22} {'gated CAGR':>11} {'gated Sh':>9}  "
          f"{'ungated CAGR':>13} {'ungated Sh':>11}")
    for label, start, end in regime_segments():
        g_eq = gated["equity"].loc[start:end]
        u_eq = ungated["equity"].loc[start:end]
        if len(g_eq) < 30:
            continue
        gs = summary_stats(g_eq)
        us = summary_stats(u_eq)
        print(f"  {label:<22} {gs['cagr']:>10.1%} {gs['sharpe']:>9.2f}  "
              f"{us['cagr']:>12.1%} {us['sharpe']:>11.2f}")

    # Verdict line
    g_full = summary_stats(gated["equity"])
    u_full = summary_stats(ungated["equity"])
    dSharpe = g_full["sharpe"] - u_full["sharpe"]
    dMaxDD = g_full["max_dd"] - u_full["max_dd"]  # less-negative = better
    print(f"\n  Gate delta (full-sample): "
          f"Sharpe {dSharpe:+.2f}, MaxDD improvement {dMaxDD:+.1%}, "
          f"CAGR {g_full['cagr'] - u_full['cagr']:+.1%}")

    # ====================================================================
    # 2. FEE SENSITIVITY
    # ====================================================================
    print("\n" + "=" * 78)
    print("DIAGNOSTIC 2 -- FEE SENSITIVITY")
    print("At what fee level does the strategy stop working?")
    print("=" * 78)
    print()

    fee_grid = [10.0, 25.0, 50.0, 75.0, 100.0]
    print(f"  {'fee bps/side':<13} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>8} "
          f"{'FeeDrag':>8}  {'IS Sh':>6}  {'OOS Sh':>7}")
    print("-" * 78)
    for f in fee_grid:
        res = run_variant(close, mask, p, gate_on=True, fee_bps=f)
        eq = res["equity"]
        s_full = summary_stats(eq)
        s_is = summary_stats(eq.loc[:IN_SAMPLE_END])
        s_oos = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])
        fees_total = res["fee_drag"].sum()
        print(f"  {f:<13.0f} {s_full['cagr']:>7.1%} {s_full['sharpe']:>7.2f} "
              f"{s_full['max_dd']:>7.1%}  {fees_total:>7.1%}  "
              f"{s_is['sharpe']:>6.2f}  {s_oos['sharpe']:>7.2f}")

    # ====================================================================
    # 3. PER-COIN PnL ATTRIBUTION
    # ====================================================================
    print("\n" + "=" * 78)
    print("DIAGNOSTIC 3 -- PER-COIN PnL ATTRIBUTION (production variant, 10bps)")
    print("Is the alpha diversified cross-sectional, or a concentrated long-leader trade?")
    print("=" * 78)

    contrib = per_coin_attribution(close, gated)
    final_equity = gated["equity"].iloc[-1]
    total_per_coin = contrib.sum(axis=0)
    weights = gated["weights"]
    days_held = (weights > 0).sum(axis=0)

    total_gross = total_per_coin.sum()
    # Approximate fee dollars: equity_pre_fee * fee_drag = equity / (1 - fd) * fd
    fee_drag = gated["fee_drag"]
    eq_post = gated["equity"]
    eq_pre = eq_post / (1.0 - fee_drag).where(fee_drag < 1.0, np.nan)
    fee_dollars_total = (eq_pre * fee_drag).fillna(0.0).sum()

    print(f"\n  Final equity: {final_equity:.2f}x starting capital")
    print(f"  Sum of per-coin gross PnL contributions: {total_gross:+.2f}")
    print(f"  Total fees paid (in starting-capital units): {fee_dollars_total:.2f}")
    print(f"  Reconciliation: 1 + gross - fees = "
          f"{1.0 + total_gross - fee_dollars_total:.2f}  "
          f"(vs final equity {final_equity:.2f})")

    print("\nFull-sample contribution by coin (sorted by $ contribution):")
    print(f"  {'coin':<6} {'$ contrib':>11} {'% of gross':>11} {'days held':>11}")
    print("  " + "-" * 41)
    sorted_idx = total_per_coin.sort_values(ascending=False).index
    for coin in sorted_idx:
        pct = total_per_coin[coin] / total_gross * 100 if total_gross != 0 else np.nan
        print(f"  {coin:<6} {total_per_coin[coin]:>11.3f} {pct:>10.1f}% "
              f"{int(days_held[coin]):>11}")

    # IS vs OOS
    is_contrib = contrib.loc[:IN_SAMPLE_END].sum(axis=0)
    oos_contrib = contrib.loc[OUT_OF_SAMPLE_START:].sum(axis=0)
    is_total = is_contrib.sum()
    oos_total = oos_contrib.sum()

    print("\nBy window:")
    print(f"  {'coin':<6} {'IS $':>10} {'IS %':>7}  {'OOS $':>11} {'OOS %':>8}")
    print("  " + "-" * 47)
    # Show in OOS-rank order so the "winners" of the test period are clear.
    for coin in oos_contrib.sort_values(ascending=False).index:
        is_pct = is_contrib[coin] / is_total * 100 if is_total != 0 else np.nan
        oos_pct = oos_contrib[coin] / oos_total * 100 if oos_total != 0 else np.nan
        print(f"  {coin:<6} {is_contrib[coin]:>10.3f} {is_pct:>6.1f}%  "
              f"{oos_contrib[coin]:>11.3f} {oos_pct:>7.1f}%")

    # Concentration
    def top_n_share(s: pd.Series, n: int) -> float:
        pos = s.clip(lower=0).sum()
        return s.nlargest(n).sum() / pos * 100 if pos > 0 else float("nan")

    print("\nTop-3 coin concentration (share of POSITIVE gross PnL):")
    print(f"  Full-sample : {top_n_share(total_per_coin, 3):.1f}%")
    print(f"  In-sample   : {top_n_share(is_contrib, 3):.1f}%")
    print(f"  Out-of-sample: {top_n_share(oos_contrib, 3):.1f}%")

    # Single-name concentration check
    print("\nLargest single-coin share (share of POSITIVE gross PnL):")
    for label, s in [
        ("Full-sample", total_per_coin),
        ("In-sample", is_contrib),
        ("Out-of-sample", oos_contrib),
    ]:
        pos = s.clip(lower=0).sum()
        if pos > 0:
            top = s.idxmax()
            share = s.loc[top] / pos * 100
            print(f"  {label:<14}: {top} = {share:.1f}%")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
