"""
survivorship_audit.py  (Phase 2 — data & survivorship audit)
------------------------------------------------------------
Interrogates data/prices.parquet to answer the single question every historical
number in this project rests on: is the universe genuinely point-in-time and
survivorship-free, and is the review substrate a clean, single-source series?

It does NOT modify data or the engine. It writes results/survivorship_audit.json
and prints a summary. Findings and the verdict live in results/survivorship_audit.md
and RESEARCH_MEMO.md.

Run:  PYTHONIOENCODING=utf-8 python scripts/research/survivorship_audit.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
PRICES = ROOT / "data" / "prices.parquet"
OUT = ROOT / "results" / "survivorship_audit.json"

# Well-known major Binance USDT-quoted coins that carried meaningful liquidity at
# some point in 2019-2021. INDICATIVE, not exhaustive, and not a reconstruction of
# the true point-in-time top-N (that needs the full Binance listing history, which
# the no-new-vendor rule puts out of scope). Used only to demonstrate that the
# 25-name set omits a large tail of era-liquid names — i.e. selection bias.
KNOWN_ERA_MAJORS = [
    "VET", "THETA", "XTZ", "XMR", "DASH", "ZEC", "NEO", "IOTA", "WAVES", "ONT",
    "ZIL", "QTUM", "OMG", "ICX", "BAT", "ENJ", "CHZ", "SAND", "MANA", "AXS",
    "GALA", "SUSHI", "YFI", "CRV", "COMP", "SNX", "1INCH", "GRT", "HBAR", "EGLD",
    "KSM", "RUNE", "CAKE", "LUNC", "UST", "CEL", "SRM", "DENT", "ANKR", "IOST",
]

# Market-wide crash dates that legitimately move most of the universe at once, so a
# clustered multi-coin jump on these dates is NOT a data-splice artefact.
KNOWN_CRASH_DATES = {
    "2020-03-12": "COVID crash",
    "2021-05-19": "May-2021 crypto crash (China mining ban)",
    "2021-05-24": "May-2021 aftershock",
    "2022-05-12": "Terra/LUNA collapse",
    "2022-11-08": "FTX/FTT collapse",
    "2025-10-10": "Oct-2025 sell-off",
}


def main() -> int:
    df = pd.read_parquet(PRICES)
    df["date"] = pd.to_datetime(df["date"])
    syms = sorted(df["symbol"].unique())
    close = df.pivot(index="date", columns="symbol", values="close").sort_index()
    volume = df.pivot(index="date", columns="symbol", values="volume").sort_index()
    panel_end = close.index.max()

    # --- 1. universe composition & true delistings -------------------------
    g = df.groupby("symbol")["date"].agg(["min", "max", "count"])
    ends_early = sorted(g.index[g["max"] < panel_end - pd.Timedelta(days=7)].tolist())

    # --- 2. selection gap: era-major names absent from the set -------------
    absent = [c for c in KNOWN_ERA_MAJORS if c not in syms]

    # --- 3. death capture: are LUNA/FTT honest declines, not fabricated? ---
    ret = close.pct_change()
    death = {}
    for sym, crash in [("LUNA", "2022-05-12"), ("FTT", "2022-11-08")]:
        if sym in close.columns:
            worst = float(ret[sym].min())
            pre = float(close[sym].loc[:crash].iloc[-30:].max())
            resid = float(close[sym].dropna().iloc[-1])
            death[sym] = {
                "worst_1d_return": round(worst, 4),
                "pre_crash_peak_30d": round(pre, 6),
                "residual_close_latest": round(resid, 8),
                "collapse_captured": worst < -0.5 and resid < 0.05 * pre,
            }

    # --- 4. splice / glitch scan: multi-coin same-day jumps ---------------
    big = (ret.abs() > 0.25).sum(axis=1)
    clusters = big[big >= 6].sort_values(ascending=False)
    cluster_rows = []
    for d, n in clusters.items():
        key = d.strftime("%Y-%m-%d")
        cluster_rows.append(
            {"date": key, "n_coins": int(n), "known_event": KNOWN_CRASH_DATES.get(key, "UNEXPLAINED")}
        )
    unexplained = [r for r in cluster_rows if r["known_event"] == "UNEXPLAINED"]

    # --- 5. interior NaN gaps (post-listing holes) ------------------------
    interior_na = {}
    for c in close.columns:
        s = close[c]
        first = s.first_valid_index()
        if first is not None:
            n = int(s.loc[first:].isna().sum())
            if n > 0:
                interior_na[c] = n

    # --- 6. effective universe size over time (liquidity gate can't see 25) -
    #        median count of names with a non-NaN close per day.
    eff = close.notna().sum(axis=1)
    eff_by_year = {int(y): int(eff[eff.index.year == y].median()) for y in range(2018, 2027)}

    result = {
        "panel": {
            "date_start": str(close.index.min().date()),
            "date_end": str(panel_end.date()),
            "n_symbols": len(syms),
            "symbols": syms,
        },
        "true_delistings_in_set": ends_early,          # expected: [] -> not point-in-time
        "selection_gap": {
            "known_era_majors_checked": len(KNOWN_ERA_MAJORS),
            "absent_from_set": absent,
            "n_absent": len(absent),
        },
        "death_capture": death,
        "splice_scan": {
            "multi_coin_jump_days": cluster_rows,
            "unexplained_clusters": unexplained,       # expected: [] -> clean splice
        },
        "interior_nan_gaps": dict(sorted(interior_na.items(), key=lambda kv: -kv[1])),
        "effective_universe_median_by_year": eff_by_year,
        "nonpositive_close_count": int((close <= 0).sum().sum()),
    }

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))

    # --- console summary ---------------------------------------------------
    print(f"panel: {result['panel']['date_start']} -> {result['panel']['date_end']}, "
          f"{len(syms)} symbols")
    print(f"true delistings in the set (coins that stop early): {ends_early or 'NONE'}")
    print(f"  -> a point-in-time universe would contain delistings; NONE here = "
          f"fixed hindsight-selected set")
    print(f"era-major names ABSENT from the set: {len(absent)} of "
          f"{len(KNOWN_ERA_MAJORS)} checked -> {absent}")
    print(f"death capture: " + "; ".join(
        f"{k} worst1d={v['worst_1d_return']:.1%} captured={v['collapse_captured']}"
        for k, v in death.items()))
    print(f"splice scan: {len(cluster_rows)} multi-coin jump days, "
          f"unexplained={[r['date'] for r in unexplained] or 'NONE (clean)'}")
    print(f"interior NaN gaps: {result['interior_nan_gaps']}")
    print(f"effective universe median/yr: {eff_by_year}")
    print(f"non-positive closes: {result['nonpositive_close_count']}")
    print(f"\nwrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
