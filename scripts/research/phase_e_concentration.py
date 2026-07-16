"""
Phase E — concentration floor (RESEARCH_MEMO PR-5, pre-registered 2026-07-15).

Tests whether a floor / cap on the auto-concentration property of `rank_top_n`
improves the tail without buying it with the edge. The frozen v3.1 engine
(`scripts/backtest.py`) is NOT modified — every arm is built on top of it by
reshaping the rank weights or the gate BEFORE `build_target_weights`, so the
close-T -> trade-T+1 lag and the daily trend-exit are inherited unchanged.

The property under test: `rank_top_n` assigns `1/min(n, len(valid))` and the
tier then scales it, so the FULL tier exposure deploys across however many names
are trend-eligible. When eligibility is thin the book CONCENTRATES rather than
under-deploying.

Arms (all five declared in the pre-registration BEFORE any run):
  E.1  minimum-qualifier floor  — deploy only if n_eligible >= k, else cash.
                                  k in {2, 3}.
  E.2  per-name cap             — cap any single name at c of the book, residual
                                  to cash. c in {0.34, 0.50}.
  E.3  pro-rata scaling         — gross = tier * (n_eligible / top_n); unfilled
                                  slots cash. (Equivalently:每 name gets
                                  tier/top_n regardless of how many qualify.)

Success bar is ASYMMETRIC (this is a risk fix, not a return fix) — see PR-5.
ADOPT iff the tail improves AND full-sample Sharpe loss <= 0.15 vs frozen v3.1
AND MaxDD does not worsen AND the -50% hard-stop still clears.

Reports ALL five arms and the FULL distributions, never just the best or just
the five single-name events (PR-5 guards 1 and 2).

Outputs: results/phase_e_concentration.json + .md, and 5 appended lines in
results/trial_registry.jsonl (v3.1 pool).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import (  # noqa: E402
    Params, PRICES_PATH, load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier, per_coin_trend_entry_mask,
    per_coin_trend_exit_mask, momentum_score, rank_top_n,
    build_target_weights, run_backtest, summary_stats,
)
from phase_b_review import deflated_sharpe  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = PROJECT_ROOT / "results"
REGISTRY = RESULTS / "trial_registry.jsonl"

# Frozen 2026-07-15 in PR-5, BEFORE any run. Do not tune.
E1_FLOORS = [2, 3]
E2_CAPS = [0.34, 0.50]
SHARPE_LOSS_TOLERANCE = 0.15
MAXDD_HARD_STOP = -0.50
CONDITIONAL_ELIGIBLE = 2      # the thin-eligibility event set: n_eligible <= 2
FORWARD_WINDOW_D = 14         # the conditional forward window


def _f(v):
    try:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return None
        return float(v)
    except Exception:
        return None


def config_id(cfg: dict) -> str:
    """10-hex stable hash of the config (house format)."""
    return hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:10]


def build_tw(ranks: pd.DataFrame, gate: pd.Series, weekday: int,
             cap: float | None = None) -> pd.DataFrame:
    """Scale ranks by the gate, optionally cap per-name, keep rebalance rows only.

    Mirrors backtest.build_target_weights exactly when cap is None — asserted in
    main() and in tests/test_phase_e_floor.py, so this re-implementation cannot
    silently drift from the frozen engine. The cap must be applied AFTER the
    gate multiply, which is why the frozen function cannot be reused for E.2.
    """
    scaled = ranks.mul(gate, axis=0)
    if cap is not None:
        scaled = scaled.clip(upper=cap)
    is_rebal_row = scaled.index.weekday == weekday
    out = scaled.copy()
    out.loc[~is_rebal_row, :] = np.nan
    return out


def eligible_count(mom: pd.DataFrame) -> pd.Series:
    """Number of RANKABLE names per date.

    rank_top_n does row.dropna(), so `valid` is exactly the non-NaN momentum
    scores — names that cleared investability AND the trend entry filter AND
    have a defined composite. Uses only close-T information; the T+1 execution
    lag is applied downstream by run_backtest.
    """
    return mom.notna().sum(axis=1)


def arm_weights(arm: str, ranks: pd.DataFrame, gate: pd.Series,
                n_elig: pd.Series, p: Params, *, k=None, c=None) -> pd.DataFrame:
    """Target weights for one arm. Baseline is the frozen path, untouched."""
    if arm == "baseline":
        return build_target_weights(ranks, gate, p.rebalance_weekday)
    if arm == "E1":
        # Gate to cash whenever fewer than k names qualify.
        return build_target_weights(ranks, gate.where(n_elig >= k, 0.0),
                                    p.rebalance_weekday)
    if arm == "E2":
        # Cap each name at c of the book; the residual falls to cash.
        return build_tw(ranks, gate, p.rebalance_weekday, cap=c)
    if arm == "E3":
        # Every selected name gets exactly 1/top_n of the tier, so gross scales
        # pro-rata with how many qualified and unfilled slots sit in cash.
        ranks_pr = (ranks > 0).astype(float) / p.rank_top_n
        return build_target_weights(ranks_pr, gate, p.rebalance_weekday)
    raise ValueError(arm)


def worst_rolling_12m(equity: pd.Series) -> float:
    r = equity / equity.shift(365) - 1.0
    return float(r.min()) if r.notna().any() else float("nan")


def tail_stats(tw: pd.DataFrame, res: dict, event_dates: pd.DatetimeIndex) -> dict:
    """Concentration + conditional-tail statistics for one arm.

    `event_dates` is the BASELINE's thin-eligibility rebalance set. Eligibility is
    arm-independent (the floor changes sizing, never who qualifies), so every arm
    is scored on the SAME dates — a like-for-like read on the same events.
    """
    rebal = tw.dropna(how="all")
    risk_on = rebal[rebal.sum(axis=1) > 0.001]
    n_names = (risk_on > 0).sum(axis=1)
    maxw = risk_on.max(axis=1) if len(risk_on) else pd.Series(dtype=float)

    eq = res["equity"]
    fwd = []
    for d in event_dates:
        # Execution is the bar AFTER the signal (lag_days=1); measure from there.
        loc = eq.index.get_indexer([d], method="bfill")[0]
        if loc < 0 or loc + 1 >= len(eq):
            continue
        start = loc + 1
        end = min(start + FORWARD_WINDOW_D, len(eq) - 1)
        fwd.append(float(eq.iloc[end] / eq.iloc[start] - 1.0))

    return {
        "n_risk_on_rebalances": int(len(risk_on)),
        "n_single_name_100pct": int(((n_names == 1) & (maxw >= 0.999)).sum()) if len(risk_on) else 0,
        "n_fewer_than_4_names": int((n_names < 4).sum()) if len(risk_on) else 0,
        "n_exactly_1_name": int((n_names == 1).sum()) if len(risk_on) else 0,
        "max_single_name_weight": _f(maxw.max()) if len(risk_on) else None,
        "p95_single_name_weight": _f(maxw.quantile(0.95)) if len(risk_on) else None,
        "median_single_name_weight": _f(maxw.median()) if len(risk_on) else None,
        "conditional_fwd14_worst": _f(min(fwd)) if fwd else None,
        "conditional_fwd14_mean": _f(np.mean(fwd)) if fwd else None,
        "conditional_n_events": len(fwd),
    }


def main() -> int:
    p = Params()
    print("Phase E — concentration floor (PR-5). Frozen engine, display-independent.\n")
    close, volume = load_prices(PRICES_PATH)
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd,
        min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    n_elig = eligible_count(mom)

    # Guard: our re-implementation must equal the frozen builder when uncapped.
    base_tw = build_target_weights(ranks, gate, p.rebalance_weekday)
    assert build_tw(ranks, gate, p.rebalance_weekday).equals(base_tw), \
        "build_tw diverges from the frozen build_target_weights"

    # ---- the event set: thin-eligibility risk-on rebalances (arm-independent)
    rebal_rows = base_tw.dropna(how="all")
    risk_on_rows = rebal_rows[rebal_rows.sum(axis=1) > 0.001]
    ev = risk_on_rows.index[n_elig.reindex(risk_on_rows.index) <= CONDITIONAL_ELIGIBLE]
    print(f"Risk-on rebalance Mondays: {len(risk_on_rows)}")
    print(f"  thin-eligibility events (n_eligible <= {CONDITIONAL_ELIGIBLE}): {len(ev)}\n")

    # ---- full distribution of n_eligible over risk-on rebalances (guard 2)
    ne_on = n_elig.reindex(risk_on_rows.index)
    dist = {int(v): int(c) for v, c in ne_on.value_counts().sort_index().items()}
    print("n_eligible distribution across risk-on rebalances:")
    for v, c in sorted(dist.items()):
        print(f"    {v:>2} eligible : {c:>3}  ({c/len(ne_on):5.1%})")
    print()

    arms = [("baseline", {}, "frozen v3.1 (control)")]
    for k in E1_FLOORS:
        arms.append(("E1", {"k": k}, f"minimum-qualifier floor k={k}"))
    for c in E2_CAPS:
        arms.append(("E2", {"c": c}, f"per-name cap c={c}"))
    arms.append(("E3", {}, "pro-rata gross = tier x n_eligible/top_n"))

    rows = []
    for arm, kw, label in arms:
        tw = arm_weights(arm, ranks, gate, n_elig, p, **kw)
        res = run_backtest(close, tw, p.fee_bps_per_side, lag_days=1,
                           daily_exit_mask=exit_mask)
        st = summary_stats(res["equity"])
        ts = tail_stats(tw, res, ev)
        rows.append({
            "arm": arm, "label": label, "params": kw,
            "sharpe": _f(st["sharpe"]), "cagr": _f(st["cagr"]),
            "max_dd": _f(st["max_dd"]), "vol": _f(st["vol"]),
            "worst_12m": _f(worst_rolling_12m(res["equity"])),
            "ret": res["equity"].pct_change().dropna(),
            **ts,
        })
        print(f"  {label:<42} Sharpe {st['sharpe']:.3f}  CAGR {st['cagr']:6.1%}  "
              f"MaxDD {st['max_dd']:6.1%}  1-name100% {ts['n_single_name_100pct']}")

    base = rows[0]
    print(f"\n{'arm':<42} {'dSharpe':>8} {'dMaxDD':>8} {'100%':>5} {'maxW':>6} "
          f"{'cond14 worst':>13} {'ADOPT':>6}")
    for r in rows:
        d_sh = r["sharpe"] - base["sharpe"]
        d_dd = r["max_dd"] - base["max_dd"]
        if r["arm"] == "baseline":
            verdict = "—"
        else:
            tail_ok = (r["n_single_name_100pct"] == 0 if r["arm"] in ("E1", "E3")
                       else (r["max_single_name_weight"] or 1) <= r["params"].get("c", 1) + 1e-9)
            cond_ok = ((r["conditional_fwd14_worst"] or -1) >
                       (base["conditional_fwd14_worst"] or -1) + 1e-9)
            edge_ok = (d_sh >= -SHARPE_LOSS_TOLERANCE and d_dd >= -1e-9
                       and r["max_dd"] >= MAXDD_HARD_STOP)
            verdict = "YES" if (tail_ok and cond_ok and edge_ok) else "no"
            r["tail_ok"], r["cond_ok"], r["edge_ok"] = tail_ok, cond_ok, edge_ok
        r["d_sharpe"], r["d_maxdd"], r["adopt"] = _f(d_sh), _f(d_dd), verdict
        print(f"  {r['label']:<40} {d_sh:>+8.3f} {d_dd:>+8.2%} "
              f"{r['n_single_name_100pct']:>5} {(r['max_single_name_weight'] or 0):>6.1%} "
              f"{(r['conditional_fwd14_worst'] or float('nan')):>12.1%} {verdict:>6}")

    # ---- DSR over the enlarged pool.
    # PR-5 froze "N=104" — that is the count of ALL logged trials (99) + 5. It is
    # WRONG on the memo's own rule: the v3.1 pool is B+C2+C3a+C4 = 79 and C3b's 20
    # are a SEPARATE pool that must not cross-contaminate. Both are reported; the
    # pre-registered figure is not quietly replaced with the one we now prefer.
    pool = [json.loads(l) for l in REGISTRY.read_text(encoding="utf-8").splitlines() if l.strip()]
    v31 = [t for t in pool if t["arm"] in ("B", "C2", "C3a", "C4")]
    tsh = [t["metrics"]["sharpe"] for t in v31 if t["metrics"].get("sharpe") is not None]
    tsh += [r["sharpe"] for r in rows if r["arm"] != "baseline"]
    n_v31 = len(v31) + 5
    n_all = len(pool) + 5
    dsr = deflated_sharpe(base["ret"], tsh, n_v31)
    print(f"\nDSR (frozen v3.1 baseline) — v3.1 pool N={n_v31}: {dsr['dsr_base']:.4f}")
    print(f"  pre-registered N={n_all} (cross-pool, see note): "
          f"{deflated_sharpe(base['ret'], tsh, n_all)['dsr_base']:.4f}")
    print(f"  ladder: " + "  ".join(f"N={n}:{v['dsr']:.4f}" for n, v in dsr["by_n"].items()))

    out = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pre_registration": "RESEARCH_MEMO PR-5 (frozen 2026-07-15)",
        "sharpe_loss_tolerance": SHARPE_LOSS_TOLERANCE,
        "maxdd_hard_stop": MAXDD_HARD_STOP,
        "n_risk_on_rebalances": int(len(risk_on_rows)),
        "n_eligible_distribution": dist,
        "n_thin_events": int(len(ev)),
        "thin_event_dates": [str(d.date()) for d in ev],
        "dsr_v31_pool": {"n": n_v31, "dsr": dsr["dsr_base"], "ladder": dsr["by_n"]},
        "dsr_prereg_cross_pool": {"n": n_all,
                                  "dsr": deflated_sharpe(base["ret"], tsh, n_all)["dsr_base"]},
        "arms": [{k: v for k, v in r.items() if k != "ret"} for r in rows],
    }
    (RESULTS / "phase_e_concentration.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {RESULTS / 'phase_e_concentration.json'}")

    # ---- append the 5 trials to the v3.1 pool (append-only, house format)
    run_utc = datetime.now(timezone.utc).isoformat()
    with REGISTRY.open("a", encoding="utf-8") as fh:
        for r in rows:
            if r["arm"] == "baseline":
                continue
            cfg = {"floor_arm": r["arm"], **r["params"]}
            fh.write(json.dumps({
                "run_utc": run_utc, "arm": "E", "config_id": config_id(cfg),
                "config": cfg,
                "metrics": {"sharpe": r["sharpe"], "cagr": r["cagr"],
                            "max_dd": r["max_dd"], "worst_12m": r["worst_12m"],
                            "dsr": None},
                "split": "full",
                "notes": f"PR-5 phase-E concentration floor — {r['label']}",
            }) + "\n")
    print(f"Appended 5 trials to {REGISTRY.name} (v3.1 pool {len(v31)} -> {n_v31})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
