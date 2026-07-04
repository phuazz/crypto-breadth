"""
make_review_charts.py  — charts for the 2026-07-04 review records
-----------------------------------------------------------------
Reproducible chart generation for reviews/2026-07-04_*.docx. Reads the committed
results JSONs and re-runs the frozen v3.1 baseline + benchmarks. White theme,
navy primary, rounded numbers, plain captions supplied in the docx.

Run:  PYTHONIOENCODING=utf-8 python scripts/research/make_review_charts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (  # noqa: E402
    Params, PRICES_PATH, load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier, momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest, benchmark_hodl, benchmark_60_40_btc_eth,
)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "reviews" / "charts"
OUT.mkdir(parents=True, exist_ok=True)

NAVY, RED, TEAL, GREY = "#1e3a8a", "#dc2626", "#0891b2", "#9ca3af"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
                     "axes.edgecolor": "#c8ccd2", "axes.linewidth": 0.8,
                     "figure.facecolor": "white", "axes.facecolor": "white"})


def v31_equity(close, volume, p):
    mask = investability_mask_liquidity(close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    tw = build_target_weights(ranks, gate, p.rebalance_weekday)
    ex = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    return run_backtest(close, tw, p.fee_bps_per_side, lag_days=1, daily_exit_mask=ex)["equity"]


def main():
    p = Params()
    close, volume = load_prices(PRICES_PATH)
    eq = v31_equity(close, volume, p)
    btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    b6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    # 1. equity vs passive crypto (log)
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.plot(eq.index, eq.values, color=NAVY, lw=1.8, label="Strategy v3.1")
    ax.plot(btc.index, btc.values, color=RED, lw=1.1, alpha=0.8, label="BTC buy-and-hold")
    ax.plot(b6040.index, b6040.values, color=TEAL, lw=1.1, alpha=0.8, label="60/40 BTC-ETH")
    ax.set_yscale("log"); ax.set_ylabel("Growth of $1 (log)")
    ax.legend(loc="upper left", frameon=False, fontsize=10)
    ax.grid(True, alpha=0.25); ax.set_title("Strategy vs passive crypto — growth of $1", fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "equity_vs_passive.png", dpi=130); plt.close(fig)

    # 2. drawdown vs BTC
    dd = (eq / eq.cummax() - 1) * 100
    bdd = (btc / btc.cummax() - 1) * 100
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.fill_between(bdd.index, bdd.values, 0, color=RED, alpha=0.18, label=f"BTC (worst {bdd.min():.0f}%)")
    ax.fill_between(dd.index, dd.values, 0, color=NAVY, alpha=0.30, label=f"Strategy v3.1 (worst {dd.min():.0f}%)")
    ax.axhline(-30, color="#b45309", ls="--", lw=1, alpha=0.8)
    ax.text(dd.index[30], -28, "old −30% ceiling (removed)", color="#b45309", fontsize=9)
    ax.set_ylabel("Drawdown (%)"); ax.legend(loc="lower left", frameon=False, fontsize=10)
    ax.grid(True, alpha=0.25); ax.set_title("Drawdown — deep, but far shallower than passive crypto", fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "drawdown_vs_btc.png", dpi=130); plt.close(fig)

    # 3. annual returns (2021 dominance) — symlog
    ann = eq.resample("YE").last().pct_change()
    ann.iloc[0] = eq.resample("YE").last().iloc[0] - 1
    ann = ann * 100
    yrs = [d.year for d in ann.index]
    fig, ax = plt.subplots(figsize=(9, 3.8))
    cols = [NAVY if v >= 0 else RED for v in ann.values]
    ax.bar(yrs, ann.values, color=cols, alpha=0.85)
    ax.set_yscale("symlog", linthresh=50)
    ax.axhline(0, color="#555", lw=0.8)
    for x, v in zip(yrs, ann.values):
        ax.text(x, v * 1.05 if v > 0 else v * 1.05, f"{v:+.0f}%", ha="center",
                va="bottom" if v > 0 else "top", fontsize=8.5)
    ax.set_ylabel("Annual return (%, symlog)")
    ax.set_title("Annual returns — the track is dominated by 2021 (+1882%)", fontweight="bold")
    ax.grid(True, axis="y", alpha=0.2)
    fig.tight_layout(); fig.savefig(OUT / "annual_returns.png", dpi=130); plt.close(fig)

    # 4. DSR vs N
    d = json.load(open(ROOT / "results" / "phase_b_review.json"))
    ns = sorted(int(k) for k in d["dsr"]["by_n"])
    dsr = [d["dsr"]["by_n"][str(n)]["dsr"] for n in ns]
    fig, ax = plt.subplots(figsize=(7.5, 3.4))
    ax.plot(ns, dsr, "o-", color=NAVY, lw=1.8)
    ax.axhline(0.95, color=RED, ls="--", lw=1, label="0.95 significance bar")
    ax.set_ylim(0.90, 1.005); ax.set_xscale("log")
    ax.set_xlabel("Assumed number of trials (N)"); ax.set_ylabel("Deflated Sharpe (prob.)")
    ax.legend(loc="lower left", frameon=False, fontsize=10); ax.grid(True, alpha=0.25)
    ax.set_title("Edge survives trial-count deflation (DSR ≥ 0.999 to N=200)", fontweight="bold")
    fig.tight_layout(); fig.savefig(OUT / "dsr_vs_n.png", dpi=130); plt.close(fig)

    # 5. scope funnel — configs tested per arm
    arms = [("Phase B: v3.1 grid", 60), ("C.2 overlay", 5), ("C.3a shrink", 5),
            ("C.3b majors engine", 20), ("C.4 gate", 5)]
    labels = [a for a, _ in arms][::-1]; vals = [n for _, n in arms][::-1]
    fig, ax = plt.subplots(figsize=(8, 3.4))
    ax.barh(labels, vals, color=NAVY, alpha=0.85)
    for i, v in enumerate(vals):
        ax.text(v + 0.6, i, str(v), va="center", fontsize=9.5)
    ax.set_xlabel("Configurations evaluated")
    ax.set_title("The work behind the verdict: 95 configs tested → v3.1 kept, C.3 rejected, gate validated",
                 fontweight="bold", fontsize=10.5)
    ax.grid(True, axis="x", alpha=0.2)
    fig.tight_layout(); fig.savefig(OUT / "scope_funnel.png", dpi=130); plt.close(fig)

    print(f"wrote 5 charts to {OUT}")
    for f in sorted(OUT.glob("*.png")):
        print(f"  {f.name} ({f.stat().st_size//1024} KB)")


if __name__ == "__main__":
    raise SystemExit(main())
