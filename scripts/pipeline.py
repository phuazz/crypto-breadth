"""
pipeline.py
-----------
Canonical build script (per vault convention). Runs the v3 backtest,
computes every number the dashboard needs, packages it as JSON, injects
into template.html, and writes docs/index.html.

Inputs:
  data/prices.parquet      (from scripts/fetch_data.py)
  template.html            (the source dashboard template)

Outputs:
  data/dashboard_data.json (sidecar for dev mode / fetch fallback)
  docs/index.html          (production microsite — served by GitHub Pages)

Run order:
  python scripts/fetch_data.py     # daily
  python scripts/pipeline.py       # after fetch or any signal change
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
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    benchmark_hodl, benchmark_equal_weight, benchmark_60_40_btc_eth,
    summary_stats, regime_segments,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = PROJECT_ROOT / "template.html"
DOCS_DIR = PROJECT_ROOT / "docs"
DATA_JSON_PATH = PROJECT_ROOT / "data" / "dashboard_data.json"
WALK_FORWARD_JSON = PROJECT_ROOT / "data" / "walk_forward.json"


# ----- production pipeline --------------------------------------------------

def run_v3(close: pd.DataFrame, volume: pd.DataFrame, p: Params) -> tuple[dict, pd.DataFrame]:
    """Returns (result_dict, mask)."""
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
    res = run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )
    return res, mask


# ----- analysis helpers -----------------------------------------------------

def bootstrap_sharpe(daily_ret: pd.Series, *, n_boot: int = 2000,
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

    # Histogram for plotting
    counts, edges = np.histogram(sharpes, bins=50)
    bins_mid = (edges[:-1] + edges[1:]) / 2.0
    return {
        "p05": float(np.quantile(sharpes, 0.05)),
        "p25": float(np.quantile(sharpes, 0.25)),
        "p50": float(np.quantile(sharpes, 0.50)),
        "p75": float(np.quantile(sharpes, 0.75)),
        "p95": float(np.quantile(sharpes, 0.95)),
        "mean": float(sharpes.mean()),
        "p_positive": float((sharpes > 0).mean()),
        "p_gt_0p8": float((sharpes > 0.8).mean()),
        "counts": counts.tolist(),
        "bins_mid": bins_mid.tolist(),
    }


def per_year_returns(eq: pd.Series) -> pd.Series:
    yearly = eq.groupby(eq.index.year).agg(["first", "last"])
    return (yearly["last"] / yearly["first"] - 1.0)


def per_coin_attribution(close: pd.DataFrame, result: dict) -> pd.DataFrame:
    weights = result["weights"]
    equity = result["equity"]
    daily_ret = close.pct_change().fillna(0.0)
    eq_lag = equity.shift(1).fillna(1.0)
    w_lag = weights.shift(1).fillna(0.0)
    return w_lag.mul(daily_ret, axis=0).mul(eq_lag, axis=0)


def extract_trade_history(close: pd.DataFrame, result: dict) -> list[dict]:
    """Decompose every trade day into per-coin events.

    A trade day is any day where the backtest's `turnover` series is > 0 —
    that captures both weekly rebalances and daily trend-exit overrides.

    For each trade day, the previous day's weights are drifted forward by
    today's returns (then re-normalised, including idle cash). The
    difference between the actual end-of-day weight and the drifted weight
    is the trade — anything else is just price drift and we ignore it.

    Threshold: 0.5% absolute weight change per coin to count as a trade.
    """
    weights = result["weights"]
    turnover = result["turnover"]
    daily_exits = result["daily_exit_count"]
    daily_ret = close.pct_change().fillna(0.0)

    trade_days = turnover[turnover > 1e-4].index
    events = []
    for dt in trade_days:
        idx = weights.index.get_loc(dt)
        if idx == 0:
            continue
        w_curr = weights.iloc[idx]
        w_prev = weights.iloc[idx - 1]
        r = daily_ret.iloc[idx]
        prev_cash = max(0.0, 1.0 - w_prev.sum())
        new_values = w_prev * (1.0 + r)
        total = new_values.sum() + prev_cash
        if total > 0:
            w_drift = new_values / total
        else:
            w_drift = new_values * 0.0

        is_exit_day = bool(daily_exits.loc[dt] > 0)
        is_rebal_day = bool(turnover.loc[dt] > 0)
        # A day can have both — label by what fired.
        if is_exit_day and is_rebal_day:
            trigger = "rebal + exit"
        elif is_exit_day:
            trigger = "daily exit"
        else:
            trigger = "rebal"

        for coin in w_curr.index:
            actual = float(w_curr[coin])
            drifted = float(w_drift[coin])
            delta = actual - drifted
            if abs(delta) < 0.005:
                continue
            if drifted < 0.005 and actual >= 0.005:
                action = "entry"
            elif drifted >= 0.005 and actual < 0.005:
                action = "exit"
            else:
                action = "resize"
            events.append({
                "date": str(dt.date()),
                "trigger": trigger,
                "coin": coin,
                "action": action,
                "old_w": drifted,
                "new_w": actual,
                "delta": delta,
            })

    # Newest first
    events.sort(key=lambda e: e["date"], reverse=True)
    return events


def current_state(
    close: pd.DataFrame, volume: pd.DataFrame, result: dict,
    breadth: pd.Series, target_exposure: pd.Series, mask: pd.DataFrame, p: Params,
) -> dict:
    """Latest signal — what the strategy would have you holding right now."""
    weights = result["weights"]
    turnover = result["turnover"]
    daily_exits = result["daily_exit_count"]

    last_date = weights.index[-1]
    last_w = weights.iloc[-1]
    holdings = []
    for c in last_w.index:
        w = float(last_w[c])
        if w > 0.005:
            holdings.append({"coin": c, "weight": w})
    holdings.sort(key=lambda h: -h["weight"])
    cash_weight = float(max(0.0, 1.0 - last_w.sum()))

    last_breadth = float(breadth.iloc[-1])
    last_exposure = float(target_exposure.iloc[-1])
    if last_exposure <= 0.01:
        tier_label = "0% — all cash"
    elif last_exposure <= 0.31:
        tier_label = "30% tier"
    elif last_exposure <= 0.61:
        tier_label = "60% tier"
    else:
        tier_label = "100% — full risk"

    trade_days = turnover[turnover > 1e-4].index
    exit_days = daily_exits[daily_exits > 0].index
    last_rebal = trade_days[-1] if len(trade_days) > 0 else None
    last_exit = exit_days[-1] if len(exit_days) > 0 else None

    investable_today = int(mask.loc[last_date].sum())
    investable_names = sorted(mask.columns[mask.loc[last_date]].tolist())

    return {
        "as_of": str(last_date.date()),
        "holdings": holdings,
        "cash_weight": cash_weight,
        "breadth": last_breadth,
        "exposure": last_exposure,
        "tier_label": tier_label,
        "investable_today": investable_today,
        "investable_names": investable_names,
        "last_rebal": str(last_rebal.date()) if last_rebal is not None else None,
        "last_exit": str(last_exit.date()) if last_exit is not None else None,
        "days_since_rebal": int((last_date - last_rebal).days) if last_rebal is not None else None,
        "days_since_exit": int((last_date - last_exit).days) if last_exit is not None else None,
        "n_rebal_days": int(len(trade_days)),
        "n_exit_days": int(len(exit_days)),
    }


def regime_breakdown(eq: pd.Series, bm_btc: pd.Series, bm_6040: pd.Series) -> list[dict]:
    """Strategy + key benchmarks per regime window."""
    rows = []
    for label, start, end in regime_segments():
        eq_sub = eq.loc[start:end]
        if len(eq_sub) < 30:
            continue
        s = summary_stats(eq_sub)
        btc_sub = bm_btc.loc[start:end]
        bm6040_sub = bm_6040.loc[start:end]
        s_btc = summary_stats(btc_sub) if len(btc_sub) >= 30 else {"cagr": np.nan}
        s_6040 = summary_stats(bm6040_sub) if len(bm6040_sub) >= 30 else {"cagr": np.nan}
        # Win verdict
        max_bench = max(s_btc["cagr"], s_6040["cagr"])
        if s["cagr"] > max_bench + 0.05:
            verdict = "strategy wins"
        elif s["cagr"] < max_bench - 0.05:
            verdict = "strategy lags"
        else:
            verdict = "in-line"
        rows.append({
            "label": label.replace("_", " "),
            "start": start,
            "end": end[:10] if end != "2030-12-31" else "now",
            "strat_cagr": _f(s["cagr"]),
            "strat_sharpe": _f(s["sharpe"]),
            "strat_max_dd": _f(s["max_dd"]),
            "btc_cagr": _f(s_btc["cagr"]),
            "bm_6040_cagr": _f(s_6040["cagr"]),
            "verdict": verdict,
        })
    return rows


def sensitivity_sweep(close: pd.DataFrame, volume: pd.DataFrame,
                      p_default: Params) -> list[dict]:
    """Same protocol as scripts/sensitivity.py — IS Sharpe ranges per param."""
    base_res, _ = run_v3(close, volume, p_default)
    base_is_sh = summary_stats(base_res["equity"].loc[:IN_SAMPLE_END])["sharpe"]

    grids = {
        "breadth_ma_window": [25, 50, 75, 100, 200],
        "rank_top_n": [1, 2, 3, 4, 5, 7],
        "per_coin_trend_window": [25, 50, 75, 100, 200],
        "liquidity_min_adv_usd": [10e6, 25e6, 50e6, 100e6],
    }
    tuple_grids = {
        "momentum_lookbacks_d": [(10,30,60),(14,42,84),(21,63,126),(30,90,180),(63,)],
    }
    rows = []
    for param_name, values in grids.items():
        sharpes = []
        for v in values:
            p_var = replace(p_default, **{param_name: v})
            try:
                res, _ = run_v3(close, volume, p_var)
                sharpes.append(summary_stats(res["equity"].loc[:IN_SAMPLE_END])["sharpe"])
            except Exception:
                continue
        if not sharpes:
            continue
        rows.append({
            "param": param_name,
            "min": _f(min(sharpes)),
            "default": _f(base_is_sh),
            "max": _f(max(sharpes)),
        })
    for param_name, values in tuple_grids.items():
        sharpes = []
        for v in values:
            p_var = replace(p_default, **{param_name: v})
            try:
                res, _ = run_v3(close, volume, p_var)
                sharpes.append(summary_stats(res["equity"].loc[:IN_SAMPLE_END])["sharpe"])
            except Exception:
                continue
        if not sharpes:
            continue
        rows.append({
            "param": param_name,
            "min": _f(min(sharpes)),
            "default": _f(base_is_sh),
            "max": _f(max(sharpes)),
        })
    return rows


# ----- serialisers ----------------------------------------------------------

def _f(v):
    """JSON-safe float (NaN -> None)."""
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def _dates(idx: pd.DatetimeIndex) -> list[str]:
    return idx.strftime("%Y-%m-%d").tolist()


def _series(s: pd.Series) -> list:
    return [_f(x) for x in s.values]


def downsample_to_weekly(s: pd.Series) -> pd.Series:
    """Keep the daily curve readable in the browser. Weekly resample is plenty for charts."""
    return s.resample("W-MON").last().dropna()


# ----- main -----------------------------------------------------------------

def main() -> int:
    p = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)
    print(f"  {close.shape[0]} dates x {close.shape[1]} symbols")

    print("Running v3 production strategy ...")
    res, mask = run_v3(close, volume, p)
    eq = res["equity"]

    print("Building benchmarks ...")
    bm_btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    bm_ew = benchmark_equal_weight(close, mask).reindex(eq.index).ffill()
    bm_6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    print("Computing summary stats ...")
    s_full = summary_stats(eq)
    s_is = summary_stats(eq.loc[:IN_SAMPLE_END])
    s_oos = summary_stats(eq.loc[OUT_OF_SAMPLE_START:])

    is_oos_data = {}
    for key, series in [("strategy", eq), ("btc", bm_btc), ("ew", bm_ew), ("bm_6040", bm_6040)]:
        s_is_b = summary_stats(series.loc[:IN_SAMPLE_END])
        s_oos_b = summary_stats(series.loc[OUT_OF_SAMPLE_START:])
        is_oos_data[key] = {
            "is": {"cagr": _f(s_is_b["cagr"]), "sharpe": _f(s_is_b["sharpe"]), "max_dd": _f(s_is_b["max_dd"])},
            "oos": {"cagr": _f(s_oos_b["cagr"]), "sharpe": _f(s_oos_b["sharpe"]), "max_dd": _f(s_oos_b["max_dd"])},
        }

    print("Bootstrapping Sharpe distribution ...")
    boot = bootstrap_sharpe(res["daily_ret"])

    print("Computing yearly returns ...")
    yr_strat = per_year_returns(eq)
    yr_btc = per_year_returns(bm_btc)
    yr_ew = per_year_returns(bm_ew)
    yr_6040 = per_year_returns(bm_6040)
    years = sorted(set(yr_strat.index) | set(yr_btc.index) | set(yr_6040.index))
    yearly = {
        "years": [int(y) for y in years],
        "strategy": [_f(yr_strat.get(y, np.nan)) for y in years],
        "btc": [_f(yr_btc.get(y, np.nan)) for y in years],
        "ew": [_f(yr_ew.get(y, np.nan)) for y in years],
        "6040": [_f(yr_6040.get(y, np.nan)) for y in years],
    }

    print("Computing rolling Sharpe ...")
    daily_ret = res["daily_ret"]
    roll_sharpe = (daily_ret.rolling(252).mean() * 365) / (daily_ret.rolling(252).std() * np.sqrt(365))
    roll_sharpe = roll_sharpe.dropna()
    # Downsample to weekly for chart payload
    rs_weekly = downsample_to_weekly(roll_sharpe)

    print("Regime breakdown ...")
    regimes = regime_breakdown(eq, bm_btc, bm_6040)

    print("Per-coin attribution ...")
    contrib = per_coin_attribution(close, res)
    full_contrib = contrib.sum(axis=0).sort_values(ascending=False)
    oos_contrib = contrib.loc[OUT_OF_SAMPLE_START:].sum(axis=0).reindex(full_contrib.index)
    attribution = {
        "coins": full_contrib.index.tolist(),
        "full": [_f(x) for x in full_contrib.values],
        "oos": [_f(x) for x in oos_contrib.values],
    }

    print("Current state (latest signal) ...")
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    target_exposure = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    monitor = current_state(close, volume, res, breadth, target_exposure, mask, p)
    print(f"  as of {monitor['as_of']}: {len(monitor['holdings'])} holdings, "
          f"cash {monitor['cash_weight']:.0%}, breadth {monitor['breadth']:.0%}")

    print("Extracting trade history ...")
    trades = extract_trade_history(close, res)
    print(f"  {len(trades)} trade events")

    print("Parameter sensitivity sweep ...")
    sensitivity = sensitivity_sweep(close, volume, p)

    # ----- assemble payload -----
    print("Building payload ...")
    eq_weekly = downsample_to_weekly(eq)
    btc_weekly = downsample_to_weekly(bm_btc)
    ew_weekly = downsample_to_weekly(bm_ew)
    bm6040_weekly = downsample_to_weekly(bm_6040)

    # Drawdown stays daily — short to compute, important for visual.
    dd = (eq / eq.cummax() - 1.0)
    dd_weekly = downsample_to_weekly(dd)

    payload = {
        "meta": {
            "version": "v3",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sample_start": str(eq.index[0].date()),
            "sample_end": str(eq.index[-1].date()),
            "is_end": IN_SAMPLE_END,
            "oos_start": OUT_OF_SAMPLE_START,
        },
        "summary": {
            "full": {"cagr": _f(s_full["cagr"]), "sharpe": _f(s_full["sharpe"]),
                     "max_dd": _f(s_full["max_dd"]), "vol": _f(s_full["vol"])},
            "is":   {"cagr": _f(s_is["cagr"]), "sharpe": _f(s_is["sharpe"]),
                     "max_dd": _f(s_is["max_dd"]), "vol": _f(s_is["vol"])},
            "oos":  {"cagr": _f(s_oos["cagr"]), "sharpe": _f(s_oos["sharpe"]),
                     "max_dd": _f(s_oos["max_dd"]), "vol": _f(s_oos["vol"])},
        },
        "benchmarks": {
            "btc": {"cagr": _f(summary_stats(bm_btc)["cagr"]), "sharpe": _f(summary_stats(bm_btc)["sharpe"]),
                    "max_dd": _f(summary_stats(bm_btc)["max_dd"])},
            "ew": {"cagr": _f(summary_stats(bm_ew)["cagr"]), "sharpe": _f(summary_stats(bm_ew)["sharpe"]),
                   "max_dd": _f(summary_stats(bm_ew)["max_dd"])},
            "bm_6040": {"cagr": _f(summary_stats(bm_6040)["cagr"]), "sharpe": _f(summary_stats(bm_6040)["sharpe"]),
                        "max_dd": _f(summary_stats(bm_6040)["max_dd"])},
        },
        "is_oos": is_oos_data,
        "bootstrap": boot,
        "equity": {
            "dates": _dates(eq_weekly.index),
            "strategy": _series(eq_weekly),
            "btc": _series(btc_weekly),
            "ew": _series(ew_weekly),
            "6040": _series(bm6040_weekly),
        },
        "drawdown": {
            "dates": _dates(dd_weekly.index),
            "strategy": _series(dd_weekly),
        },
        "rolling_sharpe": {
            "dates": _dates(rs_weekly.index),
            "values": _series(rs_weekly),
        },
        "yearly_returns": yearly,
        "regimes": regimes,
        "sensitivity": sensitivity,
        "attribution": attribution,
        "monitor": monitor,
        "trades": trades,
    }

    # ----- merge walk-forward results if present ----------------
    if WALK_FORWARD_JSON.exists():
        try:
            wf = json.loads(WALK_FORWARD_JSON.read_text(encoding="utf-8"))
            payload["walk_forward"] = wf
            print(f"  merged walk-forward results ({len(wf.get('anchors', []))} anchors)")
        except Exception as e:
            print(f"  warn: could not load walk-forward JSON: {e!r}")

    # ----- write JSON sidecar -----
    DATA_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_JSON_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {DATA_JSON_PATH} "
          f"({DATA_JSON_PATH.stat().st_size / 1024:.1f} KB)")

    # ----- inject into template -----
    print("Injecting into template -> docs/index.html ...")
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload_json = json.dumps(payload, separators=(",", ":"))
    if "{{DATA_PLACEHOLDER}}" not in template:
        print("  ERROR: template.html is missing the {{DATA_PLACEHOLDER}} marker.")
        return 1
    output = template.replace("{{DATA_PLACEHOLDER}}", payload_json)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DOCS_DIR / "index.html"
    out_path.write_text(output, encoding="utf-8")
    print(f"  wrote {out_path} ({out_path.stat().st_size / 1024:.1f} KB)")

    # Also drop a small .nojekyll so GitHub Pages serves the directory verbatim
    # (otherwise underscore-prefixed files are sometimes hidden).
    (DOCS_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print("\nDone. To preview locally:")
    print("  npx serve docs")
    print("  -> open http://localhost:3000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
