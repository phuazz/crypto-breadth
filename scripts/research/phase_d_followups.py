"""
phase_d_followups.py  — review follow-ups (2026-07-04)
------------------------------------------------------
Two checks the Phase-B review skipped, raised in the four-lens critique:

  (a) YEAR-BLOCK bootstrap of the Sharpe. The dashboard's [0.82, 1.92] CI uses
      short blocks and overstates confidence when one year (2021) drives 62% of
      the growth. Resampling whole calendar years respects that dominance and the
      within-year serial structure, giving an honest CI and P(Sharpe > BTC 0.61).

  (b) DIVERSIFICATION test. A "diversifier" must be uncorrelated to the existing
      book when it matters. Correlate crypto-breadth monthly returns to SPY and to
      the deployed breadth-thrust blend, full-sample and in risk-off windows.

Reads SPY + the blend from the sibling breadth-thrust-etf repo (read-only).
Writes results/phase_d_followups.json.

Run:  PYTHONIOENCODING=utf-8 python scripts/research/phase_d_followups.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (  # noqa: E402
    Params, PRICES_PATH, load_prices, investability_mask_liquidity,
    benchmark_hodl, benchmark_60_40_btc_eth,
)
from phase_c3_concentration import run_v3_masked  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
BTE = ROOT.parent / "breadth-thrust-etf"
OUT = ROOT / "results" / "phase_d_followups.json"
BTC_SHARPE = 0.61


def _f(v):
    try:
        return None if (v is None or not np.isfinite(v)) else float(v)
    except Exception:
        return None


def main() -> int:
    p = Params()
    close, volume = load_prices(PRICES_PATH)
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    eq = run_v3_masked(close, volume, p, mask)["equity"]
    r = eq.pct_change().dropna()

    # --- (a) year-block bootstrap ----------------------------------------
    years = sorted(set(r.index.year))
    by_year = {y: r[r.index.year == y].values for y in years}
    rng = np.random.default_rng(42)
    n_iter = 5000
    boot = np.empty(n_iter)
    for i in range(n_iter):
        pick = rng.choice(years, size=len(years), replace=True)
        cat = np.concatenate([by_year[y] for y in pick])
        boot[i] = cat.mean() / cat.std() * np.sqrt(365.0)
    p05, p50, p95 = np.percentile(boot, [5, 50, 95])
    boot_res = {
        "method": "year-block bootstrap (resample whole calendar years, n=5000)",
        "n_year_blocks": len(years),
        "sharpe_p05": _f(p05), "sharpe_p50": _f(p50), "sharpe_p95": _f(p95),
        "p_sharpe_gt_0": _f((boot > 0).mean()),
        "p_sharpe_gt_btc_0p61": _f((boot > BTC_SHARPE).mean()),
        "p_sharpe_gt_1": _f((boot > 1.0).mean()),
    }

    # --- (b) diversification vs SPY and the deployed blend ---------------
    spy = pd.read_parquet(BTE / "data" / "asset_class_prices_cache.parquet")["SPY"]
    spy.index = pd.to_datetime(spy.index)

    # deployed blend from multi_strategy.json (auto-detect the equity/dates keys)
    blend_ret = None
    try:
        ms = json.load(open(BTE / "data" / "multi_strategy.json"))
        b = ms["strategies"]["blend_35_35_10_20"]
        dk = next((k for k in b if "date" in k.lower()), None)
        ek = next((k for k in b if any(t in k.lower() for t in ("equity", "nav", "curve", "cum"))), None)
        if dk and ek:
            be = pd.Series(b[ek], index=pd.to_datetime(b[dk])).astype(float)
            blend_ret = be.resample("ME").last().pct_change().dropna()
    except Exception as e:
        blend_ret = None
        blend_err = repr(e)

    cb_m = eq.resample("ME").last().pct_change().dropna()
    spy_m = spy.resample("ME").last().pct_change().dropna()

    def corr_block(other_m, label):
        common = cb_m.index.intersection(other_m.index)
        a, b_ = cb_m.loc[common], other_m.loc[common]
        full = a.corr(b_)
        # stress windows
        y2022 = a.index.year == 2022
        recent = a.index >= "2025-01-01"
        beta = np.polyfit(b_.values, a.values, 1)[0] if len(a) > 2 else np.nan
        # crypto-breadth mean return in the other's worst-quartile months
        thr = b_.quantile(0.25)
        worst = b_ <= thr
        return {
            "label": label, "n_months": int(len(common)),
            "corr_full": _f(full),
            "corr_2022": _f(a[y2022].corr(b_[y2022])) if y2022.sum() > 2 else None,
            "corr_2025on": _f(a[recent].corr(b_[recent])) if recent.sum() > 2 else None,
            "beta_to_other": _f(beta),
            "cb_mean_in_others_worst_quartile": _f(a[worst].mean()),
            "cb_mean_overall": _f(a.mean()),
        }

    div = [corr_block(spy_m, "vs SPY (equities)")]
    if blend_ret is not None:
        div.append(corr_block(blend_ret, "vs deployed breadth-thrust blend"))

    # --- (c) like-for-like: strip the SAME years from strategy AND benchmarks --
    # The "net of 2020-21 ~= BTC" framing was unfair — it handicapped the strategy
    # (removed its best years) but compared to full-sample BTC. Stripping the same
    # years from each is the honest test.
    btc = benchmark_hodl(close, "BTC").reindex(eq.index).ffill()
    s6040 = benchmark_60_40_btc_eth(close).reindex(eq.index).ffill()

    def _sh(e, drop):
        rr = e.pct_change().dropna()
        rr = rr[~rr.index.year.isin(drop)]
        return _f(rr.mean() / rr.std() * np.sqrt(365.0))

    lfl = []
    for lab, drop in [("full", ()), ("ex-2021", (2021,)),
                      ("ex-2020-21", (2020, 2021)), ("ex-2018/20/21", (2018, 2020, 2021))]:
        row = {"window": lab, "strategy": _sh(eq, drop), "btc": _sh(btc, drop),
               "blend_6040": _sh(s6040, drop)}
        row["strategy_beats_btc"] = bool(row["strategy"] > row["btc"])
        lfl.append(row)
    print("=== (c) like-for-like Sharpe (same years stripped from each) ===")
    for x in lfl:
        print(f"  {x['window']:<14} strat={x['strategy']:.2f}  btc={x['btc']:.2f}  "
              f"60/40={x['blend_6040']:.2f}  strat_beats_btc={x['strategy_beats_btc']}")

    result = {"year_block_bootstrap": boot_res, "diversification": div,
              "like_for_like_sharpe": lfl,
              "note": "like-for-like added 2026-07-05 after the '~= BTC' framing was "
                      "found unfair; dashboard hero switched to year-block bootstrap."}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))

    print("=== (a) year-block bootstrap Sharpe ===")
    print(f"  p05/p50/p95 = {p05:.2f} / {p50:.2f} / {p95:.2f}")
    print(f"  P(Sharpe>0)={boot_res['p_sharpe_gt_0']:.0%}  "
          f"P(Sharpe>BTC 0.61)={boot_res['p_sharpe_gt_btc_0p61']:.0%}  "
          f"P(Sharpe>1)={boot_res['p_sharpe_gt_1']:.0%}")
    print("=== (b) diversification (monthly) ===")
    for d in div:
        print(f"  {d['label']:<38} corr_full={d['corr_full']:+.2f}  "
              f"corr_2022={d['corr_2022']}  corr_2025+={d['corr_2025on']}  beta={d['beta_to_other']}")
        print(f"      cb mean in {d['label'].split()[1]}'s worst-quartile months = "
              f"{d['cb_mean_in_others_worst_quartile']:+.1%} (vs overall {d['cb_mean_overall']:+.1%})")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
