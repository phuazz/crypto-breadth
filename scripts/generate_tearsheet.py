"""
generate_tearsheet.py
---------------------
One-page summary PNG + markdown text for v3.

Outputs:
  - data/tearsheet.png   : 2x2 chart grid (equity / drawdown / yearly bars /
                            sensitivity bar chart)
  - stdout               : markdown-friendly summary table (paste-ready)

Usage:
  python scripts/generate_tearsheet.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START, OUT_DIR,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    benchmark_hodl, benchmark_equal_weight, benchmark_60_40_btc_eth,
    summary_stats,
)


def run_v3(close: pd.DataFrame, volume: pd.DataFrame, p: Params) -> dict:
    mask = investability_mask_liquidity(
        close, volume,
        lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days,
    )
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    target_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry_trend = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry_trend)
    weights_rank = rank_top_n(mom, p.rank_top_n)
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday,
                                    single_name_cap=p.single_name_cap)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    return run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )


def bootstrap_sharpe_ci(daily_ret: pd.Series, n_boot: int = 2000,
                        block_size: int = 21, seed: int = 42) -> dict:
    daily = daily_ret.dropna().values
    n = len(daily)
    n_blocks = n // block_size
    rng = np.random.default_rng(seed=seed)
    sharpes = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n - block_size, size=n_blocks)
        sample = np.concatenate([daily[s:s + block_size] for s in starts])
        mu = sample.mean() * 365
        sig = sample.std() * np.sqrt(365)
        sharpes[i] = mu / sig if sig > 0 else 0.0
    return {
        "p05": float(np.quantile(sharpes, 0.05)),
        "p50": float(np.quantile(sharpes, 0.50)),
        "p95": float(np.quantile(sharpes, 0.95)),
        "p_pos": float((sharpes > 0).mean()),
        "p_gt_0p8": float((sharpes > 0.8).mean()),
    }


def per_year_returns(eq: pd.Series) -> pd.Series:
    """Calendar-year total return for an equity curve."""
    annual = eq.resample("YE").last() / eq.resample("YE").last().shift(1) - 1.0
    # First year: from start to first year-end
    first_year = eq.index[0].year
    first_end = eq.loc[f"{first_year}-01-01":f"{first_year}-12-31"]
    if len(first_end) > 0:
        annual.loc[f"{first_year}-12-31"] = first_end.iloc[-1] / eq.iloc[0] - 1.0
    annual.index = annual.index.year
    return annual.dropna()


def sensitivity_summary() -> pd.DataFrame:
    """Re-run a slimmed sensitivity sweep and return per-parameter min/default/max
    IS Sharpe."""
    p_default = Params()
    close, volume = load_prices(PRICES_PATH)
    base_res = run_v3(close, volume, p_default)
    base_is_sh = summary_stats(base_res["equity"].loc[:IN_SAMPLE_END])["sharpe"]

    grids = {
        "breadth_ma_window": [25, 50, 75, 100, 200],
        "rank_top_n": [1, 2, 3, 4, 5, 7],
        "per_coin_trend_window": [25, 50, 75, 100, 200],
        "liquidity_min_adv_usd": [10e6, 25e6, 50e6, 100e6],
    }
    # Treat tuple params separately
    rows = []
    for param_name, values in grids.items():
        sharpes = []
        for v in values:
            p_var = replace(p_default, **{param_name: v})
            try:
                res = run_v3(close, volume, p_var)
                is_sh = summary_stats(res["equity"].loc[:IN_SAMPLE_END])["sharpe"]
            except Exception:
                continue
            sharpes.append(is_sh)
        rows.append({
            "param": param_name,
            "min": min(sharpes),
            "default": base_is_sh,
            "max": max(sharpes),
        })

    # tuple params — short manual sweeps
    for label, mom_lb in [("mom_lookbacks", [(10,30,60),(14,42,84),(21,63,126),(30,90,180),(63,)])]:
        sharpes = []
        for v in mom_lb:
            p_var = replace(p_default, momentum_lookbacks_d=v)
            try:
                res = run_v3(close, volume, p_var)
                is_sh = summary_stats(res["equity"].loc[:IN_SAMPLE_END])["sharpe"]
            except Exception:
                continue
            sharpes.append(is_sh)
        rows.append({"param": label, "min": min(sharpes),
                     "default": base_is_sh, "max": max(sharpes)})

    return pd.DataFrame(rows).set_index("param")


def main() -> int:
    p = Params()
    print("Loading prices and running v3 ...")
    close, volume = load_prices(PRICES_PATH)
    res = run_v3(close, volume, p)
    eq = res["equity"]

    print("Building benchmarks ...")
    bm_btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    mask = investability_mask_liquidity(
        close, volume,
        lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days,
    )
    bm_ew = benchmark_equal_weight(close, mask).reindex(eq.index).ffill()
    bm_6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    print("Bootstrapping Sharpe CI ...")
    ci = bootstrap_sharpe_ci(res["daily_ret"])

    print("Sensitivity sweep (subset) ...")
    sens = sensitivity_summary()

    # ----- compute summary stats -----
    full_s = summary_stats(eq)
    is_s = summary_stats(eq.loc[:IN_SAMPLE_END])
    oos_s = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])
    bm_btc_full = summary_stats(bm_btc)
    bm_ew_full = summary_stats(bm_ew)
    bm_6040_full = summary_stats(bm_6040)

    # ----- build figure -----
    fig = plt.figure(figsize=(14, 10), constrained_layout=True)
    gs = fig.add_gridspec(3, 2, height_ratios=[1.5, 1, 1.2])

    # (0,0..1) equity curves — span both columns
    ax_eq = fig.add_subplot(gs[0, :])
    ax_eq.plot(eq.index, eq.values, label="Strategy v3", lw=1.8, color="#111")
    ax_eq.plot(bm_btc.index, bm_btc.values, label="BTC HODL", lw=1.0, alpha=0.7, color="#f7931a")
    ax_eq.plot(bm_ew.index, bm_ew.values, label="Equal-weight investable", lw=1.0, alpha=0.7, color="#27ae60")
    ax_eq.plot(bm_6040.index, bm_6040.values, label="60/40 BTC/ETH", lw=1.0, alpha=0.7, color="#2980b9")
    ax_eq.set_yscale("log")
    ax_eq.axvline(pd.Timestamp(OUT_OF_SAMPLE_START), color="gray", lw=0.8, linestyle="--", alpha=0.6)
    ax_eq.text(pd.Timestamp(OUT_OF_SAMPLE_START), ax_eq.get_ylim()[1] * 0.6,
               "  IS / OOS split", fontsize=8, color="gray")
    ax_eq.set_title("Equity curves (log scale)", fontsize=12, fontweight="bold")
    ax_eq.legend(loc="upper left", fontsize=9)
    ax_eq.grid(True, alpha=0.3)

    # (1,0) Drawdown
    ax_dd = fig.add_subplot(gs[1, 0])
    dd = eq / eq.cummax() - 1.0
    ax_dd.fill_between(dd.index, dd.values, 0, color="#c0392b", alpha=0.35)
    ax_dd.plot(dd.index, dd.values, color="#c0392b", lw=0.7)
    ax_dd.set_title("Drawdown", fontsize=11, fontweight="bold")
    ax_dd.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax_dd.grid(True, alpha=0.3)

    # (1,1) Per-year returns: strategy vs 60/40 vs BTC
    ax_yr = fig.add_subplot(gs[1, 1])
    yr_strat = per_year_returns(eq)
    yr_btc = per_year_returns(bm_btc)
    yr_6040 = per_year_returns(bm_6040)
    years = sorted(set(yr_strat.index) | set(yr_btc.index) | set(yr_6040.index))
    x = np.arange(len(years))
    w = 0.27
    ax_yr.bar(x - w, [yr_strat.get(y, 0) for y in years], w, label="Strategy",
              color="#111")
    ax_yr.bar(x,      [yr_btc.get(y, 0) for y in years],   w, label="BTC",
              color="#f7931a")
    ax_yr.bar(x + w, [yr_6040.get(y, 0) for y in years],   w, label="60/40",
              color="#2980b9")
    ax_yr.axhline(0, color="black", lw=0.6)
    ax_yr.set_xticks(x)
    ax_yr.set_xticklabels(years, rotation=0)
    ax_yr.set_title("Calendar-year returns", fontsize=11, fontweight="bold")
    ax_yr.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax_yr.legend(loc="upper left", fontsize=8)
    ax_yr.grid(True, alpha=0.3, axis="y")

    # (2,0) Rolling 252d Sharpe
    ax_rs = fig.add_subplot(gs[2, 0])
    daily_ret = res["daily_ret"]
    roll_mean = daily_ret.rolling(252).mean() * 365
    roll_vol = daily_ret.rolling(252).std() * np.sqrt(365)
    roll_sharpe = (roll_mean / roll_vol).dropna()
    ax_rs.plot(roll_sharpe.index, roll_sharpe.values, lw=1.2, color="#111")
    ax_rs.axhline(0, color="black", lw=0.4)
    ax_rs.axhline(1, color="#27ae60", lw=0.4, linestyle="--", alpha=0.6)
    ax_rs.axhline(full_s["sharpe"], color="#c0392b", lw=0.6, linestyle="--",
                  alpha=0.7, label=f"full-sample {full_s['sharpe']:.2f}")
    ax_rs.set_title("Rolling 252d Sharpe", fontsize=11, fontweight="bold")
    ax_rs.legend(loc="upper left", fontsize=8)
    ax_rs.grid(True, alpha=0.3)

    # (2,1) Sensitivity range bars
    ax_sn = fig.add_subplot(gs[2, 1])
    sens_sorted = sens.sort_values("max")
    y_pos = np.arange(len(sens_sorted))
    # Range bar from min to max
    ax_sn.barh(y_pos, sens_sorted["max"] - sens_sorted["min"],
               left=sens_sorted["min"], color="#bdc3c7", alpha=0.7, label="IS Sh range")
    # Mark default
    ax_sn.scatter(sens_sorted["default"], y_pos, color="#111", s=40, zorder=3,
                  label=f"default ({sens_sorted['default'].iloc[0]:.2f})")
    ax_sn.set_yticks(y_pos)
    ax_sn.set_yticklabels(sens_sorted.index, fontsize=9)
    ax_sn.axvline(sens_sorted["default"].iloc[0], color="#111", lw=0.4, alpha=0.4)
    ax_sn.set_title("Parameter sensitivity (IS Sharpe)", fontsize=11, fontweight="bold")
    ax_sn.set_xlabel("Sharpe (IS)", fontsize=9)
    ax_sn.legend(loc="lower right", fontsize=8)
    ax_sn.grid(True, alpha=0.3, axis="x")

    # ----- header text -----
    title = (
        f"v3 strategy  |  full Sharpe {full_s['sharpe']:.2f}  "
        f"|  bootstrap 90% CI [{ci['p05']:.2f}, {ci['p95']:.2f}]  "
        f"|  IS {is_s['sharpe']:.2f}  /  OOS {oos_s['sharpe']:.2f}  "
        f"|  MaxDD {full_s['max_dd']:.0%}"
    )
    fig.suptitle(title, fontsize=12, fontweight="bold", y=1.02)

    out_png = OUT_DIR / "tearsheet.png"
    plt.savefig(out_png, dpi=120, bbox_inches="tight")
    plt.close()
    print(f"  wrote {out_png}")

    # ----- markdown summary to stdout -----
    print("\n" + "=" * 78)
    print("MARKDOWN SUMMARY  (copy-paste into reports / chat)")
    print("=" * 78)
    print()
    print("## v3 strategy — one-page summary\n")
    print(f"**Full sample 2018-01-01 → {eq.index[-1].date()}:**")
    print(f"- CAGR **{full_s['cagr']:.1%}**, Sharpe **{full_s['sharpe']:.2f}**, "
          f"MaxDD **{full_s['max_dd']:.1%}**")
    print(f"- Bootstrap 90% CI on Sharpe: **[{ci['p05']:.2f}, {ci['p95']:.2f}]**, "
          f"P(Sh>0)={ci['p_pos']:.0%}, P(Sh>0.8)={ci['p_gt_0p8']:.0%}")
    print()
    print("**vs benchmarks (full sample):**")
    print()
    print("| series | CAGR | Sharpe | MaxDD |")
    print("|---|---|---|---|")
    print(f"| strategy v3 | {full_s['cagr']:.1%} | {full_s['sharpe']:.2f} | {full_s['max_dd']:.1%} |")
    print(f"| BTC HODL | {bm_btc_full['cagr']:.1%} | {bm_btc_full['sharpe']:.2f} | {bm_btc_full['max_dd']:.1%} |")
    print(f"| equal-weight investable | {bm_ew_full['cagr']:.1%} | {bm_ew_full['sharpe']:.2f} | {bm_ew_full['max_dd']:.1%} |")
    print(f"| 60/40 BTC/ETH | {bm_6040_full['cagr']:.1%} | {bm_6040_full['sharpe']:.2f} | {bm_6040_full['max_dd']:.1%} |")
    print()
    print("**IS vs OOS:**")
    print(f"- IS  (2018–2020): CAGR {is_s['cagr']:.1%}, Sharpe {is_s['sharpe']:.2f}, MaxDD {is_s['max_dd']:.1%}")
    print(f"- OOS (2021+):     CAGR {oos_s['cagr']:.1%}, Sharpe {oos_s['sharpe']:.2f}, MaxDD {oos_s['max_dd']:.1%}")
    print()
    print("**Sensitivity (IS Sharpe range across ±50% perturbations):**")
    print()
    print("| parameter | min IS Sh | default | max IS Sh | range |")
    print("|---|---|---|---|---|")
    for name, row in sens.iterrows():
        print(f"| {name} | {row['min']:.2f} | {row['default']:.2f} | "
              f"{row['max']:.2f} | {row['max'] - row['min']:.2f} |")
    print()
    print("See `data/tearsheet.png` for the chart pack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
