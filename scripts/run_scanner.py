"""Crypto cross-sectional scanner — daily build.

Reads the project substrate (``data/prices.parquet``, Binance USDT spot),
computes every column and alert for the live coin universe, and writes
``data/scanner_latest.json`` for the page builder plus
``docs/scanner_history.json`` for the expandable row charts.

A crypto port of the breadth-thrust-etf ETF scanner (``run_scanner.py``,
spec ``etf_scanner_spec_en.md``). Differences from the ETF build, all owner
decisions of 2026-09-24:

* **Calendar-equivalent look-backs** on the 365-day UTC calendar — see
  ``scanner_indicators``.
* **No ETF layer.** P/D% and 5D flow have no spot-crypto counterpart and are
  dropped; no new data source is introduced.
* **Benchmark = BTC** for the RS column; BTC's own row shows "—".
* **Overlays read this project's state, never recompute it**: the breadth
  gate and the book come from ``data/dashboard_data.json`` (built by
  ``pipeline.py``). The one computed overlay is the ETH/BTC 50D/200D chip,
  the crypto stand-in for the ETF page's EM-tilt chip; it is display-only
  and feeds nothing.

Nothing here touches the engine or its outputs. The scanner is a monitoring
panel: it fetches nothing (the daily workflow has already refreshed the
parquet), and a scanner failure must never be able to disturb the book.

**Guards abort, they do not warn.** A monitoring page has no downstream
consumer to notice a wrong number, so the build failing is the only signal.

**Parameters are frozen and unvalidated.** Every threshold lives in
``scanner_indicators`` or the ALERT block below.

Usage:
    python scripts/run_scanner.py
    python scripts/run_scanner.py --prices path/to/prices.parquet --today 2026-09-24
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import scanner_indicators as si  # noqa: E402
from fetch_daily_update import DELISTED_ON_BINANCE  # noqa: E402

PRICES_PATH = ROOT / "data" / "prices.parquet"
DASHBOARD_PATH = ROOT / "data" / "dashboard_data.json"
OUT_PATH = ROOT / "data" / "scanner_latest.json"
# Chart history goes to docs/ because the browser fetches it beside the
# published page and GitHub Pages serves only docs/.
HISTORY_PATH = ROOT / "docs" / "scanner_history.json"
# Same window the RV and BBW percentiles rank within, so the chart shows
# exactly the history those columns are measured against.
HISTORY_DAYS = si.PCTL_WINDOW

MAX_MISSING_COINS = 5            # >= this many live coins without bars aborts
STALE_DAYS = 3                   # a row this many days behind the panel is greyed
GAP_CHECK_DAYS = si.PCTL_WINDOW + si.RV_WINDOW   # widest positional look-back
BENCHMARK = "BTC"                # RS 1M is measured against BTC for every row
TILT_NUMERATOR, TILT_DENOMINATOR = "ETH", "BTC"

# --- Alert thresholds (frozen, unvalidated — ETF spec §4 and §8) ----------
ALERT_SIGMA_MOVE = 2.0           # |return| > 2 sigma of the prior 20 daily returns
ALERT_VOLUME_MULTIPLE = 3.0      # volume > 3x its 20-day average
ALERT_RSI_HIGH = 75.0
ALERT_RSI_LOW = 25.0
SQUEEZE_RV_PCTL = 25.0           # squeeze needs BOTH low
SQUEEZE_BBW_PCTL = 10.0
SQUEEZE_RELEASE_SIGMA = 1.5
# Rank crossing. The ETF page uses the top 10 of 54 (~19%). Scaled to 22 coins
# that is ~4, which would read as the engine's top-4 book — it is not: the
# engine selects on its own momentum and trend rules, and this composite is a
# different ranking. 5 keeps the proportion approximately and stays visibly
# distinct from the book size. Confirmation: 5 sessions -> 7 days.
RANK_CROSS_CUT = 5
RANK_CROSS_CONFIRM = 7

# Chip priority when the collapsed view is used (ETF spec §4, no ETF layer)
ALERT_PRIORITY = {
    "squeeze": 1, "squeeze_release": 1,
    "ma200_cross": 2, "52w": 2, "rank_cross": 3, "sigma_move": 4,
    "rsi": 5, "volume": 6,
}
MAX_CHIPS = 12

# Display names. Static on purpose: names do not change and the build must
# not make a network call for them.
COIN_NAMES = {
    "AAVE": "Aave", "ADA": "Cardano", "ALGO": "Algorand", "ATOM": "Cosmos",
    "AVAX": "Avalanche", "BCH": "Bitcoin Cash", "BNB": "BNB", "BTC": "Bitcoin",
    "DOGE": "Dogecoin", "DOT": "Polkadot", "EOS": "EOS", "ETC": "Ethereum Classic",
    "ETH": "Ethereum", "FIL": "Filecoin", "FTT": "FTX Token", "LINK": "Chainlink",
    "LTC": "Litecoin", "LUNA": "Terra Classic", "MATIC": "Polygon", "NEAR": "NEAR Protocol",
    "SOL": "Solana", "TRX": "TRON", "UNI": "Uniswap", "XLM": "Stellar", "XRP": "XRP",
}


class ScannerBuildError(RuntimeError):
    """Raised when a guard fails. The build stops; the page is not written."""


@dataclass
class CoinData:
    """One coin's USDT OHLCV on its own (UTC daily) bars."""

    ticker: str
    frame: pd.DataFrame          # date-indexed open/high/low/close/volume

    @property
    def as_of(self) -> pd.Timestamp:
        return self.frame.index[-1]

    @property
    def close(self) -> pd.Series:
        return self.frame["close"]


@dataclass
class Alert:
    ticker: str
    kind: str
    label: str
    value: str = ""

    @property
    def priority(self) -> int:
        return ALERT_PRIORITY.get(self.kind, 9)


@dataclass
class BuildReport:
    failures: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# =========================================================================
# Load
# =========================================================================
def load_panel(
    prices_path: Path, report: BuildReport, gapped: set[str] | None = None
) -> dict[str, CoinData]:
    """Split the long parquet into one frame per LIVE coin.

    Frozen tickers (``DELISTED_ON_BINANCE``) are excluded, not greyed: their
    last bar is months old by design, and a scanner row for them would only
    ever show a stale reading of a pair that no longer trades.
    """
    if not prices_path.exists():
        raise ScannerBuildError(f"missing {prices_path}")
    long = pd.read_parquet(prices_path)
    need = {"date", "open", "high", "low", "close", "volume", "symbol"}
    missing_cols = need - set(long.columns)
    if missing_cols:
        raise ScannerBuildError(f"prices parquet lacks columns {sorted(missing_cols)}")

    long["date"] = pd.to_datetime(long["date"]).dt.tz_localize(None)
    gapped = gapped if gapped is not None else set()
    panel: dict[str, CoinData] = {}
    live = sorted(set(long["symbol"]) - DELISTED_ON_BINANCE)
    for sym in live:
        g = long.loc[long["symbol"] == sym].drop(columns=["symbol"])
        g = g.set_index("date").sort_index()
        if g.index.has_duplicates:
            raise ScannerBuildError(f"{sym}: duplicate dates in the parquet")
        g = g[["open", "high", "low", "close", "volume"]].astype("float64")
        g = g.dropna(subset=["close"])
        if g.empty:
            report.failures.append(f"{sym}: no bars")
            continue
        # Look-backs are positional (N bars = N days only on a gap-free
        # calendar). A hole inside the widest window would silently stretch
        # every horizon, so it is disclosed. Measured on the last N BARS, not
        # the last N calendar days: a hole straddling a date window's start
        # would otherwise pass while the bars reach back further than labelled.
        # FTT's 2022-23 suspension sits outside the window today.
        tail = g.index[-GAP_CHECK_DAYS:]
        span = (tail[-1] - tail[0]).days + 1
        if span > len(tail):
            report.notes.append(
                f"{sym}: {span - len(tail)} missing day(s) inside its last {len(tail)} "
                f"bars — its look-backs span more calendar time than labelled"
            )
            gapped.add(sym)
        panel[sym] = CoinData(sym, g)

    if len(report.failures) >= MAX_MISSING_COINS:
        raise ScannerBuildError(
            f"{len(report.failures)} coins without bars (limit {MAX_MISSING_COINS}); "
            "refusing to publish a partial panel:\n  - " + "\n  - ".join(report.failures)
        )
    if not panel:
        raise ScannerBuildError("no live coins in the parquet")
    return panel


# =========================================================================
# Per-coin columns
# =========================================================================
def horizon_returns(data: CoinData, offset: int = 0) -> dict[int, float]:
    """Total return over each rank horizon on this coin's own bars.

    ``offset`` steps the calculation back N bars, which is how delta-R and
    the rank crossings are derived without persisting ranks.
    """
    close = data.close if offset == 0 else data.close.iloc[:-offset]
    return {h: si.total_return(close, h) for h in si.RANK_HORIZONS}


def build_columns(data: CoinData, benchmark: pd.Series) -> dict:
    """Every non-rank column for one row."""
    frame, close = data.frame, data.close
    high, low, volume = frame["high"], frame["low"], frame["volume"]

    rv = si.percentile_of_latest(si.realised_vol(close))
    bbw = si.percentile_of_latest(si.bollinger_bandwidth(close))

    return {
        "trend": si.trend_state(close),
        "mom_12_1": si.momentum_12_1(close),
        "vs_52w_high": si.vs_52w_high(close),
        "rv_pctl": rv.value,
        "rv_truncated": rv.truncated,
        "bbw_pctl": bbw.value,
        "bbw_truncated": bbw.truncated,
        "atr_pct": si.atr_pct(high, low, close),
        "ret_1d": si.total_return(close, 1),
        "vol_ratio": si.volume_ratio(volume),
        "ret_1m": si.total_return(close, si.MOMENTUM_SKIP),
        "rs_1m": (
            None if data.ticker == BENCHMARK
            else si.relative_strength_1m(close, benchmark)
        ),
        "dev_200d": si.dev_from_ma(close),
        "rsi14": float(si.rsi(close).iloc[-1]),
        "n_bars": int(len(close)),
    }


# =========================================================================
# Alerts
# =========================================================================
def build_alerts(data: CoinData, cols: dict) -> list[Alert]:
    """Event chips for one row, on its latest bar."""
    out: list[Alert] = []
    close = data.close
    t = data.ticker

    window = close.iloc[-si.DAYS_YEAR:]
    if len(window) >= si.DAYS_YEAR:
        if close.iloc[-1] >= window.max():
            out.append(Alert(t, "52w", "52-week high"))
        elif close.iloc[-1] <= window.min():
            out.append(Alert(t, "52w", "52-week low"))

    ma200 = si.sma(close, si.MA_LONG)
    if len(close) > si.MA_LONG and np.isfinite(ma200.iloc[-2]):
        was_below = close.iloc[-2] < ma200.iloc[-2]
        is_below = close.iloc[-1] < ma200.iloc[-1]
        if was_below and not is_below:
            out.append(Alert(t, "ma200_cross", "Crossed above MA200"))
        elif not was_below and is_below:
            out.append(Alert(t, "ma200_cross", "Crossed below MA200"))

    # The yardstick stops at the previous bar, so a large move cannot inflate
    # the standard deviation it is measured against.
    daily = close.pct_change()
    sigma = daily.iloc[-si.RV_WINDOW - 1:-1].std(ddof=1)
    last = daily.iloc[-1]
    if np.isfinite(sigma) and sigma > 0 and abs(last) > ALERT_SIGMA_MOVE * sigma:
        out.append(
            Alert(t, "sigma_move",
                  f"{abs(last) / sigma:.1f}-sigma move", f"{last * 100:+.1f}%")
        )

    vr = cols.get("vol_ratio")
    if vr is not None and np.isfinite(vr) and vr > ALERT_VOLUME_MULTIPLE:
        out.append(Alert(t, "volume", f"Volume {vr:.1f}x 20D"))

    rv_p, bbw_p = cols.get("rv_pctl"), cols.get("bbw_pctl")
    in_squeeze = (
        rv_p is not None and bbw_p is not None
        and np.isfinite(rv_p) and np.isfinite(bbw_p)
        and rv_p < SQUEEZE_RV_PCTL and bbw_p < SQUEEZE_BBW_PCTL
    )
    if in_squeeze:
        if np.isfinite(sigma) and sigma > 0 and abs(last) > SQUEEZE_RELEASE_SIGMA * sigma:
            out.append(Alert(t, "squeeze_release", "Squeeze release"))
        else:
            out.append(
                Alert(t, "squeeze", "Squeeze (RV & BBW low)",
                      f"RV p{rv_p:.0f} / BBW p{bbw_p:.0f}")
            )

    r = cols.get("rsi14")
    if r is not None and np.isfinite(r):
        if r >= ALERT_RSI_HIGH:
            out.append(Alert(t, "rsi", f"RSI {r:.0f} overbought"))
        elif r <= ALERT_RSI_LOW:
            out.append(Alert(t, "rsi", f"RSI {r:.0f} oversold"))
    return out


def rank_crossings(
    panel: dict[str, CoinData],
    rank_now: si.RankResult,
    exclude: set[str] | None = None,
    cut: int = RANK_CROSS_CUT,
    confirm: int = RANK_CROSS_CONFIRM,
) -> list[Alert]:
    """Chips for rows that crossed the top-``cut`` boundary on the latest bar.

    Inside the cut today and outside it on each of the previous ``confirm``
    days, or the mirror. The confirmation stops a row oscillating at the
    boundary from firing every other day. A row unrankable at any point in
    the window is silent: no crossing is asserted on partial evidence.
    """
    excluded = exclude or set()
    history: list[pd.Series] = []
    for offset in range(1, confirm + 1):
        returns = pd.DataFrame({
            t: horizon_returns(d, offset=offset)
            for t, d in panel.items()
            if len(d.close) > offset + min(si.MIN_RANK_HORIZONS)
        }).T
        if returns.empty:
            return []
        history.append(si.rank_from_horizon_returns(returns).ranks)

    out: list[Alert] = []
    for ticker, rank in rank_now.ranks.items():
        if ticker in excluded:
            continue
        priors = [h.get(ticker) for h in history]
        if any(p is None for p in priors):
            continue
        inside_now = int(rank) <= cut
        if all((int(p) <= cut) != inside_now for p in priors):
            out.append(Alert(
                ticker, "rank_cross",
                f"{'Entered' if inside_now else 'Left'} the top {cut}",
                f"rank {int(rank)}",
            ))
    return out


def order_alerts(alerts: list[Alert]) -> list[Alert]:
    return sorted(alerts, key=lambda a: (a.priority, a.ticker))


# =========================================================================
# Overlays — read the project's state; the one computed chip is display-only
# =========================================================================
def load_monitor(dashboard_path: Path) -> dict | None:
    """The ``monitor`` block pipeline.py publishes, or None if unavailable."""
    if not dashboard_path.exists():
        return None
    try:
        d = json.loads(dashboard_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    m = d.get("monitor")
    if not isinstance(m, dict):
        return None
    m = dict(m)
    m["engine_version"] = (d.get("meta") or {}).get("version")
    m["monday_read"] = last_rebalance_read(
        d.get("indicator_history") or {}, m.get("as_of"), m.get("rebalance_weekday", 0)
    )
    return m


def last_rebalance_read(history: dict, as_of: str | None, weekday: int) -> dict | None:
    """The gate reading on the most recent rebalance day on or before ``as_of``.

    ``monitor.exposure`` is the tier at the LATEST close (pipeline.py:
    ``target_exposure.iloc[-1]``). The book acts on the tier read at the
    rebalance-day close (Monday; Python weekday 0 = Monday), so mid-week the
    two can differ — on ~23% of days over the sample. The page shows both,
    each named by its basis, rather than one under the other's label.
    """
    dates, expo, breadth = (history.get(k) or [] for k in ("dates", "exposure", "breadth"))
    if not as_of or not dates or len(dates) != len(expo):
        return None
    cutoff = pd.Timestamp(as_of)
    for i in range(len(dates) - 1, -1, -1):
        d = pd.Timestamp(dates[i])
        if d <= cutoff and d.weekday() == weekday:
            return {
                "date": dates[i],
                "exposure": expo[i],
                "breadth": breadth[i] if i < len(breadth) else None,
            }
    return None


def tier_state(exposure: float | None) -> str | None:
    """Engine tiers are 0 / 0.3 / 0.6 / 1.0."""
    if exposure is None:
        return None
    if exposure <= 0:
        return "RISK_OFF"
    return "PARTIAL" if exposure < 1 else "RISK_ON"


def tilt_chip(panel: dict[str, CoinData]) -> dict:
    """ETH/BTC price ratio, 50-day SMA against 200-day SMA.

    The crypto stand-in for the ETF page's EM-tilt chip (EEM/SPY). Computed
    here because no project output carries it; display-only, feeds nothing.
    The ratio is formed on the dates both legs share, so a missing bar on
    one leg cannot pair a price with the wrong day of the other.
    """
    num, den = panel.get(TILT_NUMERATOR), panel.get(TILT_DENOMINATOR)
    label_pair = f"{TILT_NUMERATOR}/{TILT_DENOMINATOR}"
    if num is None or den is None:
        return {"kind": "eth_btc", "state": None, "label": f"{label_pair} · unavailable"}
    ratio = (num.close / den.close).dropna()
    ma50 = si.sma(ratio, si.MA_MID)
    ma200 = si.sma(ratio, si.MA_LONG)
    if len(ratio) == 0 or not np.isfinite(ma200.iloc[-1]):
        return {"kind": "eth_btc", "state": None,
                "label": f"{label_pair} · insufficient history"}
    spread = float(ma50.iloc[-1] / ma200.iloc[-1] - 1.0)
    on = spread > 0
    return {
        "kind": "eth_btc",
        "state": "ON" if on else "OFF",
        "value": spread,
        "ratio": float(ratio.iloc[-1]),
        "label": (
            f"{label_pair} · 50D {'above' if on else 'below'} 200D "
            f"({spread * 100:+.1f}%)"
        ),
        "as_of": ratio.index[-1].strftime("%Y-%m-%d"),
    }


def overlay_chips(monitor: dict | None, panel: dict[str, CoinData]) -> list[dict]:
    """Breadth gate (target) and book (held) — kept as SEPARATE chips.

    The gate is read at Monday's close and executes on the next bar, so the
    target tier and the actual holdings legitimately diverge mid-week. The
    project rule is that the two are never conflated: the gate chip carries
    only the target, the book chip only what is held.
    """
    chips: list[dict] = []
    if monitor is None:
        chips.append({"kind": "breadth_gate", "state": None,
                      "label": "Breadth gate · dashboard data unavailable"})
    else:
        breadth, exposure = monitor.get("breadth"), monitor.get("exposure")
        monday = monitor.get("monday_read")
        m_expo = monday.get("exposure") if monday else None
        differs = m_expo is not None and exposure is not None and abs(m_expo - exposure) > 1e-9
        if breadth is not None and exposure is not None:
            label = (f"Gate tier at latest close ({monitor.get('as_of')}) · "
                     f"{breadth * 100:.0f}% breadth → {exposure * 100:.0f}%")
            if m_expo is not None:
                label += (f" · last Monday read ({monday['date']}) "
                          f"{m_expo * 100:.0f}%" + (" — differs" if differs else ""))
        else:
            label = "Breadth gate · unavailable"
        chips.append({
            "kind": "breadth_gate",
            "state": tier_state(exposure),
            "monday_state": tier_state(m_expo),
            "differs_from_monday": differs,
            "value": breadth,
            "exposure": exposure,
            "monday_read": monday,
            "label": label,
            "as_of": monitor.get("as_of"),
            "engine_version": monitor.get("engine_version"),
        })
        held = [h.get("coin") for h in (monitor.get("holdings") or []) if h.get("coin")]
        cash = monitor.get("cash_weight") or 0.0
        chips.append({
            "kind": "book",
            "state": "HELD" if held else "CASH",
            "held": held,
            "label": (
                f"Book · {', '.join(held)}"
                + (f" + {cash * 100:.0f}% cash" if cash > 0.005 else "")
                if held else "Book · all cash"
            ),
            "as_of": monitor.get("as_of"),
            "last_rebal": monitor.get("last_rebal"),
        })
    chips.append(tilt_chip(panel))
    return chips


# =========================================================================
# Guards
# =========================================================================
def assert_invariants(rows: list[dict], expected: int) -> None:
    """Cross-sectional properties the page cannot be allowed to violate."""
    problems: list[str] = []

    if len(rows) != expected:
        problems.append(f"{len(rows)} rows built, panel has {expected} coins")

    ranks = [r["rank"] for r in rows if r["rank"] is not None]
    if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
        dupes = {r for r in ranks if ranks.count(r) > 1}
        problems.append(
            f"ranks are not a permutation of 1..{len(ranks)} "
            f"(duplicates: {sorted(dupes) or 'none'}, max {max(ranks)})"
        )

    # A withheld percentile is legitimate where the coin lacks the history;
    # a missing one on a coin that has it, or an out-of-range one, is not.
    minimum_bars = {
        "rv_pctl": si.MIN_PCTL_OBS + si.RV_WINDOW,
        "bbw_pctl": si.MIN_PCTL_OBS + si.BBW_WINDOW,
    }
    for row in rows:
        for key, needed in minimum_bars.items():
            value = row.get(key)
            if value is None or not np.isfinite(value):
                if (row.get("n_bars") or 0) >= needed and not row.get("stale"):
                    problems.append(
                        f"{row['ticker']}: {key} is missing despite "
                        f"{row.get('n_bars')} bars (needs {needed})"
                    )
                continue
            if not (0.0 <= value <= 100.0):
                problems.append(f"{row['ticker']}: {key} = {value} outside [0,100]")
        if row["ticker"] == BENCHMARK and row.get("rs_1m") is not None:
            problems.append(f"{BENCHMARK}: RS against itself must be blank")

    if problems:
        raise ScannerBuildError(
            "cross-sectional invariants failed:\n  - " + "\n  - ".join(problems)
        )


def assert_no_naive_divergence(
    panel: dict[str, CoinData], as_of: str, sample_size: int = 3
) -> list[str]:
    """Vectorised path against the naive one, on a date-rotated sample.

    The only check that can catch a rolling-window regression: it changes
    every number at once and breaks no test sharing the implementation.
    """
    tickers = sorted(panel)
    seed = sum(ord(c) for c in as_of)
    picked = [tickers[(seed + i) % len(tickers)] for i in range(min(sample_size, len(tickers)))]

    problems: list[str] = []
    for ticker in picked:
        frame = panel[ticker].frame
        close = frame["close"].tolist()
        checks = {
            "sma20": (si.sma(frame["close"], 20).iloc[-1], si.naive_sma_latest(close, 20)),
            "rsi14": (float(si.rsi(frame["close"]).iloc[-1]), si.naive_rsi_latest(close)),
            "atr_pct": (
                si.atr_pct(frame["high"], frame["low"], frame["close"]),
                si.naive_atr_pct_latest(frame["high"].tolist(), frame["low"].tolist(), close),
            ),
        }
        for name, (fast, naive) in checks.items():
            if not np.isfinite(fast) and not np.isfinite(naive):
                continue
            if not np.isclose(fast, naive, rtol=1e-9, atol=0):
                problems.append(f"{ticker} {name}: vectorised {fast!r} != naive {naive!r}")
    if problems:
        raise ScannerBuildError("naive-recompute divergence:\n  - " + "\n  - ".join(problems))
    return picked


def assert_date_indexed_recompute(
    ranked: dict[str, CoinData], rows: list[dict], benchmark: pd.Series, gapped: set[str],
) -> str:
    """Recompute the calendar-converted columns by DATE and compare.

    The production path is positional (N bars back). This one looks up the
    close N CALENDAR DAYS back by date, independently, for 12-1, vs 52W high,
    1M return, RS 1M against BTC, the composite rank and delta-R. On a
    gap-free calendar the two must agree exactly; a wrong constant, an
    off-by-one or a date misalignment in either path breaks the equality.
    Coins with a disclosed gap in the window are skipped (the two paths
    legitimately differ there), and so is the rank check if any ranked coin
    is gapped, because every rank depends on every row.
    """
    def at(close: pd.Series, when: pd.Timestamp) -> float:
        v = close.get(when)
        return float(v) if v is not None and np.isfinite(v) else float("nan")

    def horizon_by_date(close: pd.Series, end: pd.Timestamp) -> dict[int, float]:
        last = at(close, end)
        return {h: last / at(close, end - pd.Timedelta(days=h)) - 1.0 for h in si.RANK_HORIZONS}

    by_ticker = {r["ticker"]: r for r in rows}
    problems: list[str] = []

    def same(label: str, published, recomputed: float) -> None:
        pub = float("nan") if published is None else float(published)
        if not (np.isnan(pub) and np.isnan(recomputed)) and not np.isclose(
            pub, recomputed, rtol=1e-10, atol=1e-12
        ):
            problems.append(f"{label}: published {pub!r} != date-indexed {recomputed!r}")

    checked = sorted(t for t in ranked if t not in gapped)
    for t in checked:
        close, end, row = ranked[t].close, ranked[t].as_of, by_ticker[t]
        day = pd.Timedelta(days=1)
        same(f"{t} 12-1", row["mom_12_1"],
             at(close, end - si.MOMENTUM_SKIP * day) / at(close, end - si.DAYS_YEAR * day) - 1)
        window = close[(close.index > end - si.DAYS_YEAR * day) & (close.index <= end)]
        same(f"{t} vs 52W", row["vs_52w_high"], at(close, end) / window.max() - 1)
        same(f"{t} 1M", row["ret_1m"],
             at(close, end) / at(close, end - si.MOMENTUM_SKIP * day) - 1)
        if t != BENCHMARK and not benchmark.empty:
            start = end - si.MOMENTUM_SKIP * day
            same(f"{t} RS 1M", row["rs_1m"],
                 np.log(at(close, end) / at(close, start))
                 - np.log(at(benchmark, end) / at(benchmark, start)))

    rank_note = "rank check skipped (gapped coin in the cross-section)"
    if not (gapped & set(ranked)):
        now = si.rank_from_horizon_returns(pd.DataFrame(
            {t: horizon_by_date(d.close, d.as_of) for t, d in ranked.items()}).T).ranks
        back = pd.Timedelta(days=si.RANK_DELTA_LOOKBACK)
        prior = si.rank_from_horizon_returns(pd.DataFrame(
            {t: horizon_by_date(d.close, d.as_of - back) for t, d in ranked.items()}).T).ranks
        for t in ranked:
            r = by_ticker[t]
            exp_rank = int(now[t]) if t in now.index else None
            if r["rank"] != exp_rank:
                problems.append(f"{t} rank: published {r['rank']} != date-indexed {exp_rank}")
            exp_delta = (int(prior[t] - now[t])
                         if t in now.index and t in prior.index else None)
            if r["rank_delta"] != exp_delta:
                problems.append(
                    f"{t} delta-R: published {r['rank_delta']} != date-indexed {exp_delta}")
        rank_note = f"rank and delta-R on {len(ranked)} coins"

    if problems:
        raise ScannerBuildError(
            "date-indexed recompute divergence:\n  - " + "\n  - ".join(problems[:20]))
    return f"date-indexed recompute passed: columns on {len(checked)} coins, {rank_note}"


# =========================================================================
# Build
# =========================================================================
def days_behind(as_of: pd.Timestamp, reference: pd.Timestamp) -> int:
    """Whole UTC calendar days from ``as_of`` to ``reference`` (0 if level).

    Crypto trades every day, so calendar days ARE sessions; no business-day
    calendar applies. Timestamp arithmetic handles month and year ends.
    """
    return int((reference.normalize() - as_of.normalize()).days)


def build(
    prices_path: Path = PRICES_PATH,
    dashboard_path: Path = DASHBOARD_PATH,
    today: pd.Timestamp | None = None,
) -> tuple[dict, dict[str, CoinData]]:
    """Returns (page payload, the panel it was built from).

    ``today`` is the UTC date of the build (defaults to now). The last CLOSED
    daily candle on that date is the previous day, which is what the panel
    is expected to reach.
    """
    report = BuildReport()
    gapped: set[str] = set()
    panel = load_panel(prices_path, report, gapped)

    panel_as_of = max(d.as_of for d in panel.values())
    as_of_iso = panel_as_of.strftime("%Y-%m-%d")
    today = (today or pd.Timestamp(datetime.now(timezone.utc).date())).normalize()
    expected_close = today - pd.Timedelta(days=1)
    panel_lag = days_behind(panel_as_of, expected_close)
    if panel_lag > 0:
        report.notes.append(
            f"latest bar {as_of_iso} is {panel_lag} day(s) behind the last closed "
            f"candle ({expected_close:%Y-%m-%d}) — check data/fetch_status.json"
        )

    benchmark = panel[BENCHMARK].close if BENCHMARK in panel else pd.Series(dtype=float)
    if benchmark.empty:
        report.notes.append(f"{BENCHMARK} unavailable — RS column withheld for every row")

    # Stale rows leave the cross-section: their returns end on an older date,
    # so ranking them against current rows is not like-for-like, and they
    # would move every other row's percentiles and crossings. They stay on the
    # page, greyed, with rank "—".
    lags = {t: days_behind(d.as_of, panel_as_of) for t, d in panel.items()}
    stale_tickers = {t for t, lag in lags.items() if lag > STALE_DAYS}
    ranked = {t: d for t, d in panel.items() if t not in stale_tickers}

    now_returns = pd.DataFrame({t: horizon_returns(d) for t, d in ranked.items()}).T
    rank_now = si.rank_from_horizon_returns(now_returns)
    prior_returns = pd.DataFrame({
        t: horizon_returns(d, offset=si.RANK_DELTA_LOOKBACK)
        for t, d in ranked.items()
        if len(d.close) > si.RANK_DELTA_LOOKBACK + min(si.MIN_RANK_HORIZONS)
    }).T
    rank_prior = (
        si.rank_from_horizon_returns(prior_returns) if not prior_returns.empty else None
    )

    monitor = load_monitor(dashboard_path)
    held = {h.get("coin") for h in ((monitor or {}).get("holdings") or [])}
    investable = set((monitor or {}).get("investable_names") or [])
    if monitor is None:
        report.notes.append("dashboard_data.json monitor block unavailable — "
                            "no gate, book or investable tags")
    elif monitor.get("as_of") and monitor.get("as_of") != as_of_iso:
        report.notes.append(
            f"strategy state as of {monitor.get('as_of')}, prices as of {as_of_iso}"
        )

    rows: list[dict] = []
    alerts: list[Alert] = []
    for ticker in sorted(panel):
        data = panel[ticker]
        cols = build_columns(data, benchmark)
        lag = lags[ticker]
        stale = ticker in stale_tickers
        if stale:
            report.stale.append(f"{ticker}: {lag} days behind — excluded from the ranking")
        else:
            alerts.extend(build_alerts(data, cols))

        rank = rank_now.ranks.get(ticker)
        prior = rank_prior.ranks.get(ticker) if rank_prior is not None else None
        rows.append({
            "ticker": ticker,
            "name": COIN_NAMES.get(ticker, ticker),
            "held": ticker in held,
            "investable": ticker in investable,
            "as_of": data.as_of.strftime("%Y-%m-%d"),
            "sessions_behind": lag,
            "stale": stale,
            "rank": int(rank) if rank is not None else None,
            "rank_delta": (
                int(prior - rank) if (rank is not None and prior is not None) else None
            ),
            "rank_truncated": bool(rank_now.truncated.get(ticker, False)),
            **cols,
        })

    alerts.extend(rank_crossings(ranked, rank_now))

    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0))
    assert_invariants(rows, expected=len(panel))
    checked = assert_no_naive_divergence(panel, as_of_iso)
    report.notes.append(f"naive-recompute check passed on {', '.join(checked)}")
    report.notes.append(assert_date_indexed_recompute(ranked, rows, benchmark, gapped))
    if rank_now.unrankable:
        report.notes.append(
            f"unrankable (insufficient history): {', '.join(rank_now.unrankable)}"
        )

    frozen = sorted(DELISTED_ON_BINANCE)
    return {
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of_iso,
        "as_of_mixed": len({r["as_of"] for r in rows}) > 1,
        "expected_close": expected_close.strftime("%Y-%m-%d"),
        "panel_days_behind": panel_lag,
        "n_rows": len(rows),
        "benchmark": BENCHMARK,
        "percentile_window": si.PCTL_WINDOW,
        "days_year": si.DAYS_YEAR,
        "rank_horizons": list(si.RANK_HORIZONS),
        "rank_delta_lookback": si.RANK_DELTA_LOOKBACK,
        "rank_cross_cut": RANK_CROSS_CUT,
        "rank_cross_confirm": RANK_CROSS_CONFIRM,
        "frozen_excluded": frozen,
        "parameters_validated": False,
        "parameter_note": (
            "All parameters are industry defaults (RSI 14, MA 20/50/200, ATR 14, "
            "BBW 20/2sigma, equal-weight four-horizon momentum composite), with the "
            "time-based look-backs converted to the 365-day crypto calendar "
            "(1M/3M/6M/12M = 30/91/182/365 days, percentile window 730 days). "
            "NONE has been validated on this universe."
        ),
        "overlays": overlay_chips(monitor, panel),
        "alerts": [
            {"ticker": a.ticker, "kind": a.kind, "label": a.label, "value": a.value}
            for a in order_alerts(alerts)
        ],
        "alerts_display_cap": MAX_CHIPS,
        "rows": rows,
        "data_health": {
            "failures": report.failures,
            "stale": report.stale,
            "notes": report.notes,
            "stale_threshold_days": STALE_DAYS,
        },
    }, panel


def _round_sig(value: float | None, digits: int = 5) -> float | None:
    """Significant-figure rounding so one rule serves BTC at ~100,000 and
    DOGE at ~0.2. Charts need no more precision, and it halves the payload."""
    if value is None or not np.isfinite(value):
        return None
    if value == 0:
        return 0.0
    exponent = int(np.floor(np.log10(abs(value))))
    return round(float(value), max(0, digits - 1 - exponent))


def build_history(panel: dict[str, CoinData]) -> dict:
    """Close history for the row charts, on ONE shared UTC calendar.

    Every live coin trades on the same 24/7 calendar, so dates are published
    once and each coin's closes align to them (null where it has no bar).
    MA50 / MA200 are computed in the browser from the closes.
    """
    dates = sorted({d for data in panel.values() for d in data.frame.index})
    dates = dates[-HISTORY_DAYS:]
    axis = [d.strftime("%Y-%m-%d") for d in dates]
    series: dict[str, dict] = {}
    for ticker, data in panel.items():
        closes = data.close.reindex(dates)
        series[ticker] = {
            "calendar": "UTC",
            "close": [_round_sig(v) if pd.notna(v) else None for v in closes],
        }
    return {
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sessions": len(axis),
        "note": (
            "Daily closes in USDT (Binance spot), one shared UTC calendar. Moving "
            "averages are computed client-side. The window matches the percentile "
            "window on the scanner page."
        ),
        "calendars": {"UTC": axis},
        "series": series,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--prices", default=str(PRICES_PATH), help="prices parquet")
    parser.add_argument("--dashboard", default=str(DASHBOARD_PATH),
                        help="dashboard_data.json carrying the monitor block")
    parser.add_argument("--today", default=None,
                        help="UTC build date YYYY-MM-DD (default: now)")
    parser.add_argument("--out", default=str(OUT_PATH), help="output JSON path")
    parser.add_argument("--history-out", default=str(HISTORY_PATH),
                        help="chart history JSON path")
    args = parser.parse_args(argv)

    payload, panel = build(
        Path(args.prices), Path(args.dashboard),
        pd.Timestamp(args.today) if args.today else None,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    history = build_history(panel)
    hist_path = Path(args.history_out)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(history, separators=(",", ":")) + "\n", encoding="utf-8")

    health = payload["data_health"]
    print(f"as-of {payload['as_of']}  rows {payload['n_rows']}  "
          f"alerts {len(payload['alerts'])}")
    print(f"wrote {hist_path} ({hist_path.stat().st_size / 1024:.0f} KB, "
          f"{len(history['series'])} series x {history['sessions']} days)")
    for note in health["notes"]:
        print(f"  note: {note}")
    for s in health["stale"]:
        print(f"  STALE: {s}")
    for f in health["failures"]:
        print(f"  FAILED: {f}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScannerBuildError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
