"""
sensitivity.py
--------------
One-at-a-time (OAT) parameter sensitivity sweep of the v3 production strategy.

PROTOCOL (honest, no peeking):
  - The current Params() values are hard-coded defaults — they were never
    IS-tuned. This script is NOT a tuning exercise; it is a stress test.
  - For each parameter, hold all others at their default and test a small
    set of perturbations (typically -50% / default / +50%, plus natural
    alternatives where they exist).
  - Primary metric: IS Sharpe (2018-01-01 to 2020-12-31) and its RANGE
    across the perturbations of that one parameter.
  - OOS Sharpe is reported alongside, but is NOT used to pick a "best"
    config. The decision is purely "is IS Sharpe stable, yes or no."

VERDICT RULES (decided upfront so I cannot move goalposts):
  - Robust              : Max - Min IS Sharpe across a parameter <= 0.30
  - Mildly sensitive    : 0.30 < range <= 0.60
  - Fragile             : range > 0.60 (suggests overfitting risk)
  - Default-on-knife-edge: default Sharpe is the MAX of its parameter's grid
    AND any neighbour drops by >= 0.20. That is a red flag regardless of
    overall range.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    summary_stats,
)


def run_v3(close: pd.DataFrame, volume: pd.DataFrame, p: Params) -> dict:
    """Production pipeline as a function of Params."""
    mask = investability_mask_liquidity(
        close, volume,
        lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days,
    )
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
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday)
    exit_mask = (
        per_coin_trend_exit_mask(close, p.per_coin_trend_window)
        if p.use_daily_trend_exit else None
    )
    return run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )


def stats_for(eq: pd.Series) -> tuple[float, float, float]:
    """Return (IS Sharpe, OOS Sharpe, Full Sharpe)."""
    is_s = summary_stats(eq.loc[:IN_SAMPLE_END])
    oos_s = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])
    full_s = summary_stats(eq)
    return is_s, oos_s, full_s


# ---------------------------------------------------------------------------
# Parameter grids. Each entry is a list of (label, kwargs_for_replace).
# ---------------------------------------------------------------------------

def build_grids() -> dict:
    return {
        "breadth_ma_window": [
            ("25d (-50%)",  {"breadth_ma_window": 25}),
            ("50d default", {"breadth_ma_window": 50}),
            ("75d (+50%)",  {"breadth_ma_window": 75}),
            ("100d (+100%)", {"breadth_ma_window": 100}),
            ("200d (+300%)", {"breadth_ma_window": 200}),
        ],
        "momentum_lookbacks_d": [
            ("(10,30,60) faster",   {"momentum_lookbacks_d": (10, 30, 60)}),
            ("(14,42,84) -33%",     {"momentum_lookbacks_d": (14, 42, 84)}),
            ("(21,63,126) default", {"momentum_lookbacks_d": (21, 63, 126)}),
            ("(30,90,180) +50%",    {"momentum_lookbacks_d": (30, 90, 180)}),
            ("(63,) single 3m",     {"momentum_lookbacks_d": (63,)}),
            ("(21,) single 1m",     {"momentum_lookbacks_d": (21,)}),
        ],
        "rank_top_n": [
            ("1 single name",    {"rank_top_n": 1}),
            ("2",                {"rank_top_n": 2}),
            ("3 default",        {"rank_top_n": 3}),
            ("4",                {"rank_top_n": 4}),
            ("5",                {"rank_top_n": 5}),
            ("7",                {"rank_top_n": 7}),
        ],
        "tier_thresholds": [
            ("(0.20,0.40,0.60) looser",   {"tier_thresholds": (0.20, 0.40, 0.60)}),
            ("(0.30,0.50,0.70) default",  {"tier_thresholds": (0.30, 0.50, 0.70)}),
            ("(0.40,0.60,0.80) stricter", {"tier_thresholds": (0.40, 0.60, 0.80)}),
        ],
        "tier_exposures": [
            ("(0,0.3,0.6,1.0) default",   {"tier_exposures": (0.0, 0.3, 0.6, 1.0)}),
            ("(0,0.5,0.75,1.0) more bold", {"tier_exposures": (0.0, 0.5, 0.75, 1.0)}),
            ("(0,0,0,1.0) binary gate",   {"tier_exposures": (0.0, 0.0, 0.0, 1.0)}),
            ("(0,0.25,0.5,0.75) capped",  {"tier_exposures": (0.0, 0.25, 0.5, 0.75)}),
        ],
        "per_coin_trend_window": [
            ("25d (-50%)",   {"per_coin_trend_window": 25}),
            ("50d default",  {"per_coin_trend_window": 50}),
            ("75d (+50%)",   {"per_coin_trend_window": 75}),
            ("100d (+100%)", {"per_coin_trend_window": 100}),
            ("200d (+300%)", {"per_coin_trend_window": 200}),
        ],
        "liquidity_min_adv_usd": [
            ("$10M (-60%)",  {"liquidity_min_adv_usd": 10_000_000.0}),
            ("$25M default", {"liquidity_min_adv_usd": 25_000_000.0}),
            ("$50M (+100%)", {"liquidity_min_adv_usd": 50_000_000.0}),
            ("$100M (+300%)", {"liquidity_min_adv_usd": 100_000_000.0}),
        ],
    }


def main() -> int:
    p_default = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)

    # Run the default config first to anchor.
    base_res = run_v3(close, volume, p_default)
    base_is, base_oos, base_full = stats_for(base_res["equity"])
    print(f"\nBaseline (v3 production):")
    print(f"  IS  Sharpe={base_is['sharpe']:>5.2f}  CAGR={base_is['cagr']:>6.1%}")
    print(f"  OOS Sharpe={base_oos['sharpe']:>5.2f}  CAGR={base_oos['cagr']:>6.1%}")
    print(f"  Full Sharpe={base_full['sharpe']:>5.2f}  CAGR={base_full['cagr']:>6.1%}")

    grids = build_grids()
    all_results = {}

    for param_name, grid in grids.items():
        print("\n" + "=" * 78)
        print(f"PARAMETER: {param_name}")
        print("=" * 78)
        print(f"\n  {'label':<28} {'IS Sh':>6} {'IS CAGR':>8} {'IS DD':>8} "
              f"{'OOS Sh':>7} {'Full Sh':>8}")
        print("  " + "-" * 70)

        param_rows = []
        for label, kwargs in grid:
            p_var = replace(p_default, **kwargs)
            try:
                res = run_v3(close, volume, p_var)
                is_s, oos_s, full_s = stats_for(res["equity"])
            except Exception as e:
                print(f"  {label:<28} ERROR: {e!r}")
                continue
            is_sh = is_s["sharpe"]
            oos_sh = oos_s["sharpe"]
            full_sh = full_s["sharpe"]
            param_rows.append({
                "label": label,
                "kwargs": kwargs,
                "IS_Sh": is_sh, "IS_CAGR": is_s["cagr"], "IS_DD": is_s["max_dd"],
                "OOS_Sh": oos_sh, "Full_Sh": full_sh,
            })
            is_default = "default" in label
            marker = "  <-- default" if is_default else ""
            print(f"  {label:<28} {is_sh:>6.2f} {is_s['cagr']:>7.1%} "
                  f"{is_s['max_dd']:>7.1%}  {oos_sh:>7.2f} {full_sh:>8.2f}{marker}")

        # Compute IS Sharpe range and verdict for this parameter
        if not param_rows:
            continue
        is_sh_values = [r["IS_Sh"] for r in param_rows if not np.isnan(r["IS_Sh"])]
        if not is_sh_values:
            continue
        sh_min = min(is_sh_values)
        sh_max = max(is_sh_values)
        sh_range = sh_max - sh_min

        default_row = next((r for r in param_rows if "default" in r["label"]), None)
        default_sh = default_row["IS_Sh"] if default_row else float("nan")

        # Find neighbours of default (one step in either direction in the grid)
        if default_row is not None:
            default_idx = param_rows.index(default_row)
            neighbours_sh = []
            if default_idx > 0:
                neighbours_sh.append(param_rows[default_idx - 1]["IS_Sh"])
            if default_idx < len(param_rows) - 1:
                neighbours_sh.append(param_rows[default_idx + 1]["IS_Sh"])
            worst_neighbour_drop = (default_sh - min(neighbours_sh)
                                    if neighbours_sh else 0.0)
        else:
            worst_neighbour_drop = 0.0

        if sh_range <= 0.30:
            verdict = "ROBUST"
        elif sh_range <= 0.60:
            verdict = "MILDLY SENSITIVE"
        else:
            verdict = "FRAGILE"
        # Knife-edge override
        knife_edge = (
            default_row is not None
            and default_sh == sh_max
            and worst_neighbour_drop >= 0.20
        )
        print(f"\n  IS Sharpe range: {sh_min:.2f} -> {sh_max:.2f}  "
              f"(span {sh_range:.2f})")
        print(f"  Default IS Sharpe: {default_sh:.2f}")
        if default_row is not None:
            print(f"  Worst single-step neighbour drop: {worst_neighbour_drop:+.2f}")
        print(f"  Verdict: {verdict}"
              f"{'  [KNIFE-EDGE]' if knife_edge else ''}")

        all_results[param_name] = {
            "rows": param_rows,
            "sh_min": sh_min, "sh_max": sh_max, "sh_range": sh_range,
            "default_sh": default_sh, "verdict": verdict,
            "knife_edge": knife_edge,
        }

    # ----- final summary -----------------------------------------------
    print("\n" + "=" * 78)
    print("OVERALL ROBUSTNESS SUMMARY")
    print("=" * 78)
    print(f"\n  {'parameter':<24} {'IS Sh min':>10} {'IS Sh max':>10} "
          f"{'span':>6} {'default':>8} {'verdict':<20}")
    print("  " + "-" * 78)
    for name, r in all_results.items():
        ke = "  [KNIFE-EDGE]" if r["knife_edge"] else ""
        print(f"  {name:<24} {r['sh_min']:>9.2f} {r['sh_max']:>9.2f} "
              f"{r['sh_range']:>6.2f} {r['default_sh']:>8.2f}  {r['verdict']}{ke}")

    n_robust = sum(1 for r in all_results.values() if r["verdict"] == "ROBUST")
    n_mild = sum(1 for r in all_results.values() if r["verdict"] == "MILDLY SENSITIVE")
    n_frag = sum(1 for r in all_results.values() if r["verdict"] == "FRAGILE")
    n_knife = sum(1 for r in all_results.values() if r["knife_edge"])

    print(f"\n  {len(all_results)} parameters tested:")
    print(f"    Robust          : {n_robust}")
    print(f"    Mildly sensitive: {n_mild}")
    print(f"    Fragile         : {n_frag}")
    print(f"    Knife-edge      : {n_knife}")

    print("\n  Interpretation:")
    if n_frag == 0 and n_knife == 0:
        print("    v3 is structurally robust to the tested perturbations. The "
              "Sharpe \n    estimate is not a fluke of specific parameter choices.")
    elif n_frag > 0:
        print(f"    {n_frag} parameter(s) show fragile behaviour. Investigate "
              "before deploying.")
    if n_knife > 0:
        print(f"    {n_knife} parameter(s) sit on a knife-edge at the default "
              "value. This is\n    suspicious — the default may have been "
              "implicitly chosen on the data.")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
