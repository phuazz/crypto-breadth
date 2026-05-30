"""
vol_target_search.py
--------------------
IS-only parameter search for the vol-target overlay.

Procedure (honest, no peeking):
  1. Define a grid of (vol_target_annual, vol_lookback_d) combinations.
  2. Run each combination on the FULL sample but evaluate Sharpe ONLY on
     the in-sample window (2018-01-01 to 2020-12-31).
  3. Pick the IS-best configuration.
  4. Report OOS (2021+) for that single configuration only.

This avoids the failure mode where the analyst peeks at OOS while choosing
parameters and then declares the OOS as "validation."

Baseline for comparison: the production strategy WITHOUT the vol-target
overlay (i.e., what `backtest.py` does today).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# scripts/research/ -> scripts/ (one level up).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    summary_stats,
)


def run_with_vol_target(
    close: pd.DataFrame, volume: pd.DataFrame, p: Params,
    *,
    vol_target_annual: float | None,
    vol_lookback_d: int,
    vol_floor_annual: float = 0.20,
) -> dict:
    """Run the production strategy, with optional vol-target overlay.

    vol_target_annual=None  -> no overlay (baseline).
    Otherwise: two-pass; pass 1 uses gate exposure, pass 2 scales it by
    min(1.0, vol_target / max(realised_vol_lagged, vol_floor)).
    """
    mask = investability_mask_liquidity(
        close, volume,
        lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days,
    )
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)

    entry_trend = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry_trend)
    weights_rank = rank_top_n(mom, p.rank_top_n)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)

    if vol_target_annual is None:
        target_w = build_target_weights(weights_rank, gate_exposure, p.rebalance_weekday)
        return run_backtest(
            close, target_w, p.fee_bps_per_side,
            lag_days=1, daily_exit_mask=exit_mask,
        )

    # Pass 1: production strategy daily returns drive the vol estimate.
    target_w_p1 = build_target_weights(weights_rank, gate_exposure, p.rebalance_weekday)
    res_p1 = run_backtest(
        close, target_w_p1, p.fee_bps_per_side,
        lag_days=1, daily_exit_mask=exit_mask,
    )
    base_ret = res_p1["daily_ret"]
    realised_vol = base_ret.rolling(
        vol_lookback_d, min_periods=max(10, vol_lookback_d // 2)
    ).std() * np.sqrt(365.0)
    realised_vol_lag = realised_vol.shift(1)
    denom = realised_vol_lag.clip(lower=vol_floor_annual)
    scaler = (vol_target_annual / denom).clip(upper=1.0).fillna(1.0)
    scaled_exposure = (gate_exposure * scaler).clip(lower=0.0, upper=1.0)

    target_w_p2 = build_target_weights(weights_rank, scaled_exposure, p.rebalance_weekday)
    return run_backtest(
        close, target_w_p2, p.fee_bps_per_side,
        lag_days=1, daily_exit_mask=exit_mask,
    )


def main() -> int:
    p = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)

    # Baseline (no vol target) — what production currently does
    print("\nBaseline (production, no vol-target overlay):")
    base_res = run_with_vol_target(close, volume, p,
                                    vol_target_annual=None, vol_lookback_d=0)
    base_eq = base_res["equity"]
    base_is_s = summary_stats(base_eq.loc[:IN_SAMPLE_END])
    base_oos_s = summary_stats(base_eq.loc[OUT_OF_SAMPLE_START:])
    base_full_s = summary_stats(base_eq)
    print(f"  Full   CAGR={base_full_s['cagr']:.1%} Sh={base_full_s['sharpe']:.2f} "
          f"DD={base_full_s['max_dd']:.1%}")
    print(f"  IS     CAGR={base_is_s['cagr']:.1%} Sh={base_is_s['sharpe']:.2f} "
          f"DD={base_is_s['max_dd']:.1%}")
    print(f"  OOS    CAGR={base_oos_s['cagr']:.1%} Sh={base_oos_s['sharpe']:.2f} "
          f"DD={base_oos_s['max_dd']:.1%}")

    # Grid search on IS only
    print("\n" + "=" * 78)
    print("IS-ONLY GRID SEARCH (Sharpe on 2018-01-01 -> 2020-12-31)")
    print("=" * 78)
    vol_targets = [0.40, 0.60, 0.80, 1.00]
    vol_lookbacks = [15, 30, 60]
    grid = {}

    print(f"\n  {'vol_target':<11} {'lookback':<9} {'IS CAGR':>9} {'IS Sh':>7} {'IS DD':>8}")
    print("  " + "-" * 50)
    for vt in vol_targets:
        for vl in vol_lookbacks:
            res = run_with_vol_target(close, volume, p,
                                      vol_target_annual=vt, vol_lookback_d=vl)
            is_eq = res["equity"].loc[:IN_SAMPLE_END]
            is_s = summary_stats(is_eq)
            grid[(vt, vl)] = is_s
            print(f"  {vt:<11.0%} {vl:<9} {is_s['cagr']:>8.1%} {is_s['sharpe']:>7.2f} "
                  f"{is_s['max_dd']:>8.1%}")

    # Pick IS-best config
    best_key = max(grid.keys(), key=lambda k: grid[k]["sharpe"] if not np.isnan(grid[k]["sharpe"]) else -999)
    vt_best, vl_best = best_key
    print(f"\n  IS-best configuration: vol_target={vt_best:.0%}, vol_lookback={vl_best}d  "
          f"(IS Sharpe = {grid[best_key]['sharpe']:.2f})")

    # Evaluate OOS for the IS-best config (one-shot, no re-selection)
    print("\n" + "=" * 78)
    print(f"OOS EVALUATION of IS-best config (vt={vt_best:.0%}, vl={vl_best}d)")
    print("=" * 78)
    best_res = run_with_vol_target(close, volume, p,
                                    vol_target_annual=vt_best, vol_lookback_d=vl_best)
    best_eq = best_res["equity"]
    best_is = summary_stats(best_eq.loc[:IN_SAMPLE_END])
    best_oos = summary_stats(best_eq.loc[OUT_OF_SAMPLE_START:])
    best_full = summary_stats(best_eq)
    print(f"\n  {'window':<8} {'CAGR':>9} {'Sharpe':>7} {'MaxDD':>8}  "
          f"vs baseline Sh: ")
    print("  " + "-" * 64)
    print(f"  {'IS':<8} {best_is['cagr']:>8.1%} {best_is['sharpe']:>7.2f} "
          f"{best_is['max_dd']:>8.1%}   "
          f"{best_is['sharpe'] - base_is_s['sharpe']:+.2f}")
    print(f"  {'OOS':<8} {best_oos['cagr']:>8.1%} {best_oos['sharpe']:>7.2f} "
          f"{best_oos['max_dd']:>8.1%}   "
          f"{best_oos['sharpe'] - base_oos_s['sharpe']:+.2f}")
    print(f"  {'Full':<8} {best_full['cagr']:>8.1%} {best_full['sharpe']:>7.2f} "
          f"{best_full['max_dd']:>8.1%}   "
          f"{best_full['sharpe'] - base_full_s['sharpe']:+.2f}")

    # Honest reality check: did IS-tuning generalise OOS?
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    is_delta = best_is["sharpe"] - base_is_s["sharpe"]
    oos_delta = best_oos["sharpe"] - base_oos_s["sharpe"]
    print(f"\n  IS Sharpe improvement: {is_delta:+.2f} "
          f"(IS-best: {best_is['sharpe']:.2f} vs baseline: {base_is_s['sharpe']:.2f})")
    print(f"  OOS Sharpe improvement: {oos_delta:+.2f} "
          f"(IS-best: {best_oos['sharpe']:.2f} vs baseline: {base_oos_s['sharpe']:.2f})")
    if oos_delta > 0.05:
        print("\n  -> IS-tuned overlay generalises OOS. Adopt for v3.")
    elif oos_delta > -0.05:
        print("\n  -> Roughly neutral OOS. Marginal; keep as optional overlay.")
    else:
        print("\n  -> IS-tuning does NOT generalise OOS. Drop the overlay.")

    # Bonus: show the FULL OOS grid for the user's information, but state
    # explicitly that this is NOT the basis for the decision.
    print("\n" + "=" * 78)
    print("FULL OOS GRID  (FYI ONLY — this is NOT how the decision was made)")
    print("=" * 78)
    print(f"\n  {'vol_target':<11} {'lookback':<9} {'OOS CAGR':>10} {'OOS Sh':>8} {'OOS DD':>9}")
    print("  " + "-" * 51)
    for vt in vol_targets:
        for vl in vol_lookbacks:
            res = run_with_vol_target(close, volume, p,
                                      vol_target_annual=vt, vol_lookback_d=vl)
            oos_eq = res["equity"].loc[OUT_OF_SAMPLE_START:]
            oos_s = summary_stats(oos_eq)
            marker = "  <-- IS-best" if (vt, vl) == best_key else ""
            print(f"  {vt:<11.0%} {vl:<9} {oos_s['cagr']:>9.1%} {oos_s['sharpe']:>8.2f} "
                  f"{oos_s['max_dd']:>9.1%}{marker}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
