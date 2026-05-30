"""
backtest_v1.py
--------------
v1 of the breadth-gate + momentum strategy. Adds two overlays on top of v0:

  A. Volatility-target exposure scaler (smooths the binary breadth gate)
       - Compute the v0 strategy's rolling 30d realised vol.
       - Scale next-period gross exposure by:
             scaler_t = min(1.0, vol_target / max(realised_vol_{t-1}, vol_floor))
       - Applied multiplicatively on top of the breadth gate.

  B. Per-coin trend filter on the ranking step
       - A coin is eligible for top-N rank only if:
             close > 50d MA  AND  50d MA is rising (MA.diff() > 0)
       - Coins failing the filter are excluded from the ranking entirely,
         even if their momentum score is high. Slots stay in cash if fewer
         than N qualifiers exist.

This is a research file. Production `backtest.py` is unchanged. The signal
pipeline (breadth, momentum scoring, fees, lag, hygiene) is identical to v0
except for the two overlays above.

Run side-by-side against v0 and print a comparison table.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# scripts/research/ -> scripts/ (one level up) to import backtest.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (
    Params as ParamsV0,
    PRICES_PATH,
    IN_SAMPLE_END,
    OUT_OF_SAMPLE_START,
    load_prices, investability_mask,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    build_target_weights, run_backtest,
    summary_stats, regime_segments,
)


# ----- v1 parameters -------------------------------------------------------

@dataclass
class ParamsV1:
    # Inherited v0 parameters (kept identical for fair comparison).
    breadth_ma_window: int = 50
    momentum_lookbacks_d: tuple = (21, 63, 126)
    rank_top_n: int = 3
    rebalance_weekday: int = 0
    fee_bps_per_side: float = 10.0
    tier_thresholds: tuple = (0.30, 0.50, 0.70)
    tier_exposures: tuple = (0.0, 0.30, 0.60, 1.00)

    # NEW v1 overlay parameters.
    vol_target_annual: float = 0.60       # 60% annualised portfolio vol target
    vol_lookback_d: int = 30              # rolling window for realised vol
    vol_floor_annual: float = 0.20        # min realised vol used as divisor
    per_coin_trend_window: int = 50       # uses same window as breadth gate
    # Toggles so we can isolate each overlay's effect.
    use_vol_target: bool = True
    use_per_coin_trend: bool = True


# ----- v1 signal modifications --------------------------------------------

def per_coin_trend_mask(close: pd.DataFrame, window: int) -> pd.DataFrame:
    """Boolean DataFrame: True where (close > MA_window) AND MA_window is rising.

    Aligned to close.index x close.columns. NaN-handling: a cell is False if
    the MA cannot be computed (insufficient history).
    """
    ma = close.rolling(window, min_periods=window).mean()
    above_ma = close > ma
    ma_rising = ma.diff() > 0
    return (above_ma & ma_rising).fillna(False)


def momentum_score_filtered(
    close: pd.DataFrame, lookbacks: tuple, mask: pd.DataFrame,
    trend_mask: pd.DataFrame | None,
) -> pd.DataFrame:
    """v0 momentum_score with optional per-coin trend filter applied.

    When trend_mask is provided, momentum scores for coins failing the
    trend filter are set to NaN so they are excluded from rank_top_n.
    """
    score = momentum_score(close, lookbacks, mask)
    if trend_mask is not None:
        # Reindex defensively (should already match).
        tm = trend_mask.reindex(index=score.index, columns=score.columns).fillna(False)
        score = score.where(tm)
    return score


# ----- vol target overlay --------------------------------------------------

def vol_targeted_exposure(
    base_exposure: pd.Series,
    base_daily_returns: pd.Series,
    *,
    vol_target_annual: float,
    vol_lookback_d: int,
    vol_floor_annual: float,
) -> pd.Series:
    """Apply a vol-target scaler on top of the base (gate-driven) exposure.

    Uses rolling realised vol of the BASE STRATEGY's daily returns. Lagged
    by 1 day so the scaler at time t uses information available at t-1
    (no look-ahead).

    Returns the new exposure series (scaled, capped at 1.0).
    """
    # Annualised rolling realised vol of the base strategy.
    realised_vol = base_daily_returns.rolling(
        vol_lookback_d, min_periods=max(10, vol_lookback_d // 2)
    ).std() * np.sqrt(365.0)
    # Lag by 1 day to avoid look-ahead (today's scaler uses yesterday's vol).
    realised_vol_lag = realised_vol.shift(1)
    # Floor the denominator to avoid blowups in dead markets.
    denom = realised_vol_lag.clip(lower=vol_floor_annual)
    scaler = (vol_target_annual / denom).clip(upper=1.0)
    # Where realised vol is NaN (warmup), fall back to base exposure (no scaling).
    scaler = scaler.fillna(1.0)
    return (base_exposure * scaler).clip(lower=0.0, upper=1.0).rename("target_exposure_v1")


# ----- v1 runner -----------------------------------------------------------

def run_v1(
    close: pd.DataFrame, mask: pd.DataFrame, p: ParamsV1,
) -> dict:
    """Two-pass v1 backtest.

    Pass 1: run the strategy with v1's per-coin trend filter but WITHOUT
            the vol scaler — get base daily returns for the vol estimate.
    Pass 2: re-run with the vol-scaled exposure.

    If `p.use_vol_target` is False, returns the pass-1 result directly.
    If `p.use_per_coin_trend` is False, uses unfiltered momentum scores.
    """
    # Signals (shared)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)

    # Per-coin trend filter (overlay B)
    trend_mask = (
        per_coin_trend_mask(close, p.per_coin_trend_window)
        if p.use_per_coin_trend else None
    )
    mom = momentum_score_filtered(close, p.momentum_lookbacks_d, mask, trend_mask)
    weights_rank = rank_top_n(mom, p.rank_top_n)

    # Pass 1 (no vol scaler) — needed to estimate base vol for pass 2
    target_w_pass1 = build_target_weights(weights_rank, gate_exposure, p.rebalance_weekday)
    res_pass1 = run_backtest(close, target_w_pass1, p.fee_bps_per_side, lag_days=1)

    if not p.use_vol_target:
        return res_pass1

    # Pass 2: vol-target scaler on the gate exposure
    base_daily_returns = res_pass1["daily_ret"]
    v1_exposure = vol_targeted_exposure(
        gate_exposure, base_daily_returns,
        vol_target_annual=p.vol_target_annual,
        vol_lookback_d=p.vol_lookback_d,
        vol_floor_annual=p.vol_floor_annual,
    )
    target_w_pass2 = build_target_weights(weights_rank, v1_exposure, p.rebalance_weekday)
    res_pass2 = run_backtest(close, target_w_pass2, p.fee_bps_per_side, lag_days=1)

    # Attach overlay diagnostics for inspection
    res_pass2["v1_exposure"] = v1_exposure
    res_pass2["gate_exposure"] = gate_exposure
    res_pass2["base_strategy_daily_ret"] = base_daily_returns
    return res_pass2


# ----- v0 runner for comparison -------------------------------------------

def run_v0(close, mask, p_v0: ParamsV0) -> dict:
    breadth = breadth_pct_above_ma(close, p_v0.breadth_ma_window, mask)
    target_exposure = breadth_to_tier(breadth, p_v0.tier_thresholds, p_v0.tier_exposures)
    mom = momentum_score(close, p_v0.momentum_lookbacks_d, mask)
    weights_rank = rank_top_n(mom, p_v0.rank_top_n)
    target_w = build_target_weights(weights_rank, target_exposure, p_v0.rebalance_weekday)
    return run_backtest(close, target_w, p_v0.fee_bps_per_side, lag_days=1)


# ----- per-coin attribution (copy from diagnostics.py to keep this file self-contained)

def per_coin_attribution(close: pd.DataFrame, result: dict) -> pd.DataFrame:
    weights = result["weights"]
    equity = result["equity"]
    daily_ret = close.pct_change().fillna(0.0)
    eq_lag = equity.shift(1).fillna(1.0)
    w_lag = weights.shift(1).fillna(0.0)
    return w_lag.mul(daily_ret, axis=0).mul(eq_lag, axis=0)


# ----- reporting -----------------------------------------------------------

def print_stats_block(label: str, eq: pd.Series, fees_total: float, sample_years: float):
    s = summary_stats(eq)
    print(f"  {label:<22} CAGR={s['cagr']:>7.1%}  "
          f"Sharpe={s['sharpe']:>5.2f}  MaxDD={s['max_dd']:>6.1%}  "
          f"FeeDrag={fees_total:>6.1%}")


def print_is_oos(label: str, eq: pd.Series):
    is_s = summary_stats(eq.loc[:IN_SAMPLE_END])
    oos_s = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])
    print(f"  {label:<22} "
          f"IS  CAGR={is_s['cagr']:>6.1%} Sh={is_s['sharpe']:>5.2f}  |  "
          f"OOS CAGR={oos_s['cagr']:>6.1%} Sh={oos_s['sharpe']:>5.2f}")


def main() -> int:
    print("Loading prices ...")
    close, _ = load_prices(PRICES_PATH)
    mask = investability_mask(close)
    sample_years = (close.index[-1] - close.index[0]).days / 365.25

    p_v0 = ParamsV0()
    p_v1_full = ParamsV1(use_vol_target=True, use_per_coin_trend=True)
    p_v1_trend_only = ParamsV1(use_vol_target=False, use_per_coin_trend=True)
    p_v1_vol_only = ParamsV1(use_vol_target=True, use_per_coin_trend=False)

    print("\nRunning v0 (baseline) ...")
    v0_res = run_v0(close, mask, p_v0)

    print("Running v1 (vol target + per-coin trend) ...")
    v1_res = run_v1(close, mask, p_v1_full)

    print("Running v1 trend-only (no vol target) ...")
    v1_trend = run_v1(close, mask, p_v1_trend_only)

    print("Running v1 vol-only (no per-coin trend) ...")
    v1_vol = run_v1(close, mask, p_v1_vol_only)

    # ====================================================================
    print("\n" + "=" * 78)
    print("FULL-SAMPLE COMPARISON")
    print("=" * 78)
    print(f"  {'variant':<22} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>8} {'FeeDrag':>8}")
    print("  " + "-" * 60)
    for label, res in [
        ("v0 baseline", v0_res),
        ("v1 trend-only", v1_trend),
        ("v1 vol-only", v1_vol),
        ("v1 full (both)", v1_res),
    ]:
        eq = res["equity"]
        s = summary_stats(eq)
        fees_total = res["fee_drag"].sum()
        print(f"  {label:<22} {s['cagr']:>7.1%} {s['sharpe']:>7.2f} "
              f"{s['max_dd']:>7.1%}  {fees_total:>7.1%}")

    print("\nIS vs OOS:")
    for label, res in [
        ("v0 baseline", v0_res),
        ("v1 trend-only", v1_trend),
        ("v1 vol-only", v1_vol),
        ("v1 full (both)", v1_res),
    ]:
        print_is_oos(label, res["equity"])

    print("\nREGIME BREAKDOWN (Sharpe / CAGR):")
    print(f"  {'regime':<20} {'v0 Sh':>6} {'v0 CAGR':>9}   "
          f"{'v1 Sh':>6} {'v1 CAGR':>9}   {'dSh':>5} {'dCAGR':>7}")
    print("  " + "-" * 76)
    for label, start, end in regime_segments():
        v0_eq = v0_res["equity"].loc[start:end]
        v1_eq = v1_res["equity"].loc[start:end]
        if len(v0_eq) < 30:
            continue
        s0 = summary_stats(v0_eq)
        s1 = summary_stats(v1_eq)
        d_sh = s1["sharpe"] - s0["sharpe"]
        d_cagr = s1["cagr"] - s0["cagr"]
        print(f"  {label:<20} {s0['sharpe']:>6.2f} {s0['cagr']:>8.1%}   "
              f"{s1['sharpe']:>6.2f} {s1['cagr']:>8.1%}   "
              f"{d_sh:>+5.2f} {d_cagr:>+7.1%}")

    # ====================================================================
    print("\n" + "=" * 78)
    print("v1 OVERLAY DIAGNOSTICS")
    print("=" * 78)
    if "v1_exposure" in v1_res:
        v1_exp = v1_res["v1_exposure"]
        gate_exp = v1_res["gate_exposure"]
        print(f"\n  Gate exposure (v0):   mean={gate_exp.mean():.2f}  "
              f"%full={(gate_exp == 1.0).mean():.1%}  "
              f"%zero={(gate_exp == 0.0).mean():.1%}")
        print(f"  Scaled exposure (v1): mean={v1_exp.mean():.2f}  "
              f"%full={(v1_exp >= 0.99).mean():.1%}  "
              f"%zero={(v1_exp < 0.01).mean():.1%}")
        print(f"  Effective vol-scaler (v1 / gate, where gate>0): "
              f"mean={(v1_exp[gate_exp > 0] / gate_exp[gate_exp > 0]).mean():.2f}")

    # Turnover and fee comparison
    print(f"\n  Annual turnover (v0): "
          f"{v0_res['turnover'].sum() / sample_years:.1f}x")
    print(f"  Annual turnover (v1): "
          f"{v1_res['turnover'].sum() / sample_years:.1f}x")

    # ====================================================================
    print("\n" + "=" * 78)
    print("PER-COIN PnL ATTRIBUTION -- v0 vs v1 (full-sample, gross of fees)")
    print("=" * 78)
    contrib_v0 = per_coin_attribution(close, v0_res)
    contrib_v1 = per_coin_attribution(close, v1_res)
    tot_v0 = contrib_v0.sum(axis=0)
    tot_v1 = contrib_v1.sum(axis=0)

    # OOS attribution
    oos_v0 = contrib_v0.loc[OUT_OF_SAMPLE_START:].sum(axis=0)
    oos_v1 = contrib_v1.loc[OUT_OF_SAMPLE_START:].sum(axis=0)

    print(f"\n  {'coin':<6} {'v0 full':>10} {'v1 full':>10}   "
          f"{'v0 OOS':>10} {'v1 OOS':>10}   {'OOS d':>9}")
    print("  " + "-" * 67)
    for coin in sorted(tot_v0.index, key=lambda c: -tot_v1[c]):
        d_oos = oos_v1[coin] - oos_v0[coin]
        print(f"  {coin:<6} {tot_v0[coin]:>10.2f} {tot_v1[coin]:>10.2f}   "
              f"{oos_v0[coin]:>10.2f} {oos_v1[coin]:>10.2f}   {d_oos:>+9.2f}")

    print("\n  Aggregate:")
    print(f"    v0 gross PnL (full): {tot_v0.sum():+.2f}    "
          f"v1 gross PnL (full): {tot_v1.sum():+.2f}")
    print(f"    v0 gross PnL (OOS):  {oos_v0.sum():+.2f}    "
          f"v1 gross PnL (OOS):  {oos_v1.sum():+.2f}")
    print(f"    Negative-contributors v0 OOS: "
          f"{[c for c in oos_v0.index if oos_v0[c] < 0]}")
    print(f"    Negative-contributors v1 OOS: "
          f"{[c for c in oos_v1.index if oos_v1[c] < 0]}")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
