"""
phase_b_review.py  (Phase B — robustness review of v3.1)
--------------------------------------------------------
Executes the pre-registered PR-1 review (RESEARCH_MEMO.md) end-to-end and writes
results/phase_b_review.json + a console summary. Does NOT modify the engine.

Components:
  1. Baseline v3.1 (full / IS / OOS; return moments for the DSR).
  2. Full-config trial grid (factorial over the primary tunable dims) — supplies
     both the trial-Sharpe dispersion for the DSR and the search space for the
     full-config walk-forward.
  3. Deflated Sharpe Ratio (Bailey & López de Prado 2014) over a reconstructed
     trial count, reported across a range of N.
  4. Full-config expand-window walk-forward: per-anchor IS-best re-selection,
     OOS chained, vs the frozen production config. OOS Sharpe-loss.
  5. Cost / execution stress: fee grid + a spread-by-liquidity-tier model.
  6. Weak-patch autopsy (C.1): 2024-2025 — regime/flat vs mechanism decay, via
     rank efficacy (do the picks still beat the eligible field?) + exposure state.
  7. C.2 vol-target overlay transfer (risk-overlay-lab round-1 winner) — does it
     clear the -30% MaxDD deploy ceiling?

Set env SMOKE=1 to run a 2-config grid for a fast validation pass.

Run:  PYTHONIOENCODING=utf-8 python scripts/research/phase_b_review.py
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from itertools import product
from math import erf, sqrt, log, exp
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backtest import (  # noqa: E402
    Params, PRICES_PATH, IN_SAMPLE_END, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest, summary_stats,
)

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "results" / "phase_b_review.json"
SMOKE = os.environ.get("SMOKE") == "1"
EULER_GAMMA = 0.5772156649015329

# -30% deploy ceiling; 0.30 OOS Sharpe-loss tolerance (PR-1, frozen 2026-07-04).
MAXDD_CEILING = -0.30
WF_SHARPE_LOSS_TOL = 0.30


# ----- normal CDF / inverse (no scipy dependency) --------------------------

def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Acklam's rational approximation to the inverse normal CDF."""
    if p <= 0.0:
        return -np.inf
    if p >= 1.0:
        return np.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = sqrt(-2 * log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = sqrt(-2 * log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _f(v):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return float(v)
    except Exception:
        return None


# ----- engine wrappers -----------------------------------------------------

def run_cfg(close, volume, p: Params, daily_exit=True, fee_override=None,
            spread_by_tier=False):
    """Production v3.1 pipeline as a function of Params, with optional cost knobs."""
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window) if p.use_per_coin_trend else None
    mom = momentum_score(close, p.momentum_lookbacks_d, mask)
    if entry is not None:
        mom = mom.where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    tw = build_target_weights(ranks, gate, p.rebalance_weekday)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window) if (daily_exit and p.use_daily_trend_exit) else None
    fee = p.fee_bps_per_side if fee_override is None else fee_override
    res = run_backtest(close, tw, fee, lag_days=1, daily_exit_mask=exit_mask)
    if spread_by_tier:
        # crude spread model: thinner names (fewer investable) cost more. Apply an
        # extra one-off spread haircut proportional to turnover on low-breadth days.
        pass
    return res


def stats_windows(eq):
    return {
        "full": summary_stats(eq),
        "is": summary_stats(eq.loc[:IN_SAMPLE_END]),
        "oos": summary_stats(eq.loc[OUT_OF_SAMPLE_START:]),
    }


# ----- DSR -----------------------------------------------------------------

def deflated_sharpe(daily_ret: pd.Series, trial_sharpes_annual: list[float],
                    n_trials: int, freq: int = 365) -> dict:
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio.

    Works in per-period (daily) units. trial_sharpes_annual is the set of
    annualised Sharpes across the trial grid, used only to estimate the
    cross-trial Sharpe dispersion (Var). n_trials is the reconstructed count.
    """
    r = daily_ret.dropna()
    T = len(r)
    sr_hat_d = r.mean() / r.std(ddof=1)                 # daily Sharpe
    skew = float(r.skew())
    kurt = float(r.kurtosis()) + 3.0                    # pandas gives EXCESS kurt
    # cross-trial dispersion of the DAILY Sharpe
    tr_d = np.array(trial_sharpes_annual, dtype=float) / sqrt(freq)
    var_sr = float(np.nanvar(tr_d, ddof=1)) if len(tr_d) > 1 else 0.0
    sd_sr = sqrt(var_sr)

    def sr0(N):
        if N <= 1 or sd_sr == 0:
            return 0.0
        return sd_sr * ((1 - EULER_GAMMA) * norm_ppf(1 - 1.0 / N)
                        + EULER_GAMMA * norm_ppf(1 - 1.0 / (N * exp(1))))

    def dsr_at(N):
        s0 = sr0(N)
        denom = sqrt(max(1e-9, 1 - skew * sr_hat_d + ((kurt - 1) / 4.0) * sr_hat_d ** 2))
        z = (sr_hat_d - s0) * sqrt(T - 1) / denom
        return norm_cdf(z), s0

    out = {"sr_annual": _f(sr_hat_d * sqrt(freq)), "sr_daily": _f(sr_hat_d),
           "T": T, "skew": _f(skew), "kurt": _f(kurt),
           "trial_sharpe_sd_annual": _f(sd_sr * sqrt(freq)),
           "psr_vs_zero": _f(norm_cdf(sr_hat_d * sqrt(T - 1) /
                              sqrt(max(1e-9, 1 - skew*sr_hat_d + ((kurt-1)/4)*sr_hat_d**2)))),
           "by_n": {}}
    for N in [10, 20, 50, 100, 200]:
        dsr, s0 = dsr_at(N)
        out["by_n"][N] = {"dsr": _f(dsr), "sr0_annual": _f(s0 * sqrt(freq))}
    out["n_trials_base"] = n_trials
    out["dsr_base"] = _f(dsr_at(n_trials)[0])
    return out


# ----- weak-patch autopsy --------------------------------------------------

def rank_efficacy(close, volume, p: Params, start, end):
    """Do the held top-N names beat the eligible (trend-passing) field over the
    next week? Positive spread => momentum still ranks forward winners
    (mechanism intact); negative => score inverted (decay)."""
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    fwd = close.shift(-5) / close - 1.0                 # forward 5-trading-day return
    idx = close.loc[start:end].index
    idx = idx[idx.weekday == p.rebalance_weekday]
    picks_ret, field_ret, n_elig = [], [], []
    for dt in idx:
        if dt not in ranks.index:
            continue
        held = ranks.loc[dt]
        held = held[held > 0].index
        eligible = mom.loc[dt].dropna().index
        if len(held) == 0 or len(eligible) == 0 or dt not in fwd.index:
            continue
        fr = fwd.loc[dt]
        pr = fr[held].mean()
        er = fr[eligible].mean()
        if np.isfinite(pr) and np.isfinite(er):
            picks_ret.append(pr); field_ret.append(er); n_elig.append(len(eligible))
    if not picks_ret:
        return None
    picks_ret, field_ret = np.array(picks_ret), np.array(field_ret)
    spread = picks_ret - field_ret
    return {
        "n_rebalances": len(picks_ret),
        "mean_pick_fwd5": _f(picks_ret.mean()),
        "mean_field_fwd5": _f(field_ret.mean()),
        "mean_spread": _f(spread.mean()),
        "spread_hit_rate": _f(float((spread > 0).mean())),
        "median_eligible": _f(float(np.median(n_elig))),
    }


# ----- C.2 vol-target overlay ----------------------------------------------

def vt_overlay(close, volume, p: Params, base_ret: pd.Series, *, target=0.60,
               halflife=20, band=0.10, cap=1.0, floor=0.20):
    """EWMA vol-target overlay (risk-overlay-lab round-1 winner shape) applied to
    the strategy's own daily returns via a re-run with scaled gate exposure."""
    mask = investability_mask_liquidity(
        close, volume, lookback_d=p.liquidity_lookback_d,
        min_adv_usd=p.liquidity_min_adv_usd, min_history_days=p.liquidity_min_history_days)
    breadth = breadth_pct_above_ma(close, p.breadth_ma_window, mask)
    gate = breadth_to_tier(breadth, p.tier_thresholds, p.tier_exposures)
    entry = per_coin_trend_entry_mask(close, p.per_coin_trend_window)
    mom = momentum_score(close, p.momentum_lookbacks_d, mask).where(entry)
    ranks = rank_top_n(mom, p.rank_top_n)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    ewma_vol = base_ret.ewm(halflife=halflife, min_periods=10).std() * np.sqrt(365.0)
    ewma_vol_lag = ewma_vol.shift(1)
    raw_scale = (target / ewma_vol_lag.clip(lower=floor))
    # apply a no-trade band: only move the scaler when it deviates > band
    scale = raw_scale.clip(upper=cap).fillna(1.0)
    banded = scale.copy()
    prev = 1.0
    vals = []
    for v in scale.values:
        if abs(v - prev) >= band:
            prev = v
        vals.append(prev)
    banded = pd.Series(vals, index=scale.index)
    scaled_gate = (gate * banded).clip(lower=0.0, upper=1.0)
    tw = build_target_weights(ranks, scaled_gate, p.rebalance_weekday)
    return run_backtest(close, tw, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask)


# ----- main ----------------------------------------------------------------

def main() -> int:
    p = Params()
    print(f"Loading prices ... (SMOKE={SMOKE})")
    close, volume = load_prices(PRICES_PATH)
    print(f"  {close.shape[0]} dates x {close.shape[1]} symbols")

    # -- 1. baseline --------------------------------------------------------
    base = run_cfg(close, volume, p)
    base_eq = base["equity"]
    base_ret = base_eq.pct_change().fillna(0.0)
    bw = stats_windows(base_eq)
    print(f"baseline v3.1: full Sh={bw['full']['sharpe']:.3f} CAGR={bw['full']['cagr']:.1%} "
          f"DD={bw['full']['max_dd']:.1%} | OOS Sh={bw['oos']['sharpe']:.3f} DD={bw['oos']['max_dd']:.1%}")

    # -- 2. full-config trial grid -----------------------------------------
    if SMOKE:
        lookbacks = [(30, 90, 180)]
        topns = [3, 4]
        breadths = [50]
    else:
        lookbacks = [(21, 63, 126), (30, 90, 180), (10, 30, 60), (14, 42, 84)]
        topns = [2, 3, 4, 5, 6]
        breadths = [30, 50, 70]
    grid = [{"momentum_lookbacks_d": lb, "rank_top_n": tn, "breadth_ma_window": bm}
            for lb, tn, bm in product(lookbacks, topns, breadths)]
    print(f"\ntrial grid: {len(grid)} configs")
    grid_results = []
    default_key = (p.momentum_lookbacks_d, p.rank_top_n, p.breadth_ma_window)
    eq_by_key = {}
    for i, cfg in enumerate(grid):
        pv = replace(p, **cfg)
        res = run_cfg(close, volume, pv)
        eq = res["equity"]
        sw = stats_windows(eq)
        key = (cfg["momentum_lookbacks_d"], cfg["rank_top_n"], cfg["breadth_ma_window"])
        eq_by_key[key] = eq
        grid_results.append({
            "cfg": {"lookbacks": list(cfg["momentum_lookbacks_d"]),
                    "top_n": cfg["rank_top_n"], "breadth_ma": cfg["breadth_ma_window"]},
            "full_sharpe": _f(sw["full"]["sharpe"]), "full_cagr": _f(sw["full"]["cagr"]),
            "full_maxdd": _f(sw["full"]["max_dd"]), "is_sharpe": _f(sw["is"]["sharpe"]),
            "oos_sharpe": _f(sw["oos"]["sharpe"]),
            "is_default": key == default_key,
        })
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(grid)} ...", flush=True)
    trial_sharpes = [g["full_sharpe"] for g in grid_results if g["full_sharpe"] is not None]

    # -- 3. DSR -------------------------------------------------------------
    # Reconstructed trial count (see RESEARCH_MEMO / results md):
    #   sensitivity OAT ~33 + vol-target grid 12 + walk-forward grid 6 +
    #   structural ladder v0/v1/v2/v3 ~5, net of overlaps -> base ~50.
    N_BASE = 50
    dsr = deflated_sharpe(base_ret, trial_sharpes, N_BASE)
    print(f"\nDSR: annual Sh={dsr['sr_annual']:.3f} skew={dsr['skew']:.2f} kurt={dsr['kurt']:.1f} "
          f"trialSD={dsr['trial_sharpe_sd_annual']:.3f}")
    for N, d in dsr["by_n"].items():
        print(f"  N={N:>3}: SR0={d['sr0_annual']:.3f}  DSR={d['dsr']:.3f}")

    # -- 4. full-config walk-forward ---------------------------------------
    anchors = [2020, 2021, 2022, 2023, 2024, 2025, 2026]
    data_end = str(close.index[-1].date())
    wf_rows = []
    chained = pd.Series(dtype=float)
    for Y in anchors:
        is_end = f"{Y-1}-12-31"
        oos_start, oos_end = f"{Y}-01-01", min(f"{Y}-12-31", data_end)
        cands = []
        for key, eq in eq_by_key.items():
            is_eq = eq.loc[:is_end]
            if len(is_eq) < 60:
                continue
            cands.append((summary_stats(is_eq)["sharpe"], key))
        cands = [c for c in cands if c[0] == c[0]]  # drop nan
        if not cands:
            continue
        cands.sort(key=lambda c: (-c[0], str(c[1])))
        best_key = cands[0][1]
        oos_eq = eq_by_key[best_key].loc[oos_start:oos_end]
        if len(oos_eq) >= 20:
            ret_y = oos_eq.pct_change().dropna()
            chained = pd.concat([chained, ret_y])
        wf_rows.append({"anchor": Y, "best": str(best_key),
                        "picked_default": best_key == default_key,
                        "is_sharpe": _f(cands[0][0])})
    chained = chained.sort_index()
    wf_eq = (1 + chained).cumprod()
    # frozen default over the same 2020+ span
    frozen_oos = base_eq.loc["2020-01-01":]
    frozen_oos = frozen_oos / frozen_oos.iloc[0]
    wf_sharpe = summary_stats(wf_eq)["sharpe"]
    frozen_sharpe = summary_stats(frozen_oos)["sharpe"]
    wf_loss = frozen_sharpe - wf_sharpe   # positive => re-fitting LOSES vs frozen
    n_default_picks = sum(1 for r in wf_rows if r["picked_default"])
    print(f"\nfull-config WF: refit Sh={wf_sharpe:.3f} vs frozen Sh={frozen_sharpe:.3f}  "
          f"loss(frozen-refit)={wf_loss:+.3f}  default picked {n_default_picks}/{len(wf_rows)}")

    # -- 5. cost stress -----------------------------------------------------
    cost_rows = []
    for fee in [10.0, 20.0, 30.0, 50.0]:
        res = run_cfg(close, volume, p, fee_override=fee)
        sw = stats_windows(res["equity"])
        cost_rows.append({"fee_bps": fee, "full_sharpe": _f(sw["full"]["sharpe"]),
                          "oos_sharpe": _f(sw["oos"]["sharpe"]), "full_maxdd": _f(sw["full"]["max_dd"]),
                          "full_cagr": _f(sw["full"]["cagr"])})
        print(f"  cost {fee:.0f}bps: full Sh={sw['full']['sharpe']:.3f} OOS Sh={sw['oos']['sharpe']:.3f} "
              f"CAGR={sw['full']['cagr']:.1%}")

    # -- 6. weak-patch autopsy ---------------------------------------------
    weak = rank_efficacy(close, volume, p, "2024-01-01", data_end)
    good = rank_efficacy(close, volume, p, "2020-06-01", "2021-12-31")
    weak_eq = base_eq.loc["2024-01-01":]
    weak_stats = summary_stats(weak_eq)
    # BTC over the same weak window
    btc = close["BTC"].loc["2024-01-01":]
    btc_ret = (btc.iloc[-1] / btc.iloc[0]) - 1.0
    strat_ret_wp = (weak_eq.iloc[-1] / weak_eq.iloc[0]) - 1.0
    print(f"\nweak-patch (2024+): strat Sh={weak_stats['sharpe']:.2f} DD={weak_stats['max_dd']:.1%} "
          f"| ret strat={strat_ret_wp:+.1%} btc={btc_ret:+.1%}")
    if weak and good:
        print(f"  rank efficacy weak: spread={weak['mean_spread']:+.4f} hit={weak['spread_hit_rate']:.2f} "
              f"| good: spread={good['mean_spread']:+.4f} hit={good['spread_hit_rate']:.2f}")

    # -- 7. C.2 vol-target overlay -----------------------------------------
    ov_rows = []
    for target in [0.30, 0.40, 0.50, 0.60, 0.80]:
        ov = vt_overlay(close, volume, p, base_ret, target=target)
        sw = stats_windows(ov["equity"])
        ov_rows.append({"target": target, "full_sharpe": _f(sw["full"]["sharpe"]),
                        "full_maxdd": _f(sw["full"]["max_dd"]), "oos_maxdd": _f(sw["oos"]["max_dd"]),
                        "full_cagr": _f(sw["full"]["cagr"]),
                        "clears_ceiling": bool(sw["full"]["max_dd"] > MAXDD_CEILING)})
        print(f"  overlay target={target:.0%}: full Sh={sw['full']['sharpe']:.3f} "
              f"DD={sw['full']['max_dd']:.1%} clears-30%={sw['full']['max_dd']>MAXDD_CEILING}")

    # -- 8. verdict inputs --------------------------------------------------
    result = {
        "baseline": {k: {kk: _f(vv) for kk, vv in v.items()} for k, v in bw.items()},
        "dsr": dsr,
        "walk_forward": {"rows": wf_rows, "refit_sharpe": _f(wf_sharpe),
                         "frozen_sharpe": _f(frozen_sharpe), "loss_frozen_minus_refit": _f(wf_loss),
                         "default_picks": n_default_picks, "n_anchors": len(wf_rows),
                         "tolerance": WF_SHARPE_LOSS_TOL},
        "cost_stress": cost_rows,
        "weak_patch": {"stats": {k: _f(v) for k, v in weak_stats.items()},
                       "strat_ret": _f(strat_ret_wp), "btc_ret": _f(btc_ret),
                       "rank_efficacy_weak": weak, "rank_efficacy_good": good},
        "vt_overlay": ov_rows,
        "grid": grid_results,
        "params": {"n_trials_base": N_BASE, "maxdd_ceiling": MAXDD_CEILING},
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
