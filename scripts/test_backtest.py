"""
test_backtest.py
----------------
Regression smoke test. Runs the production v3.2 backtest end-to-end on the
current parquet and asserts that the headline numbers fall inside a sane
band. This is the backstop against silent regressions from:

  - a pandas / numpy / pyarrow version bump that changes behaviour
  - an accidental edit to backtest.py that quietly changes signal semantics
  - a corrupted or partially-fetched prices.parquet

The bands are intentionally WIDE. A tight band would tag every routine data
update as a "regression". The point is to catch order-of-magnitude breaks
(Sharpe collapsing to zero, CAGR going negative across the whole OOS window,
the equity series getting truncated by a date-parse bug) — not to police
small drift.

The script exits non-zero on any failed assertion. Wired into
.github/workflows/daily-check.yml ahead of pipeline.py so a regression
blocks dashboard publication rather than silently shipping bad numbers.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest import (
    Params, PRICES_PATH, OUT_OF_SAMPLE_START,
    load_prices, investability_mask_liquidity,
    breadth_pct_above_ma, breadth_to_tier,
    momentum_score, rank_top_n,
    per_coin_trend_entry_mask, per_coin_trend_exit_mask,
    build_target_weights, run_backtest,
    summary_stats,
)


# Sane bands for the headline OOS metrics. If the strategy genuinely
# changes (a new version, a different param) these need to be re-anchored
# deliberately. Drift inside these bands is normal and not a regression.
OOS_SHARPE_BAND = (0.70, 2.20)     # current ~1.45
OOS_CAGR_MIN = 0.0                  # OOS must not go negative across the
                                    # whole 5+ year window — that would be
                                    # a real strategy failure, not noise
OOS_CAGR_MAX = 3.0                  # 300 % CAGR ceiling — a sanity guard
                                    # against equity-blow-up bugs
MIN_OOS_DAYS = 1500                 # ~6 years of OOS daily bars (we have
                                    # 2021-01-01 onwards). Catches a date
                                    # parse that silently truncates data.

FAILURES: list[str] = []


def assert_band(name: str, value: float, lo: float, hi: float) -> None:
    if not (lo <= value <= hi):
        FAILURES.append(
            f"{name}={value:.4f} outside band [{lo:.4f}, {hi:.4f}]"
        )
    else:
        print(f"  OK   {name}={value:.4f} in band [{lo:.4f}, {hi:.4f}]")


def assert_min(name: str, value: float, lo: float) -> None:
    if value < lo:
        FAILURES.append(f"{name}={value} below minimum {lo}")
    else:
        print(f"  OK   {name}={value} >= {lo}")


def main() -> int:
    print(f"Loading {PRICES_PATH} ...")
    close, volume = load_prices(PRICES_PATH)
    print(f"  shape {close.shape}, dates {close.index.min().date()} -> "
          f"{close.index.max().date()}")

    # Frozen-ticker guard: these series must never grow again. LUNA is the
    # sharp one — Binance serves Terra 2.0 (a different asset) under the
    # same pair, so any new row means the fetch-script freeze failed and a
    # foreign asset is being spliced onto a dead coin's history.
    FROZEN_LAST = {"LUNA": "2022-05-13", "EOS": "2026-07-04", "MATIC": "2026-07-04"}
    print("Frozen-ticker guard ...")
    for sym, want in FROZEN_LAST.items():
        if sym not in close.columns:
            FAILURES.append(f"frozen ticker {sym} missing from parquet")
            continue
        got = str(close[sym].dropna().index.max().date())
        if got != want:
            FAILURES.append(
                f"frozen ticker {sym} last date {got} != {want} — "
                f"the freeze has been breached (foreign-asset splice risk)"
            )
        else:
            print(f"  OK   {sym} frozen at {want}")

    p = Params()
    print("Running v3.2 backtest (single_name_cap=%s) ..." % p.single_name_cap)
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
    target_w = build_target_weights(weights_rank, target_exposure, p.rebalance_weekday,
                                    single_name_cap=p.single_name_cap)
    exit_mask = per_coin_trend_exit_mask(close, p.per_coin_trend_window)
    res = run_backtest(
        close, target_w, p.fee_bps_per_side, lag_days=1, daily_exit_mask=exit_mask,
    )

    eq = res["equity"].dropna()
    if eq.empty:
        FAILURES.append("equity series is empty")
        return _finish()

    eq_oos = eq.loc[OUT_OF_SAMPLE_START:]
    if eq_oos.empty:
        FAILURES.append(f"OOS slice empty from {OUT_OF_SAMPLE_START}")
        return _finish()

    # summary_stats takes the EQUITY series (not returns). It computes the
    # wealth ratio from eq.iloc[0] to eq.iloc[-1] internally and derives
    # CAGR / Sharpe / MaxDD from there.
    stats = summary_stats(eq_oos)
    print(f"OOS window: {eq_oos.index[0].date()} -> {eq_oos.index[-1].date()} "
          f"({len(eq_oos)} bars)")
    print(f"OOS Sharpe={stats['sharpe']:.3f}  CAGR={stats['cagr']*100:.1f}%  "
          f"MaxDD={stats['max_dd']*100:.1f}%")

    assert_band("oos_sharpe", float(stats["sharpe"]),
                OOS_SHARPE_BAND[0], OOS_SHARPE_BAND[1])
    assert_band("oos_cagr", float(stats["cagr"]), OOS_CAGR_MIN, OOS_CAGR_MAX)
    assert_min("oos_bar_count", int(len(eq_oos)), MIN_OOS_DAYS)

    # Equity must be finite throughout.
    if not eq.notna().all():
        FAILURES.append(f"equity has {int(eq.isna().sum())} NaN values")
    else:
        print(f"  OK   equity finite over all {len(eq)} bars")

    return _finish()


def _finish() -> int:
    if FAILURES:
        print("\nREGRESSION DETECTED:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("\nAll regression checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
