"""
walk_forward_refit.py
---------------------
Proper expand-window walk-forward with annual re-fitting.

Protocol (no peeking):
  For each anchor year Y in [2020, 2021, ..., 2026]:
    1. IS window: 2018-01-01 to (Y-1)-12-31
    2. Run every config in GRID over the full history; evaluate IS Sharpe only.
    3. Pick the IS-best config.
    4. Apply that config's equity curve to year Y. Record OOS-Y stats.
    5. Chain year Y's daily returns into the walk-forward equity curve.

Three equity curves are compared at the end:
  - "default":  current production Params, no re-fitting.
  - "is_tuned_once": IS-best config picked on 2018-01-01 to 2020-12-31, then
                     applied unchanged across all OOS years (the naive
                     "tune once, deploy forever" approach).
  - "walk_forward_refit": the proper one — best config re-picked each year.

If walk_forward_refit beats is_tuned_once on OOS Sharpe, tuning is defensible.
If it does not, the strategy is best left at defaults.

Also reports parameter STABILITY — how often each (top_n, lookbacks) tuple was
picked across the seven anchor years. Stable picks = a real signal in the
parameter; unstable picks = the search is noise.

Output:
  data/walk_forward.json   — consumed by scripts/pipeline.py
  stdout                   — full diagnostic block

Runtime: ~3-5 minutes (42 backtests over 8 years of daily data each).
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
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
    summary_stats,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = PROJECT_ROOT / "data" / "walk_forward.json"


# ----- production pipeline wrapper -----------------------------------------

def run_v3(close: pd.DataFrame, volume: pd.DataFrame, p: Params):
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
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    return run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )


# ----- grid ----------------------------------------------------------------

# Two dimensions only — the two with the largest IS Sharpe spread in the
# sensitivity sweep, and the two most defensibly "tunable" without changing
# the strategy spec on the dashboard cards.
GRID = []
for top_n in [3, 4, 5]:
    for lb in [(21, 63, 126), (30, 90, 180)]:
        GRID.append({"rank_top_n": top_n, "momentum_lookbacks_d": lb})


def config_label(cfg: dict) -> str:
    lb = cfg["momentum_lookbacks_d"]
    lb_label = "fast(21,63,126)" if lb == (21, 63, 126) else "slow(30,90,180)"
    return f"top{cfg['rank_top_n']}+{lb_label}"


# ----- helpers -------------------------------------------------------------

def _f(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def stats_for(eq: pd.Series) -> dict:
    s = summary_stats(eq)
    return {"cagr": _f(s["cagr"]), "sharpe": _f(s["sharpe"]),
            "max_dd": _f(s["max_dd"]), "vol": _f(s["vol"])}


# ----- main walk-forward ---------------------------------------------------

def main() -> int:
    p_base = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)
    print(f"  {close.shape[0]} dates x {close.shape[1]} symbols")
    print(f"  grid size: {len(GRID)} configs")

    # Run every config ONCE over the full history. We'll then slice by year.
    print("\n=== Running grid (full history per config) ===")
    config_results = {}
    for i, cfg in enumerate(GRID):
        label = config_label(cfg)
        print(f"  [{i+1}/{len(GRID)}] {label} ...", flush=True)
        p_var = replace(p_base, **cfg)
        res = run_v3(close, volume, p_var)
        config_results[label] = {"config": cfg, "result": res}

    # Default config — what the production strategy does today (no tuning).
    print("  [baseline] default Params ...")
    res_default = run_v3(close, volume, p_base)
    default_eq = res_default["equity"]
    default_label = config_label({
        "rank_top_n": p_base.rank_top_n,
        "momentum_lookbacks_d": p_base.momentum_lookbacks_d,
    })

    # ---- pick per-year IS-best, evaluate on OOS year ----------------------
    print("\n=== Per-anchor-year IS pick / OOS evaluation ===")
    anchor_years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    anchor_rows = []
    for Y in anchor_years:
        is_end = f"{Y-1}-12-31"
        oos_start = f"{Y}-01-01"
        # Cap OOS end at the data end so 2026 (partial) still works.
        oos_end = min(f"{Y}-12-31", str(close.index[-1].date()))

        # Grid search on IS only
        candidates = []
        for label, info in config_results.items():
            is_eq = info["result"]["equity"].loc[:is_end]
            if len(is_eq) < 60:
                continue
            is_sharpe = summary_stats(is_eq)["sharpe"]
            candidates.append({"label": label, "config": info["config"], "is_sharpe": is_sharpe})

        # Tie-break by sorted label to be deterministic
        candidates.sort(key=lambda c: (-(c["is_sharpe"] if not np.isnan(c["is_sharpe"]) else -999),
                                       c["label"]))
        best = candidates[0]
        best_label = best["label"]
        best_cfg = best["config"]
        best_is_sharpe = best["is_sharpe"]

        # OOS performance for this year under the IS-best config
        oos_eq = config_results[best_label]["result"]["equity"].loc[oos_start:oos_end]
        if len(oos_eq) >= 20:
            oos_s = summary_stats(oos_eq)
        else:
            oos_s = {"cagr": np.nan, "sharpe": np.nan, "max_dd": np.nan, "vol": np.nan}

        # Default's OOS that same year (for comparison)
        default_oos_eq = default_eq.loc[oos_start:oos_end]
        if len(default_oos_eq) >= 20:
            default_oos_s = summary_stats(default_oos_eq)
        else:
            default_oos_s = {"cagr": np.nan, "sharpe": np.nan}

        is_sh_str = f"{best_is_sharpe:.2f}" if not np.isnan(best_is_sharpe) else "nan"
        oos_sh = oos_s["sharpe"]
        oos_sh_str = f"{oos_sh:+.2f}" if not (oos_sh is None or np.isnan(oos_sh)) else "nan"
        print(f"  Y={Y}  IS=[2018->{is_end}]  best={best_label:<28}  "
              f"IS Sh={is_sh_str}  OOS Sh={oos_sh_str}", flush=True)

        anchor_rows.append({
            "anchor_year": Y,
            "is_end": is_end,
            "oos_start": oos_start,
            "oos_end": oos_end,
            "best_label": best_label,
            "best_config": best_cfg,
            "is_sharpe": _f(best_is_sharpe),
            "oos": {"cagr": _f(oos_s["cagr"]), "sharpe": _f(oos_s["sharpe"]),
                    "max_dd": _f(oos_s["max_dd"])},
            "default_oos": {"cagr": _f(default_oos_s.get("cagr")), "sharpe": _f(default_oos_s.get("sharpe"))},
            "all_is": [{"label": c["label"], "is_sharpe": _f(c["is_sharpe"])} for c in candidates],
        })

    # ---- build chained walk-forward equity curve -------------------------
    print("\n=== Chaining walk-forward equity ===")
    chained_returns = pd.Series(dtype=float)
    for row in anchor_rows:
        Y = row["anchor_year"]
        best_label = row["best_label"]
        eq_year = config_results[best_label]["result"]["equity"].loc[row["oos_start"]:row["oos_end"]]
        ret_year = eq_year.pct_change().dropna()
        chained_returns = pd.concat([chained_returns, ret_year])
    chained_returns = chained_returns.sort_index()
    chained_eq = (1.0 + chained_returns).cumprod()
    # Prepend a 1.0 anchor so the curve starts at 1.0
    first_date = chained_returns.index[0] - pd.Timedelta(days=1)
    chained_eq = pd.concat([pd.Series([1.0], index=[first_date]), chained_eq])
    wf_stats_full = summary_stats(chained_eq)

    # ---- IS-tuned-once curve (best on 2018-2020, frozen) ------------------
    is_once_end = "2020-12-31"
    is_once_candidates = []
    for label, info in config_results.items():
        is_sh = summary_stats(info["result"]["equity"].loc[:is_once_end])["sharpe"]
        is_once_candidates.append({"label": label, "config": info["config"], "is_sharpe": is_sh})
    is_once_candidates.sort(key=lambda c: -(c["is_sharpe"] if not np.isnan(c["is_sharpe"]) else -999))
    is_once_pick = is_once_candidates[0]
    print(f"\nIS-tuned-once (frozen): {is_once_pick['label']} "
          f"(IS Sharpe {is_once_pick['is_sharpe']:.2f})")

    # Slice the IS-tuned-once equity to start from 2020-01-01 so it lines up
    # with the walk-forward curve.
    is_once_full_eq = config_results[is_once_pick["label"]]["result"]["equity"]
    is_once_oos_eq = is_once_full_eq.loc["2020-01-01":]
    is_once_oos_eq = is_once_oos_eq / is_once_oos_eq.iloc[0]

    # And the default
    default_oos_eq = default_eq.loc["2020-01-01":]
    default_oos_eq = default_oos_eq / default_oos_eq.iloc[0]

    # ---- compare aggregate OOS stats (2020+) ------------------------------
    print("\n=== Aggregate OOS comparison (2020-01-01 onwards) ===")
    print(f"  {'variant':<22} {'CAGR':>8} {'Sharpe':>7} {'MaxDD':>8}")
    rows_summary = []
    for name, eq in [("default", default_oos_eq),
                      ("is_tuned_once", is_once_oos_eq),
                      ("walk_forward_refit", chained_eq)]:
        s = summary_stats(eq)
        print(f"  {name:<22} {s['cagr']:>7.1%} {s['sharpe']:>7.2f} {s['max_dd']:>7.1%}")
        rows_summary.append({
            "variant": name,
            "cagr": _f(s["cagr"]),
            "sharpe": _f(s["sharpe"]),
            "max_dd": _f(s["max_dd"]),
        })

    # ---- parameter stability ---------------------------------------------
    print("\n=== Parameter stability across re-fits ===")
    label_counts = {}
    for row in anchor_rows:
        label_counts[row["best_label"]] = label_counts.get(row["best_label"], 0) + 1
    # Total anchors with at least 20 OOS days (i.e. actually evaluated)
    n_anchors = len(anchor_rows)
    for label, count in sorted(label_counts.items(), key=lambda kv: -kv[1]):
        pct = count / n_anchors * 100
        print(f"  {label:<28} picked {count}/{n_anchors} times ({pct:.0f}%)")

    # ---- serialize for dashboard -----------------------------------------
    # Down-sample equity series for the dashboard
    def to_weekly(s):
        return s.resample("W-MON").last().dropna()

    chained_w = to_weekly(chained_eq)
    is_once_w = to_weekly(is_once_oos_eq)
    default_w = to_weekly(default_oos_eq)

    payload = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "grid": [
                {"label": config_label(c), "params": {
                    "rank_top_n": c["rank_top_n"],
                    "momentum_lookbacks_d": list(c["momentum_lookbacks_d"]),
                }} for c in GRID
            ],
            "n_anchors": n_anchors,
            "anchor_start_year": anchor_years[0],
            "anchor_end_year": anchor_years[-1],
            "default_label": default_label,
            "is_once_label": is_once_pick["label"],
        },
        "anchors": anchor_rows,
        "summary": {
            "default": rows_summary[0],
            "is_tuned_once": rows_summary[1],
            "walk_forward_refit": rows_summary[2],
        },
        "param_stability": [
            {"label": label, "count": int(count),
             "share": count / n_anchors}
            for label, count in sorted(label_counts.items(), key=lambda kv: -kv[1])
        ],
        "equity_chained": {
            "dates": chained_w.index.strftime("%Y-%m-%d").tolist(),
            "values": [_f(v) for v in chained_w.values],
        },
        "equity_is_once": {
            "dates": is_once_w.index.strftime("%Y-%m-%d").tolist(),
            "values": [_f(v) for v in is_once_w.values],
        },
        "equity_default": {
            "dates": default_w.index.strftime("%Y-%m-%d").tolist(),
            "values": [_f(v) for v in default_w.values],
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"\nWrote {OUT_JSON} ({OUT_JSON.stat().st_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
