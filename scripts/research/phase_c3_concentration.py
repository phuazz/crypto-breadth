"""
phase_c3_concentration.py  (Phase 4 — C.3, the MODIFY path)
-----------------------------------------------------------
Two arms, both pre-registered (RESEARCH_MEMO PR-3), evaluated against the
Phase-B verdict's deployment gate (-30% MaxDD ceiling) and the benchmarks.

  C.3a — universe-shrink on the FROZEN cross-sectional engine: restrict the
         eligible pool to the top-K names by trailing ADV (point-in-time), hold
         top-4 as usual. Tests whether concentrating the candidate pool helps.

  C.3b — a NEW majors time-series-momentum engine (own trial ledger, PR-3): each
         coin independent in/out via its own trend (no cross-sectional ranking),
         so it does NOT depend on alt-dispersion and does NOT cede BTC-led
         markets. Universe {BTC+ETH, top-5-by-ADV}; signal {MA, MA-rising,
         12-1 TSMOM}; sizing {equal-weight, vol-target}; net of costs.

Success bar (PR-3): beat v3.1 AND BTC-HODL / 60-40 risk-adjusted net of costs,
clear the -30% MaxDD ceiling. Writes results/phase_c3_concentration.json.

Run:  PYTHONIOENCODING=utf-8 python scripts/research/phase_c3_concentration.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (  # noqa: E402
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier, momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest, summary_stats,
    benchmark_hodl, benchmark_60_40_btc_eth,
)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "results" / "phase_c3_concentration.json"
MAXDD_CEILING = -0.30


def _f(v):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return float(v)
    except Exception:
        return None


def adv_frame(close, volume):
    return (close * volume).rolling(30, min_periods=15).mean()


def topk_adv_mask(base_mask, adv, K):
    """Restrict an investability mask to the top-K names by trailing ADV each day."""
    adv_m = adv.where(base_mask)
    rank = adv_m.rank(axis=1, ascending=False, method="first")
    return base_mask & (rank <= K)


def run_v3_masked(close, volume, p, mask):
    """v3.1 engine with a caller-supplied investability mask (for C.3a)."""
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    tw = build_target_weights(ranks, gate, p.rebalance_weekday)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    return run_backtest(close, tw, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask)


def majors_engine(close, universe_mask, *, lookback, signal, sizing,
                  fee=10.0, target_vol=0.50, halflife=20, floor=0.20):
    """C.3b: independent per-coin time-series momentum on a majors universe."""
    if signal in ("ma", "ma_rising"):
        ma = close.rolling(lookback, min_periods=lookback).mean()
        on = close > ma
        if signal == "ma_rising":
            on = on & (ma.diff() > 0)
    elif signal == "tsmom":                       # classic 12-minus-1-month sign
        r = close.shift(21) / close.shift(252) - 1.0
        on = r > 0
    else:
        raise ValueError(signal)
    on = (on & universe_mask).fillna(False)
    n_on = on.sum(axis=1).replace(0, np.nan)
    w = on.div(n_on, axis=0).fillna(0.0)          # equal-weight the "on" coins
    exposure = pd.Series(1.0, index=close.index)
    if sizing == "vt":
        tw1 = build_target_weights(w, exposure, 0)
        r1 = run_backtest(close, tw1, fee, lag_days=1)["equity"].pct_change().fillna(0.0)
        rv = r1.ewm(halflife=halflife, min_periods=10).std() * np.sqrt(365.0)
        exposure = (target_vol / rv.shift(1).clip(lower=floor)).clip(upper=1.0).fillna(1.0)
    tw = build_target_weights(w, exposure, 0)     # weekly Monday rebalance
    return run_backtest(close, tw, fee, lag_days=1)


def windows(eq):
    return {"full": summary_stats(eq), "oos": summary_stats(eq.loc[OUT_OF_SAMPLE_START:])}


def row(name, eq, extra=None):
    w = windows(eq)
    d = {"name": name,
         "full_sharpe": _f(w["full"]["sharpe"]), "full_cagr": _f(w["full"]["cagr"]),
         "full_maxdd": _f(w["full"]["max_dd"]), "oos_sharpe": _f(w["oos"]["sharpe"]),
         "oos_maxdd": _f(w["oos"]["max_dd"]),
         "clears_ceiling": bool(w["full"]["max_dd"] > MAXDD_CEILING)}
    if extra:
        d.update(extra)
    return d


def main() -> int:
    p = Params()
    print("Loading prices ...")
    close, volume = load_prices(PRICES_PATH)
    adv = adv_frame(close, volume)
    base_mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)

    # -- references --------------------------------------------------------
    v31 = run_v3_masked(close, volume, p, base_mask)["equity"]
    btc = benchmark_hodl(close, "BTC").reindex(close.index).ffill()
    b6040 = benchmark_60_40_btc_eth(close).reindex(close.index).ffill()
    refs = [row("v3.1 incumbent", v31),
            row("BTC HODL", btc), row("60/40 BTC-ETH", b6040)]
    v31_full_sh = refs[0]["full_sharpe"]
    print(f"v3.1: Sh={refs[0]['full_sharpe']:.3f} DD={refs[0]['full_maxdd']:.1%} | "
          f"BTC Sh={refs[1]['full_sharpe']:.3f} DD={refs[1]['full_maxdd']:.1%}")

    # -- C.3a universe-shrink ---------------------------------------------
    print("\nC.3a universe-shrink (top-K by ADV, v3.1 engine):")
    c3a = []
    for K in [25, 15, 10, 6, 4]:
        m = topk_adv_mask(base_mask, adv, K) if K < 25 else base_mask
        eq = run_v3_masked(close, volume, p, m)["equity"]
        r = row(f"top{K}-by-ADV", eq, {"K": K})
        c3a.append(r)
        print(f"  K={K:>2}: Sh={r['full_sharpe']:.3f} OOS={r['oos_sharpe']:.3f} "
              f"DD={r['full_maxdd']:.1%} clears-30%={r['clears_ceiling']}")

    # -- C.3b majors TS-momentum ------------------------------------------
    print("\nC.3b majors TS-momentum (own engine):")
    btceth_mask = pd.DataFrame(False, index=close.index, columns=close.columns)
    for c in ["BTC", "ETH"]:
        btceth_mask[c] = close[c].notna()
    top5_mask = topk_adv_mask(base_mask, adv, 5)
    universes = {"BTC+ETH": btceth_mask, "top5-ADV": top5_mask}
    c3b = []
    for uname, umask in universes.items():
        for signal in ["ma", "ma_rising", "tsmom"]:
            lookbacks = [100, 200] if signal in ("ma", "ma_rising") else [0]
            for lb in lookbacks:
                for sizing in ["ew", "vt"]:
                    eq = majors_engine(close, umask, lookback=lb, signal=signal,
                                       sizing=sizing, fee=p.fee_bps_per_side)["equity"]
                    tag = f"{uname}/{signal}{('-'+str(lb)) if lb else ''}/{sizing}"
                    r = row(tag, eq, {"universe": uname, "signal": signal,
                                      "lookback": lb, "sizing": sizing})
                    r["beats_v31_sharpe"] = bool(r["full_sharpe"] is not None
                                                 and r["full_sharpe"] > v31_full_sh)
                    c3b.append(r)
                    print(f"  {tag:<26} Sh={r['full_sharpe']:.3f} OOS={r['oos_sharpe']:.3f} "
                          f"DD={r['full_maxdd']:.1%} clears-30%={r['clears_ceiling']} "
                          f"beats_v31={r['beats_v31_sharpe']}")

    # -- deployable candidates (clear ceiling AND beat BTC risk-adjusted) --
    btc_sh = refs[1]["full_sharpe"]
    deployable = [r for r in (c3a + c3b)
                  if r["clears_ceiling"] and (r["full_sharpe"] or 0) > btc_sh]
    deployable.sort(key=lambda r: -(r["full_sharpe"] or 0))
    print(f"\nDEPLOYABLE (clears -30% AND Sharpe > BTC {btc_sh:.2f}): "
          f"{[(r['name'], round(r['full_sharpe'],2), f'{r['full_maxdd']:.0%}') for r in deployable[:6]]}")

    result = {"references": refs, "c3a_universe_shrink": c3a,
              "c3b_majors_tsmom": c3b, "deployable": deployable,
              "ceiling": MAXDD_CEILING}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
