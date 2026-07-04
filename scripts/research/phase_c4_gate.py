"""
phase_c4_gate.py  (Phase 5 — C.4 gate ablation)
-----------------------------------------------
Does the breadth gate earn its complexity, or would a simple BTC-200-day filter
(or no gate) do as well? Same universe, momentum ranking and top-N hold; only the
gross-exposure GATE changes. Evaluated full-sample, OOS, and specifically in the
post-2024 weak patch (where a gate should prove its worth).

Gates compared:
  breadth       — incumbent: % investable > 50d MA -> tiered 0/30/60/100%
  breadth_binary— (0,0,0,1) binary breadth gate
  btc200        — BTC > 200d MA -> 100% else 0%
  btc50_200     — BTC > 200d MA AND > 50d MA -> 100% else 0% (stricter)
  none          — always fully invested when momentum has picks

Writes results/phase_c4_gate.json.
Run:  PYTHONIOENCODING=utf-8 python scripts/research/phase_c4_gate.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (  # noqa: E402
    Params, PRICES_PATH, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier, momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest, summary_stats,
)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "results" / "phase_c4_gate.json"


def _f(v):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return float(v)
    except Exception:
        return None


def run_with_gate(close, volume, p, mask, exposure):
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    tw = build_target_weights(ranks, exposure, p.rebalance_weekday)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    return run_backtest(close, tw, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask)


def stats_row(name, eq):
    full = summary_stats(eq)
    oos = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])
    weak = summary_stats(eq.loc["2024-01-01":])
    return {"gate": name,
            "full_sharpe": _f(full["sharpe"]), "full_cagr": _f(full["cagr"]),
            "full_maxdd": _f(full["max_dd"]), "oos_sharpe": _f(oos["sharpe"]),
            "weak_sharpe": _f(weak["sharpe"]), "weak_maxdd": _f(weak["max_dd"])}


def main() -> int:
    p = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)

    btc = close["BTC"]
    btc_ma200 = btc.rolling(200, min_periods=200).mean()
    btc_ma50 = btc.rolling(50, min_periods=50).mean()

    gates = {
        "breadth": breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures),
        "breadth_binary": breadth_to_tier(breadth, p.tier_thresholds, (0.0, 0.0, 0.0, 1.0)),
        "btc200": (btc > btc_ma200).astype(float).reindex(close.index).fillna(0.0),
        "btc50_200": ((btc > btc_ma200) & (btc > btc_ma50)).astype(float).reindex(close.index).fillna(0.0),
        "none": pd.Series(1.0, index=close.index),
    }

    rows = []
    for name, exp in gates.items():
        eq = run_with_gate(close, volume, p, mask, exp)["equity"]
        r = stats_row(name, eq)
        rows.append(r)
        print(f"  {name:<15} full Sh={r['full_sharpe']:.3f} DD={r['full_maxdd']:.1%} | "
              f"OOS Sh={r['oos_sharpe']:.3f} | weak24+ Sh={r['weak_sharpe']:.2f} DD={r['weak_maxdd']:.1%}")

    inc = next(r for r in rows if r["gate"] == "breadth")
    btc2 = next(r for r in rows if r["gate"] == "btc200")
    none = next(r for r in rows if r["gate"] == "none")
    print(f"\nbreadth vs BTC-200d: Sharpe {inc['full_sharpe']:.3f} vs {btc2['full_sharpe']:.3f} "
          f"({inc['full_sharpe']-btc2['full_sharpe']:+.3f}); "
          f"MaxDD {inc['full_maxdd']:.1%} vs {btc2['full_maxdd']:.1%}")
    print(f"gate value (breadth vs none): Sharpe {inc['full_sharpe']-none['full_sharpe']:+.3f}, "
          f"MaxDD {inc['full_maxdd']-none['full_maxdd']:+.1%}")

    result = {"rows": rows,
              "breadth_minus_btc200_sharpe": _f(inc["full_sharpe"] - btc2["full_sharpe"]),
              "breadth_minus_none_sharpe": _f(inc["full_sharpe"] - none["full_sharpe"]),
              "breadth_minus_none_maxdd": _f(inc["full_maxdd"] - none["full_maxdd"])}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
