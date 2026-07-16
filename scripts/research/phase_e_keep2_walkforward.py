"""
PR-5 KEEP-2 — full-config walk-forward for the E.2 cap (c=0.34).

PR-1 KEEP criterion (2): "the proper expand-window annual re-fit; OOS Sharpe-loss
vs the frozen config must be <= 0.30." PR-5 ran KEEP-1 (DSR) but not KEEP-2, so
the E.2 cap could not replace v3.1. This closes that gap.

Protocol — identical to the Phase-B full-config walk-forward
(`scripts/research/phase_b_review.py`), so the capped number is comparable to the
frozen +0.080 on record:
  for each anchor year Y in 2020..2026:
    pick the IS-best config over the grid on data up to (Y-1)-12-31,
    apply it to year Y only, chain year Y's daily returns.
  wf_loss = frozen_sharpe - refit_sharpe   (positive => re-fitting LOSES)

Run BOTH uncapped and capped on the CURRENT parquet. The +0.080 on record was
measured on data ending ~2026-07-03; the parquet has since advanced, so the
uncapped arm is re-run here to give a like-for-like control rather than
comparing the capped number against a stale figure.

The cap is applied AFTER the gate multiply (per-name clip, residual to cash), so
`ranks` and `gate` are identical between the capped and uncapped arms. They are
computed once per config and shared — the two arms differ only in the clip.

The frozen v3.1 engine is NOT modified.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import (  # noqa: E402
    Params, PRICES_PATH, load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier, per_coin_trend_entry_mask,
    per_coin_trend_exit_mask, momentum_score, rank_top_n, run_backtest,
    summary_stats,
)
from phase_e_concentration import build_tw  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = PROJECT_ROOT / "results"

CAP = 0.34                 # E.2, as adopted-pending-KEEP-2
WF_LOSS_TOLERANCE = 0.30   # PR-1 KEEP (2), frozen 2026-07-04
ANCHORS = [2020, 2021, 2022, 2023, 2024, 2025, 2026]


def _f(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def walk_forward(eq_by_key: dict, base_eq: pd.Series, default_key, data_end: str) -> dict:
    """Phase-B full-config walk-forward, verbatim in protocol."""
    rows, chained = [], pd.Series(dtype=float)
    for Y in ANCHORS:
        is_end = f"{Y-1}-12-31"
        oos_start, oos_end = f"{Y}-01-01", min(f"{Y}-12-31", data_end)
        cands = []
        for key, eq in eq_by_key.items():
            is_eq = eq.loc[:is_end]
            if len(is_eq) < 60:
                continue
            cands.append((summary_stats(is_eq)["sharpe"], key))
        cands = [c for c in cands if c[0] == c[0]]
        if not cands:
            continue
        cands.sort(key=lambda c: (-c[0], str(c[1])))
        best_key = cands[0][1]
        oos_eq = eq_by_key[best_key].loc[oos_start:oos_end]
        if len(oos_eq) >= 20:
            chained = pd.concat([chained, oos_eq.pct_change().dropna()])
        rows.append({"anchor": Y, "best": str(best_key),
                     "picked_default": best_key == default_key,
                     "is_sharpe": _f(cands[0][0])})
    chained = chained.sort_index()
    wf_eq = (1 + chained).cumprod()
    frozen_oos = base_eq.loc["2020-01-01":]
    frozen_oos = frozen_oos / frozen_oos.iloc[0]
    wf_sharpe = summary_stats(wf_eq)["sharpe"]
    frozen_sharpe = summary_stats(frozen_oos)["sharpe"]
    return {
        "refit_sharpe": _f(wf_sharpe), "frozen_sharpe": _f(frozen_sharpe),
        "loss_frozen_minus_refit": _f(frozen_sharpe - wf_sharpe),
        "n_default_picks": sum(1 for r in rows if r["picked_default"]),
        "n_anchors": len(rows), "rows": rows,
    }


def main() -> int:
    p = Params()
    print(f"PR-5 KEEP-2 — full-config walk-forward, E.2 cap c={CAP}\n")
    close, volume = load_prices(PRICES_PATH)
    data_end = str(close.index[-1].date())
    print(f"  panel {close.shape[0]} dates x {close.shape[1]} symbols, ends {data_end}")

    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)

    # Phase-B full-config grid (4 x 5 x 3 = 60).
    lookbacks = [(21, 63, 126), (30, 90, 180), (10, 30, 60), (14, 42, 84)]
    topns = [2, 3, 4, 5, 6]
    breadths = [30, 50, 70]
    default_key = (p.momentum_lookbacks_d, p.rank_top_n, p.breadth_ma_window)
    print(f"  grid: {len(lookbacks)*len(topns)*len(breadths)} configs x 2 arms "
          f"(uncapped control + capped)\n")

    gates = {bm: breadth_to_tier(breadth_pct_above_ma(close, bm, mask),
                                 p.tier_thresholds, p.tier_exposures)
             for bm in breadths}
    eq_unc, eq_cap = {}, {}
    for i, lb in enumerate(lookbacks):
        mom = momentum_score(close, lb, mask).where(entry)
        for tn in topns:
            ranks = rank_top_n(mom, tn)   # expensive; shared by both arms
            for bm in breadths:
                key = (lb, tn, bm)
                for tag, cap, store in (("unc", None, eq_unc), ("cap", CAP, eq_cap)):
                    tw = build_tw(ranks, gates[bm], p.rebalance_weekday, cap=cap)
                    res = run_backtest(close, tw, p.fee_bps_per_side, lag_days=1,
                                       daily_exit_mask=exit_mask)
                    store[key] = res["equity"]
        print(f"  [{i+1}/{len(lookbacks)}] lookbacks {lb} done", flush=True)

    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "cap": CAP, "tolerance": WF_LOSS_TOLERANCE, "data_end": data_end,
           "grid_size": len(eq_unc), "arms": {}}
    print()
    for tag, store, label in (("uncapped", eq_unc, "frozen v3.1 (control)"),
                              ("capped", eq_cap, f"v3.1 + E.2 cap c={CAP}")):
        wf = walk_forward(store, store[default_key], default_key, data_end)
        base = summary_stats(store[default_key])
        wf["full_sharpe"] = _f(base["sharpe"])
        wf["full_max_dd"] = _f(base["max_dd"])
        wf["pass"] = bool(wf["loss_frozen_minus_refit"] <= WF_LOSS_TOLERANCE)
        out["arms"][tag] = wf
        print(f"{label}")
        print(f"  full-sample Sharpe {base['sharpe']:.3f}  MaxDD {base['max_dd']:.1%}")
        print(f"  WF: refit Sh {wf['refit_sharpe']:.3f} vs frozen Sh "
              f"{wf['frozen_sharpe']:.3f}  loss {wf['loss_frozen_minus_refit']:+.3f}  "
              f"(tolerance <= {WF_LOSS_TOLERANCE})  ->  "
              f"{'PASS' if wf['pass'] else 'FAIL'}")
        print(f"  default config picked {wf['n_default_picks']}/{wf['n_anchors']} anchors")
        for r in wf["rows"]:
            print(f"    {r['anchor']}  IS-best {r['best']:<28} "
                  f"IS Sh {r['is_sharpe']:.2f}{'  (= default)' if r['picked_default'] else ''}")
        print()

    (RESULTS / "phase_e_keep2_walkforward.json").write_text(json.dumps(out, indent=2),
                                                            encoding="utf-8")
    print(f"Wrote {RESULTS / 'phase_e_keep2_walkforward.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
