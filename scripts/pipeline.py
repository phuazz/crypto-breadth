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
# Per-coin signals are lazy-loaded by the dashboard (too big to inline).
# We write to both data/ (for dev when template.html is opened directly)
# and docs/data/ (for production when GitHub Pages serves docs/).
COIN_SIGNALS_DATA_JSON = PROJECT_ROOT / "data" / "coin_signals.json"
COIN_SIGNALS_DOCS_JSON = PROJECT_ROOT / "docs" / "data" / "coin_signals.json"


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


def extract_trade_history(
    close: pd.DataFrame, result: dict,
    breadth: pd.Series, target_exposure: pd.Series,
    p: Params = None,
) -> list[dict]:
    """Decompose every trade day into per-coin events.

    A trade day is any day where the backtest's `turnover` series is > 0 —
    that captures both weekly rebalances and daily trend-exit overrides.

    For each trade day, the previous day's weights are drifted forward by
    today's returns (then re-normalised, including idle cash). The
    difference between the actual end-of-day weight and the drifted weight
    is the trade — anything else is just price drift and we ignore it.

    Each event also gets the breadth % and target exposure at the trade
    date attached, so the dashboard can show "signal at trade."

    Threshold: 0.5% absolute weight change per coin to count as a trade.
    """
    weights = result["weights"]
    turnover = result["turnover"]
    daily_exits = result["daily_exit_count"]
    # `rebal_executed` flags days when the weekly rebal block actually ran.
    # Older code used turnover > 0 as a proxy, but turnover is also bumped
    # by daily-exit forced sells, so that proxy mis-labelled pure daily
    # exits as "rebal + exit". This is the accurate signal.
    rebal_executed = result.get("rebal_executed")
    daily_ret = close.pct_change().fillna(0.0)

    # 50d MA per coin — used to attach the close-vs-MA snapshot at the
    # signal day (the day BEFORE the trade, due to the 1-bar lag) to each
    # event. This is the numerical evidence behind the "Why" text on the
    # dashboard: e.g. "on May 28 close was $634.54 vs MA $637.56".
    ma_window = (p.per_coin_trend_window if p is not None else 50)
    ma_for_trades = close.rolling(ma_window, min_periods=ma_window).mean()

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
        if rebal_executed is not None:
            is_rebal_day = bool(rebal_executed.loc[dt])
        else:
            # Fallback for old run_backtest result dicts (not flagged)
            is_rebal_day = bool(turnover.loc[dt] > 0) and not is_exit_day
        if is_rebal_day and is_exit_day:
            trigger = "rebal + exit"
        elif is_exit_day:
            trigger = "daily exit"
        else:
            trigger = "rebal"

        # Signal context at this trade date
        try:
            sig_breadth = float(breadth.loc[dt])
        except KeyError:
            sig_breadth = None
        try:
            sig_exposure = float(target_exposure.loc[dt])
        except KeyError:
            sig_exposure = None

        # The signal day is the bar BEFORE the trade (the 1-bar lag) — that
        # is when "close < MA" was evaluated for the daily exit, and when
        # the Monday-close signal was generated for weekly rebals. Grab the
        # per-coin close and MA at that bar so the dashboard can show the
        # numerical evidence behind the trade.
        sig_date = weights.index[idx - 1]

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
            sig_close = _f(close.loc[sig_date, coin]) if coin in close.columns else None
            sig_ma = _f(ma_for_trades.loc[sig_date, coin]) if coin in ma_for_trades.columns else None
            events.append({
                "date": str(dt.date()),
                "trigger": trigger,
                "coin": coin,
                "action": action,
                "old_w": drifted,
                "new_w": actual,
                "delta": delta,
                "sig_breadth": _f(sig_breadth) if sig_breadth is not None else None,
                "sig_exposure": _f(sig_exposure) if sig_exposure is not None else None,
                "sig_date": str(sig_date.date()),
                "sig_close": sig_close,
                "sig_ma": sig_ma,
            })

    events.sort(key=lambda e: e["date"], reverse=True)
    return events


def coin_signal_history(
    close: pd.DataFrame, mask: pd.DataFrame,
    result: dict, trades: list, p: Params,
) -> dict:
    """Per-coin time-series payload for the dashboard's Signal Explorer.

    For every coin in the universe, returns weekly snapshots of:
      - close
      - 50d MA + boolean ma_rising
      - composite momentum score
      - held weight (was this coin in the book?)
      - investability (passed the rolling liquidity gate?)
    Plus a `latest` block describing the current state precisely:
      "how far above/below MA", "is trend rising", "momentum score", etc.
    Plus a `events` array of trade events for this coin only (for chart markers).

    Weekly resample keeps the payload tractable while still showing every
    rebalance decision clearly. Output is fetched lazily by the dashboard.
    """
    weights = result["weights"]
    window = p.per_coin_trend_window
    ma_full = close.rolling(window, min_periods=window).mean()
    ma_diff_full = ma_full.diff()
    daily_ret = close.pct_change()

    score_parts = []
    for L in p.momentum_lookbacks_d:
        ret_L = close.pct_change(L)
        vol_L = daily_ret.rolling(L, min_periods=max(10, L // 2)).std() * np.sqrt(L)
        score_parts.append((ret_L / vol_L).where(vol_L > 0))
    momentum_full = sum(score_parts) / len(score_parts)

    last_date = close.index[-1]

    # Group trades by coin for fast attachment
    trades_by_coin: dict[str, list] = {}
    for t in trades:
        trades_by_coin.setdefault(t["coin"], []).append({
            "date": t["date"], "action": t["action"],
            "old_w": t["old_w"], "new_w": t["new_w"],
        })

    coins_out: dict[str, dict] = {}
    for coin in close.columns:
        if not mask[coin].any():
            continue  # never investable, skip

        df = pd.DataFrame({
            "close": close[coin],
            "ma": ma_full[coin],
            "ma_diff": ma_diff_full[coin],
            "momentum": momentum_full[coin],
            "weight": weights[coin] if coin in weights.columns else 0.0,
            "investable": mask[coin],
        })
        # Trim to first investable date so chart starts when relevant
        first_inv_idx = mask[coin].idxmax() if mask[coin].any() else None
        if first_inv_idx is None:
            continue
        df = df.loc[first_inv_idx:]
        # Hybrid resolution: daily for the last 365 days (so intra-week
        # exit triggers are visible — e.g. a Thursday dip below the 50d
        # MA that triggered Friday's sell), weekly for older history
        # (keeps payload bounded). Concatenate the two pieces and drop
        # the boundary duplicate.
        last_date = df.index[-1]
        cutoff = last_date - pd.Timedelta(days=365)
        df_recent = df.loc[df.index >= cutoff]
        df_older = df.loc[df.index < cutoff]
        if len(df_older) > 0:
            df_older_w = df_older.resample("W-MON").last().dropna(how="all")
        else:
            df_older_w = df_older
        weekly = pd.concat([df_older_w, df_recent]).sort_index()
        weekly = weekly[~weekly.index.duplicated(keep="last")]

        # Current-state snapshot
        last_close = _f(close.loc[last_date, coin])
        last_ma = _f(ma_full.loc[last_date, coin])
        last_diff = _f(ma_diff_full.loc[last_date, coin])
        last_mom = _f(momentum_full.loc[last_date, coin])
        last_inv = bool(mask.loc[last_date, coin])
        last_w = (
            _f(weights.loc[last_date, coin])
            if coin in weights.columns and not pd.isna(weights.loc[last_date, coin])
            else 0.0
        ) or 0.0
        ma_dist = ((last_close / last_ma) - 1.0) if (last_close and last_ma) else None
        ma_rising = (last_diff > 0) if last_diff is not None else False
        trend_eligible = (
            last_inv
            and last_close is not None and last_ma is not None
            and last_close > last_ma
            and ma_rising
        )

        coins_out[coin] = {
            "dates": weekly.index.strftime("%Y-%m-%d").tolist(),
            "close":     [_f(v) for v in weekly["close"].values],
            "ma":        [_f(v) for v in weekly["ma"].values],
            "ma_rising": [bool((v or 0) > 0) for v in weekly["ma_diff"].values],
            "momentum":  [_f(v) for v in weekly["momentum"].values],
            "weight":    [_f(v) for v in weekly["weight"].values],
            "investable":[bool(v) for v in weekly["investable"].values],
            "first_date": str(weekly.index.min().date()),
            "last_date":  str(weekly.index.max().date()),
            "latest": {
                "close": last_close,
                "ma": last_ma,
                "ma_dist_pct": _f(ma_dist),
                "ma_rising": ma_rising,
                "momentum": last_mom,
                "investable": last_inv,
                "weight": last_w,
                "trend_eligible": trend_eligible,
            },
            "events": trades_by_coin.get(coin, []),
        }

    return coins_out


def signal_walkthrough(
    close: pd.DataFrame, volume: pd.DataFrame,
    breadth: pd.Series, target_exposure: pd.Series,
    mask: pd.DataFrame, p: Params,
) -> dict:
    """Reconstruct the full signal chain for the most recent close.

    Five filters, in order:
      1. Liquidity gate         (25 candidates -> investable subset)
      2. Trend entry filter     (close > MA AND MA rising)
      3. Composite momentum     (rank surviving names)
      4. Top-N selection        (pick the N best)
      5. Breadth gate -> tier   (overall gross exposure)

    Returns enough information for the dashboard to show each step with
    pass/fail chips and the actual signal values that drove the call.
    """
    last_date = close.index[-1]

    # ---- Step 1: liquidity / investability ----
    has_data = close.notna()
    cum_age = has_data.astype(int).cumsum()
    last_cum_age = cum_age.loc[last_date]
    dollar_volume = close * volume
    adv = dollar_volume.rolling(
        p.liquidity_lookback_d, min_periods=max(5, p.liquidity_lookback_d // 2)
    ).mean()
    last_adv = adv.loc[last_date]
    last_mask = mask.loc[last_date]

    step1_rows = []
    for coin in sorted(close.columns):
        age = int(last_cum_age.get(coin, 0))
        coin_adv = _f(last_adv.get(coin, np.nan)) or 0.0
        investable = bool(last_mask.get(coin, False))
        if investable:
            reason = f"ADV ${coin_adv/1e6:.0f}M, {age}d history"
        elif not bool(has_data.loc[last_date].get(coin, False)):
            reason = "not trading"
        elif age < p.liquidity_min_history_days:
            reason = f"only {age}d history (need {p.liquidity_min_history_days})"
        elif coin_adv < p.liquidity_min_adv_usd:
            reason = f"ADV ${coin_adv/1e6:.0f}M (below ${p.liquidity_min_adv_usd/1e6:.0f}M)"
        else:
            reason = "—"
        step1_rows.append({
            "coin": coin, "pass": investable,
            "adv_usd": coin_adv, "age_days": age,
            "reason": reason,
        })
    investable_coins = [r["coin"] for r in step1_rows if r["pass"]]

    # ---- Step 2: trend entry filter ----
    window = p.per_coin_trend_window
    ma = close.rolling(window, min_periods=window).mean()
    above_ma = close > ma
    ma_diff = ma.diff()
    ma_rising = ma_diff > 0
    last_above = above_ma.loc[last_date]
    last_rising = ma_rising.loc[last_date]
    last_close = close.loc[last_date]
    last_ma = ma.loc[last_date]
    last_ma_diff = ma_diff.loc[last_date]

    step2_rows = []
    for coin in investable_coins:
        c = _f(last_close.get(coin, np.nan))
        m = _f(last_ma.get(coin, np.nan))
        above = bool(last_above.get(coin, False))
        rising = bool(last_rising.get(coin, False))
        ma_d = _f(last_ma_diff.get(coin, np.nan))
        passes = above and rising
        if passes:
            reason = "above MA, MA rising"
        elif not above and not rising:
            reason = "below MA, MA falling"
        elif not above:
            reason = "below MA"
        else:
            reason = "MA not rising"
        step2_rows.append({
            "coin": coin, "pass": passes,
            "close": c, "ma": m,
            "above_ma": above, "ma_rising": rising,
            "ma_slope_d": ma_d,
            "reason": reason,
        })
    passed_step2 = [r["coin"] for r in step2_rows if r["pass"]]

    # ---- Step 3: composite momentum ranking ----
    lookbacks = list(p.momentum_lookbacks_d)
    daily_ret = close.pct_change()
    score_parts = []
    component_returns = {}
    component_vols = {}
    for L in lookbacks:
        ret_L = close.pct_change(L)
        vol_L = daily_ret.rolling(L, min_periods=max(10, L // 2)).std() * np.sqrt(L)
        score_L = (ret_L / vol_L).where(vol_L > 0)
        score_parts.append(score_L)
        component_returns[L] = ret_L.loc[last_date]
        component_vols[L] = vol_L.loc[last_date]
    composite = sum(score_parts) / len(score_parts)
    last_composite = composite.loc[last_date]

    step3_rows = []
    for coin in passed_step2:
        score = _f(last_composite.get(coin, np.nan))
        comps = []
        for L in lookbacks:
            comps.append({
                "lookback_d": L,
                "return": _f(component_returns[L].get(coin, np.nan)),
                "ann_vol": _f(component_vols[L].get(coin, np.nan)),
            })
        step3_rows.append({"coin": coin, "score": score, "components": comps})
    # Sort by score descending (NaN to bottom)
    step3_rows.sort(key=lambda r: -(r["score"] if r["score"] is not None else -1e9))
    for i, r in enumerate(step3_rows):
        r["rank"] = i + 1

    # ---- Step 4: top-N pick ----
    top_n = p.rank_top_n
    selected = [r["coin"] for r in step3_rows[:top_n]]
    cut_coins = [r["coin"] for r in step3_rows[top_n:]]

    # ---- Step 5: breadth gate ----
    cur_breadth = _f(breadth.loc[last_date])
    cur_exposure = _f(target_exposure.loc[last_date])
    # Build tier ladder
    tiers = []
    thr = list(p.tier_thresholds)
    exp = list(p.tier_exposures)
    # Ranges: [0, thr[0]) -> exp[0]; [thr[0], thr[1]) -> exp[1]; etc.
    ranges = []
    lows = [0.0] + thr
    highs = thr + [1.0]
    for i in range(len(exp)):
        ranges.append({
            "low": lows[i], "high": highs[i],
            "exposure": exp[i],
            "active": (cur_breadth is not None and lows[i] <= cur_breadth < highs[i]),
        })
    if cur_breadth is not None and cur_breadth >= 1.0:
        ranges[-1]["active"] = True

    # ---- Final intended allocation ----
    final_weight_per_coin = (cur_exposure or 0.0) / max(top_n, 1) if selected else 0.0
    final_holdings = [{"coin": c, "weight": final_weight_per_coin} for c in selected]
    final_cash = max(0.0, 1.0 - final_weight_per_coin * len(selected))

    return {
        "as_of": str(last_date.date()),
        "step1": {
            "title": "1. Liquidity gate",
            "rule": (f"Trailing {p.liquidity_lookback_d}d ADV ≥ "
                     f"${p.liquidity_min_adv_usd/1e6:.0f}M AND ≥ "
                     f"{p.liquidity_min_history_days}d history"),
            "input_count": len(close.columns),
            "output_count": len(investable_coins),
            "candidates": step1_rows,
        },
        "step2": {
            "title": "2. Trend entry filter",
            "rule": f"close > {window}d MA AND {window}d MA rising",
            "input_count": len(investable_coins),
            "output_count": len(passed_step2),
            "candidates": step2_rows,
        },
        "step3": {
            "title": "3. Composite momentum ranking",
            "rule": f"average of risk-adjusted returns over " +
                    ", ".join(f"{L}d" for L in lookbacks),
            "input_count": len(passed_step2),
            "output_count": len(step3_rows),
            "rows": step3_rows,
        },
        "step4": {
            "title": f"4. Top-{top_n} selection",
            "rule": f"highest {top_n} composite scores",
            "input_count": len(step3_rows),
            "output_count": len(selected),
            "selected": selected,
            "cut": cut_coins,
        },
        "step5": {
            "title": "5. Breadth gate → exposure tier",
            "rule": "tier from % of investable universe above own 50d MA",
            "current_breadth": cur_breadth,
            "current_exposure": cur_exposure,
            "ladder": ranges,
        },
        "final": {
            "title": "Target allocation if rebalanced now",
            "holdings": final_holdings,
            "cash": final_cash,
            "per_coin_weight": final_weight_per_coin,
        },
    }


def weights_history_for_chart(result: dict, freq: str = "W-MON") -> dict:
    """Resample the daily weights to a tractable frequency for the stacked
    area chart. Only includes coins that have been held at least once.

    Returns dict with: dates, coins (list of names actually used), and
    a 2D array `weights[i][j]` = weight of coin j on date i (and a cash
    track appended as the final column).
    """
    weights = result["weights"]
    # Weekly snapshots
    w_weekly = weights.resample(freq).last().dropna(how="all")
    # Add cash as 1 - sum(weights)
    cash = (1.0 - w_weekly.sum(axis=1)).clip(lower=0.0)
    # Filter to coins ever held
    ever_held = (weights.max(axis=0) > 0.01)
    coins_used = sorted(weights.columns[ever_held].tolist())
    out = w_weekly[coins_used].copy()
    out["__cash__"] = cash
    return {
        "dates": out.index.strftime("%Y-%m-%d").tolist(),
        "coins": coins_used + ["Cash"],
        "weights": [[_f(v) for v in row] for row in out.values],
    }


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
    trades = extract_trade_history(close, res, breadth, target_exposure, p)
    print(f"  {len(trades)} trade events")

    # "This week's changes" — trades within the last 14 days of the sample.
    if trades:
        last_date_str = monitor["as_of"]
        last_date = pd.Timestamp(last_date_str)
        cutoff = last_date - pd.Timedelta(days=14)
        recent_trades = [t for t in trades if pd.Timestamp(t["date"]) >= cutoff]
    else:
        recent_trades = []
    print(f"  this week's changes: {len(recent_trades)} trade events in last 14 days")

    print("Weights-history for stacked area chart ...")
    exposure_history = weights_history_for_chart(res)

    print("Signal walkthrough (latest bar) ...")
    walkthrough = signal_walkthrough(close, volume, breadth, target_exposure, mask, p)

    print("Per-coin signal history (lazy-loaded) ...")
    coin_signals = coin_signal_history(close, mask, res, trades, p)
    print(f"  {len(coin_signals)} coin time-series prepared")
    print(f"  funnel: {walkthrough['step1']['input_count']} -> "
          f"{walkthrough['step2']['input_count']} -> "
          f"{walkthrough['step3']['input_count']} -> "
          f"{walkthrough['step4']['output_count']} picked, "
          f"target gross {walkthrough['step5']['current_exposure']:.0%}")

    print("Parameter sensitivity sweep ...")
    sensitivity = sensitivity_sweep(close, volume, p)

    # ----- assemble payload -----
    print("Building payload ...")
    # Equity series stays DAILY in the payload so the dashboard can:
    #   1. Rebase to 0% at any user-selected window start (the "linear
    #      cumulative-return" view replaces the wealth-multiple-log view)
    #   2. Compute week-to-date, period return, annualised, in-window
    #      Sharpe, and in-window MaxDD live on the client.
    # 4 curves × 3070 days × ~12 bytes JSON ≈ 150 KB additional payload.
    # Cheap relative to the visualisation gain.
    eq_daily = eq.dropna()
    btc_daily = bm_btc.dropna()
    ew_daily = bm_ew.dropna()
    bm6040_daily = bm_6040.dropna()

    # Drawdown also daily.
    dd_daily = (eq / eq.cummax() - 1.0).dropna()

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
            "dates": _dates(eq_daily.index),
            "strategy": _series(eq_daily),
            "btc": _series(btc_daily),
            "ew": _series(ew_daily),
            "6040": _series(bm6040_daily),
        },
        "drawdown": {
            "dates": _dates(dd_daily.index),
            "strategy": _series(dd_daily),
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
        "recent_trades": recent_trades,
        "exposure_history": exposure_history,
        "walkthrough": walkthrough,
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

    # ----- write per-coin signals (lazy-loaded, NOT inlined) -----
    coin_signals_json = json.dumps(
        {"coins": coin_signals, "generated_at": payload["meta"]["generated_at"]},
        separators=(",", ":"),
    )
    COIN_SIGNALS_DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    COIN_SIGNALS_DATA_JSON.write_text(coin_signals_json, encoding="utf-8")
    COIN_SIGNALS_DOCS_JSON.parent.mkdir(parents=True, exist_ok=True)
    COIN_SIGNALS_DOCS_JSON.write_text(coin_signals_json, encoding="utf-8")
    print(f"  wrote {COIN_SIGNALS_DATA_JSON} and {COIN_SIGNALS_DOCS_JSON} "
          f"({COIN_SIGNALS_DATA_JSON.stat().st_size / 1024:.1f} KB each)")

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
