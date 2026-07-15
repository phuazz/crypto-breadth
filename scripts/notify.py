"""
notify.py
---------
Detect new trade events since the last run and email the user.

State machine (idempotent — safe to re-run):
  1. Load data/dashboard_data.json (produced by scripts/pipeline.py).
  2. Load data/last_alert_state.json — set of {date::coin::action} keys
     we have already alerted on.
  3. New events = events from the last 21 days that are not in the seen set.
  4. For each new event, build and send an SMTP email.
  5. Append new event keys to the state file and write it back.

Environment variables (all required to actually send):
  EMAIL_FROM        — sender address (e.g. phuazz@gmail.com)
  EMAIL_TO          — recipient address (probably the same)
  EMAIL_PASSWORD    — SMTP password (for Gmail: an App Password, NOT account pw)
  EMAIL_SMTP_HOST   — defaults to smtp.gmail.com
  EMAIL_SMTP_PORT   — defaults to 587 (STARTTLS)

If credentials are missing, the script still updates the state file (so we
do not flood once they get set later) and exits 0. That way CI does not
fail before secrets are configured.

Run cadence: daily, after pipeline.py. See .github/workflows/daily-check.yml.
"""

from __future__ import annotations

import io
import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = PROJECT_ROOT / "data" / "dashboard_data.json"
COIN_SIGNALS_JSON = PROJECT_ROOT / "data" / "coin_signals.json"
STATE_FILE = PROJECT_ROOT / "data" / "last_alert_state.json"

# 3-month chart window for the inline email chart — matches the
# Signal Explorer's default zoom on the dashboard.
CHART_WINDOW_DAYS = 90

DASHBOARD_URL = "https://phuazz.github.io/crypto-breadth/"
REPO_URL = "https://github.com/phuazz/crypto-breadth"

# Only alert on trades within the last N days. Older events are assumed
# already seen (or not worth alerting on for a fresh install).
ALERT_WINDOW_DAYS = 21


def event_key(e: dict) -> str:
    return f"{e['date']}::{e['coin']}::{e['action']}"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": [], "last_run_at": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def format_email(event: dict, dash: dict) -> tuple[str, str, str]:
    """Returns (subject, plain_body, html_body) for one trade event."""
    action = event["action"]              # entry / exit / resize
    coin = event["coin"]
    trigger = event["trigger"]            # rebal / daily exit / rebal + exit
    old_w = event["old_w"]                # 0..1
    new_w = event["new_w"]
    delta = event["delta"]
    sig_breadth = event.get("sig_breadth")
    sig_exposure = event.get("sig_exposure")
    date = event["date"]

    walk = dash.get("walkthrough", {})
    n_inv = walk.get("step1", {}).get("output_count")
    n_elig = walk.get("step2", {}).get("output_count")
    selected = walk.get("step4", {}).get("selected", [])
    sample_end = dash.get("meta", {}).get("sample_end")

    full = dash.get("summary", {}).get("full", {})

    # Subject
    arrow = {"entry": "BUY", "exit": "SELL", "resize": "RESIZE"}[action]
    subject = f"[crypto-breadth] {arrow} {coin} — {date} ({trigger})"

    # Plain text body
    plain_lines = [
        f"crypto-breadth signal: {action.upper()} {coin}",
        f"Trade date: {date}    Trigger: {trigger}",
        "",
        f"Old weight:  {old_w * 100:>5.1f}%",
        f"New weight:  {new_w * 100:>5.1f}%",
        f"Delta:       {('+' if delta >= 0 else '')}{delta * 100:>4.1f} pp",
        "",
        "Why this triggered",
        "------------------",
    ]
    if action == "entry":
        plain_lines.append(
            f"  {coin} entered the top-{len(selected) or '?'} momentum rank and passed "
            f"the trend filter (close > 50d MA, MA rising)."
        )
    elif action == "exit":
        if trigger == "daily exit":
            plain_lines.append(
                f"  {coin} closed below its 50d MA, triggering the daily trend-exit "
                f"override before the next weekly rebalance."
            )
        else:
            plain_lines.append(
                f"  {coin} fell out of the top-{len(selected) or '?'} momentum rank "
                f"or lost the trend filter."
            )
    else:
        plain_lines.append(
            f"  Weight on {coin} adjusted at the weekly rebalance. The composite "
            f"momentum score moved relative to peers."
        )
    plain_lines.append("")
    plain_lines.append("Signal context at trade")
    plain_lines.append("-----------------------")
    if sig_breadth is not None:
        plain_lines.append(f"  Breadth %      {sig_breadth * 100:.0f}% of investable universe above 50d MA")
    if sig_exposure is not None:
        plain_lines.append(f"  Target gross   {sig_exposure * 100:.0f}%")
    if n_inv is not None:
        plain_lines.append(f"  Investable     {n_inv} of 25 coins (rolling liquidity gate)")
    if n_elig is not None:
        plain_lines.append(f"  Trend-eligible {n_elig} coins")
    if selected:
        plain_lines.append(f"  Current top-N  {', '.join(selected)}")
    if full.get("sharpe"):
        plain_lines.append("")
        plain_lines.append("Strategy stats (full sample, for context)")
        plain_lines.append(
            f"  CAGR {full['cagr']*100:.1f}%  ·  Sharpe {full['sharpe']:.2f}  ·  "
            f"MaxDD {full['max_dd']*100:.1f}%"
        )
    plain_lines += [
        "",
        f"Live dashboard:  {DASHBOARD_URL}",
        f"Source code:     {REPO_URL}",
        "",
        "This is research output, not financial advice. The strategy is not deployed.",
        f"Sample window ends {sample_end}.",
    ]
    plain_body = "\n".join(plain_lines)

    # HTML body (matches the dashboard's style — accent #1351b4, etc.)
    arrow_emoji = "▲" if action == "entry" else ("▼" if action == "exit" else "↔")
    action_color = ("#1d7a3a" if action == "entry" else
                    "#b3261e" if action == "exit" else "#1351b4")
    explorer_url = f"{DASHBOARD_URL}?coin={coin}#explorer"
    walkthrough_url = f"{DASHBOARD_URL}#signal"

    why_html = ""
    if action == "entry":
        why_html = (f"<strong>{coin}</strong> entered the top-N momentum rank "
                    f"and passed the trend filter (close above its own 50d MA, "
                    f"MA rising).")
    elif action == "exit":
        if trigger == "daily exit":
            why_html = (f"<strong>{coin}</strong> closed below its own 50d MA, "
                        f"triggering the <em>daily trend-exit override</em> ahead "
                        f"of the next weekly rebalance. This is the asymmetric "
                        f"exit rule designed to cut losers fast.")
        else:
            why_html = (f"<strong>{coin}</strong> fell out of the top-N momentum "
                        f"rank or lost the trend filter at the weekly rebalance.")
    else:
        why_html = (f"Weight on <strong>{coin}</strong> adjusted at the weekly "
                    f"rebalance. The composite momentum score moved relative to "
                    f"the other ranked names.")

    html = f"""<!DOCTYPE html>
<html><body style="font-family: 'Inter', -apple-system, sans-serif; color: #111418; background: #f7f8fa; margin: 0; padding: 24px;">
<div style="max-width: 640px; margin: 0 auto; background: white; border: 1px solid #e3e6ea; border-radius: 8px; padding: 24px 28px;">
  <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: #6b727a; font-weight: 700; margin-bottom: 6px;">crypto-breadth signal</div>
  <h1 style="font-size: 24px; margin: 0 0 6px 0; color: {action_color};">
    {arrow_emoji} {action.upper()} {coin}
  </h1>
  <div style="color: #4a5159; font-size: 14px; margin-bottom: 18px;">
    {date} &nbsp;·&nbsp; {trigger}
  </div>

  <table style="width: 100%; border-collapse: collapse; margin: 12px 0 20px; font-size: 14px;">
    <tr>
      <td style="padding: 8px 10px; background: #f7f8fa; border-radius: 4px; width: 33%;">
        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b727a; font-weight: 600;">Old weight</div>
        <div style="font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums;">{old_w * 100:.1f}%</div>
      </td>
      <td style="padding: 8px 10px;">
        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b727a; font-weight: 600;">New weight</div>
        <div style="font-size: 22px; font-weight: 700; color: {action_color}; font-variant-numeric: tabular-nums;">{new_w * 100:.1f}%</div>
      </td>
      <td style="padding: 8px 10px; background: #f7f8fa; border-radius: 4px;">
        <div style="font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b727a; font-weight: 600;">Delta</div>
        <div style="font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums;">{'+' if delta >= 0 else ''}{delta * 100:.1f}pp</div>
      </td>
    </tr>
  </table>

  <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b727a; font-weight: 700; margin-top: 14px;">Why this triggered</div>
  <p style="margin: 6px 0 16px; line-height: 1.55; font-size: 14px;">{why_html}</p>

  <!-- {{CHART_PLACEHOLDER}} -->

  <div style="font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em; color: #6b727a; font-weight: 700; margin-top: 14px;">Signal context at trade</div>
  <table style="width: 100%; border-collapse: collapse; margin: 6px 0 16px; font-size: 13px; font-variant-numeric: tabular-nums;">
    <tr><td style="padding: 4px 0; color: #4a5159;">Breadth</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">{f'{sig_breadth*100:.0f}% above 50d MA' if sig_breadth is not None else '—'}</td></tr>
    <tr><td style="padding: 4px 0; color: #4a5159;">Target gross exposure</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">{f'{sig_exposure*100:.0f}%' if sig_exposure is not None else '—'}</td></tr>
    <tr><td style="padding: 4px 0; color: #4a5159;">Investable universe</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">{n_inv if n_inv is not None else '—'} of 25</td></tr>
    <tr><td style="padding: 4px 0; color: #4a5159;">Trend-eligible</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">{n_elig if n_elig is not None else '—'} coins</td></tr>
    <tr><td style="padding: 4px 0; color: #4a5159;">Current top-N picks</td><td style="padding: 4px 0; text-align: right; font-weight: 600;">{', '.join(selected) if selected else '—'}</td></tr>
  </table>

  <div style="margin: 18px 0 8px;">
    <a href="{explorer_url}" style="display: inline-block; background: #1351b4; color: white; padding: 10px 18px; border-radius: 5px; text-decoration: none; font-weight: 600; font-size: 13px; margin-right: 6px;">View {coin} chart →</a>
    <a href="{walkthrough_url}" style="display: inline-block; background: white; color: #1351b4; padding: 10px 18px; border-radius: 5px; text-decoration: none; font-weight: 600; font-size: 13px; border: 1px solid #1351b4;">Full signal walkthrough →</a>
  </div>

  <hr style="border: none; border-top: 1px solid #e3e6ea; margin: 22px 0 14px;">
  <div style="font-size: 11px; color: #6b727a; line-height: 1.5;">
    crypto-breadth v3.1 · sample ends {sample_end} · full Sharpe {full.get('sharpe', 0):.2f} ·
    This is research output, not financial advice. The strategy is not deployed.
    <a href="{REPO_URL}" style="color: #1351b4;">github.com/phuazz/crypto-breadth</a>
  </div>
</div>
</body></html>"""
    return subject, plain_body, html


def render_coin_chart(coin_data: dict, event: dict) -> bytes | None:
    """Render a 1-year price + 50d MA chart with the triggering event flagged.

    Returns PNG bytes or None if the coin has no usable history.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except Exception as e:
        print(f"  matplotlib import failed, sending email without chart: {e!r}")
        return None

    dates = [datetime.strptime(d, "%Y-%m-%d") for d in coin_data.get("dates", [])]
    closes = coin_data.get("close", [])
    mas = coin_data.get("ma", [])
    weights = coin_data.get("weight", [])
    coin_events = coin_data.get("events", [])
    if not dates:
        return None

    event_dt = datetime.strptime(event["date"], "%Y-%m-%d")
    start_dt = event_dt - timedelta(days=CHART_WINDOW_DAYS)
    end_dt = event_dt + timedelta(days=21)

    idxs = [i for i, d in enumerate(dates) if start_dt <= d <= end_dt]
    if not idxs:
        return None
    dates_w = [dates[i] for i in idxs]
    closes_w = [closes[i] for i in idxs]
    mas_w = [mas[i] for i in idxs]
    weights_w = [weights[i] for i in idxs]

    fig, ax = plt.subplots(figsize=(7.0, 3.6), dpi=120)

    # Held-period shading (blue tint, matches dashboard)
    start = None
    for i, (d, w) in enumerate(zip(dates_w, weights_w)):
        if w and w > 0.001 and start is None:
            start = d
        if (w is None or w <= 0.001) and start is not None:
            ax.axvspan(start, dates_w[i], color="#1351b4", alpha=0.10, zorder=0)
            start = None
    if start is not None:
        ax.axvspan(start, dates_w[-1], color="#1351b4", alpha=0.10, zorder=0)

    # Lines
    valid_closes = [(d, c) for d, c in zip(dates_w, closes_w) if c is not None]
    if valid_closes:
        cx, cy = zip(*valid_closes)
        ax.plot(cx, cy, color="#111418", linewidth=1.6, label="Close")
    valid_mas = [(d, m) for d, m in zip(dates_w, mas_w) if m is not None]
    if valid_mas:
        mx, my = zip(*valid_mas)
        ax.plot(mx, my, color="#1351b4", linestyle="--", linewidth=1.2, label="50d MA")

    # Trade markers within the window
    for e in coin_events:
        ed = datetime.strptime(e["date"], "%Y-%m-%d")
        if ed < start_dt or ed > end_dt:
            continue
        # snap to nearest weekly point's close
        ji = min(range(len(dates_w)), key=lambda j: abs((dates_w[j] - ed).total_seconds()))
        price = closes_w[ji]
        if price is None:
            continue
        if e["action"] == "entry":
            ax.scatter([ed], [price], marker="^", color="#1d7a3a",
                       s=85, zorder=5, edgecolor="white", linewidth=1.2)
        elif e["action"] == "exit":
            ax.scatter([ed], [price], marker="v", color="#b3261e",
                       s=85, zorder=5, edgecolor="white", linewidth=1.2)
        else:
            ax.scatter([ed], [price], marker="D", color="#1351b4",
                       s=65, zorder=5, edgecolor="white", linewidth=1.2)

    # Annotated triggering event
    ji = min(range(len(dates_w)), key=lambda j: abs((dates_w[j] - event_dt).total_seconds()))
    trig_price = closes_w[ji]
    if trig_price is not None:
        action = event["action"]
        arrow_color = {"exit": "#b3261e", "entry": "#1d7a3a"}.get(action, "#1351b4")
        verb = {"exit": "EXIT", "entry": "ENTRY", "resize": "RESIZE"}[action]
        # Annotation positioned above for entries, below for exits
        offset_y = -40 if action == "entry" else 40
        ax.annotate(
            f"{verb} on {event_dt.strftime('%b %d')}",
            xy=(event_dt, trig_price),
            xytext=(15, offset_y),
            textcoords="offset points",
            fontsize=10, color=arrow_color, fontweight="bold",
            arrowprops=dict(arrowstyle="->", color=arrow_color, lw=1.4),
            bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                      edgecolor=arrow_color, alpha=0.96, linewidth=1.2),
        )

    ax.set_yscale("log")
    ax.set_title(f"{event['coin']} — last 3 months",
                 fontsize=13, fontweight="bold", color="#111418", loc="left")
    ax.set_ylabel("Price (USDT, log)", fontsize=9, color="#4a5159")
    ax.grid(True, alpha=0.35, color="#eef0f3", linewidth=0.6)
    ax.legend(loc="upper left", fontsize=9, frameon=False)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.tick_params(axis="both", colors="#4a5159", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#e3e6ea")
    fig.patch.set_facecolor("white")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _mpl():
    """Lazy Agg-backend matplotlib import shared by the digest charts. Returns
    the pyplot module or None so a missing dep degrades to a chartless email."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        return plt
    except Exception as e:
        print(f"  matplotlib import failed, digest chart skipped: {e!r}")
        return None


def render_breadth_chart(ih: dict) -> bytes | None:
    """Breadth (share of investable coins above their 50d MA) over ~3 months,
    with the deployment-gate tiers shaded. Lets the reader see how close the
    strategy is to stepping exposure up or down, and the direction of travel."""
    plt = _mpl()
    if plt is None:
        return None
    import matplotlib.dates as mdates
    pts = [(datetime.strptime(d, "%Y-%m-%d"), b)
           for d, b in zip(ih.get("dates", []), ih.get("breadth", []))
           if isinstance(b, (int, float))]
    if len(pts) < 5:
        return None
    xs, ys = zip(*pts)
    fig, ax = plt.subplots(figsize=(7.0, 2.9), dpi=120)
    # Gate tiers: breadth <30 → cash, 30-50 → 30%, 50-70 → 60%, ≥70 → 100%.
    for lo, hi, col, lab in [(0.0, 0.30, "#fbeced", "cash"), (0.30, 0.50, "#fdf3e6", "30%"),
                             (0.50, 0.70, "#eaf1fb", "60%"), (0.70, 1.0, "#e9f5ee", "100%")]:
        ax.axhspan(lo, hi, color=col, zorder=0)
        ax.text(xs[0], hi - 0.03, f"  deploy {lab}", fontsize=7.5, color="#8a8f96",
                va="top", ha="left", zorder=2)
    for thr in (0.30, 0.50, 0.70):
        ax.axhline(thr, color="#c7ccd2", linewidth=0.8, linestyle="--", zorder=1)
    ax.plot(xs, ys, color="#111418", linewidth=1.8, zorder=3)
    ax.scatter([xs[-1]], [ys[-1]], s=46, color="#1351b4", zorder=4,
               edgecolor="white", linewidth=1.2)
    ax.annotate(f"{ys[-1] * 100:.0f}%", xy=(xs[-1], ys[-1]), xytext=(6, 7),
                textcoords="offset points", fontsize=10, fontweight="bold", color="#1351b4")
    ax.set_ylim(0, 1)
    ax.set_yticks([0, 0.3, 0.5, 0.7, 1.0])
    ax.set_yticklabels(["0", "30", "50", "70", "100"], fontsize=8)
    ax.set_ylabel("Breadth (% > 50d MA)", fontsize=9, color="#4a5159")
    ax.set_title("Breadth vs deployment gates — last 3 months",
                 fontsize=12, fontweight="bold", color="#111418", loc="left")
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax.tick_params(axis="both", colors="#4a5159", labelsize=8)
    for sp in ax.spines.values():
        sp.set_color("#e3e6ea")
    fig.patch.set_facecolor("white")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def render_deviation_chart(coin_signals: dict) -> bytes | None:
    """Horizontal bars: each investable coin's % distance from its own 50d MA,
    green above / red below. The cross-sectional make-up behind the one breadth
    number — who is leading, who is lagging, and who is on the cusp."""
    plt = _mpl()
    if plt is None:
        return None
    rows = []
    for coin, cd in (coin_signals or {}).items():
        cl, ma, iv = cd.get("close") or [], cd.get("ma") or [], cd.get("investable") or []
        if cl and ma and cl[-1] and ma[-1] and (not iv or iv[-1]):
            rows.append((coin, cl[-1] / ma[-1] - 1.0))
    if not rows:
        return None
    rows.sort(key=lambda r: r[1])  # ascending → largest lands at the top of barh
    names = [r[0] for r in rows]
    vals = [r[1] * 100 for r in rows]
    colors = ["#1d7a3a" if v >= 0 else "#b3261e" for v in vals]
    fig, ax = plt.subplots(figsize=(7.0, max(2.4, 0.32 * len(rows) + 0.9)), dpi=120)
    ax.barh(range(len(rows)), vals, color=colors, height=0.68, zorder=3)
    ax.axvline(0, color="#6b727a", linewidth=1.0, zorder=2)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(names, fontsize=8.5)
    for i, v in enumerate(vals):
        ax.text(v + (0.5 if v >= 0 else -0.5), i, f"{v:+.1f}%", va="center",
                ha="left" if v >= 0 else "right", fontsize=8, color="#4a5159")
    n_above = sum(1 for v in vals if v >= 0)
    ax.set_title(f"Distance from 50-day MA — {n_above} of {len(rows)} investable coins above the line",
                 fontsize=12, fontweight="bold", color="#111418", loc="left")
    ax.set_xlabel("Distance from 50-day moving average (%)", fontsize=9, color="#4a5159")
    ax.grid(True, axis="x", alpha=0.3, color="#eef0f3", linewidth=0.6)
    ax.tick_params(axis="both", colors="#4a5159", labelsize=8)
    lo, hi = min(vals + [0.0]), max(vals + [0.0])
    pad = max(5.0, (hi - lo) * 0.18)
    ax.set_xlim(lo - pad, hi + pad)
    for sp in ax.spines.values():
        sp.set_color("#e3e6ea")
    fig.patch.set_facecolor("white")
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def send_email(
    subject: str, plain: str, html: str, cfg: dict,
    chart_png: bytes | None = None, chart_cid: str | None = None,
    charts: list[tuple[str, bytes]] | None = None,
) -> None:
    # multipart/related so the inline image(s) can be referenced from HTML via cid:
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]

    # Inside that, multipart/alternative wraps text + html.
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(plain, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)

    # Single-chart form (instant alerts) plus a multi-chart list (the digest).
    embeds = list(charts or [])
    if chart_png and chart_cid:
        embeds.append((chart_cid, chart_png))
    for cid, png in embeds:
        img = MIMEImage(png, "png")
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=f"{cid}.png")
        msg.attach(img)

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
        server.starttls(context=context)
        server.login(cfg["from"], cfg["password"])
        server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())


def _email_cfg() -> dict:
    # GitHub injects unset secrets as empty strings, so coalesce with `or`.
    return {
        "from": os.environ.get("EMAIL_FROM") or "",
        "to": os.environ.get("EMAIL_TO") or "",
        "password": os.environ.get("EMAIL_PASSWORD") or "",
        "host": os.environ.get("EMAIL_SMTP_HOST") or "smtp.gmail.com",
        "port": int(os.environ.get("EMAIL_SMTP_PORT") or "587"),
    }


def _fmt_holding(h) -> str:
    if isinstance(h, dict):
        c = h.get("coin") or h.get("symbol") or "?"
        w = h.get("weight")
        return f"{c} {w*100:.0f}%" if isinstance(w, (int, float)) else str(c)
    return str(h)


# Mirrors backtest.Params.rebalance_weekday (0 = Monday). Held as a constant rather
# than importing the engine (notify.py deliberately carries no pandas dependency);
# tests/test_digest_timing.py asserts the two never drift apart.
REBALANCE_WEEKDAY = 0


def rebalance_timing(as_of: str, weekday: int = REBALANCE_WEEKDAY) -> tuple[bool, str]:
    """Describe when the gate reading at `as_of` actually trades.

    The engine sets target weights on `weekday`'s close and executes them at the
    NEXT bar (lag_days=1 in run_backtest). Therefore:
      - `as_of` IS the rebalance weekday → that reading is the LIVE signal and it
        executes at the following day's close. This is the NORMAL scheduled case:
        the Tuesday cron reports Monday's close, which trades that same Tuesday.
      - otherwise → the reading is not actionable; the gate is re-read at the next
        rebalance weekday's close and trades the day after.

    A hardcoded "applies at the next Monday rebalance" was wrong on the scheduled
    path: it told the reader a trade was six days away when it executed that night.

    Weekdays and offsets come from a date library, never from memory (Python:
    Monday=0 … Sunday=6; months are 1-indexed). Returns (is_live_signal, phrase,
    execution_date) using explicit ISO dates, so the phrase cannot be misread
    relative to whenever the mail happens to be opened.
    """
    d = datetime.strptime(as_of, "%Y-%m-%d").date()
    if d.weekday() == weekday:
        ex = d + timedelta(days=1)
        return True, f"this is the live signal; it executes at the {ex} close", ex
    days_ahead = (weekday - d.weekday()) % 7 or 7
    nxt = d + timedelta(days=days_ahead)
    ex = nxt + timedelta(days=1)
    return False, (f"not yet actionable; the gate is re-read at the {nxt} close "
                   f"and trades at the {ex} close"), ex


def _digest_due(state: dict) -> tuple[bool, str, int]:
    """(is_due, cadence_label, window_days). Cadence via env DIGEST_CADENCE:
    weekly (default) | daily | monthly | off. Weekly weekday via DIGEST_WEEKDAY
    (0=Mon, default 0). De-duplicated by state['last_digest_date'].

    DIGEST_FORCE (set by the manual "send digest now" button) overrides both the
    schedule and the same-day de-dup, so the button always delivers even if a
    scheduled digest already went out today. It never fires on the cron path."""
    force = (os.environ.get("DIGEST_FORCE") or "").strip().lower() in ("1", "true", "yes", "on")
    mode = (os.environ.get("DIGEST_CADENCE") or "weekly").strip().lower()
    if mode in ("off", "none", "disabled", ""):
        return (True, "Status", 7) if force else (False, "Off", 7)
    today = datetime.now(timezone.utc).date()
    last = state.get("last_digest_date")
    try:
        last_d = datetime.strptime(last, "%Y-%m-%d").date() if last else None
    except Exception:
        last_d = None
    if mode == "daily":
        return (force or last_d != today, "Daily", 1)
    if mode == "monthly":
        due = last_d is None or (last_d.year, last_d.month) != (today.year, today.month)
        return (force or due, "Monthly", 31)
    weekday = int(os.environ.get("DIGEST_WEEKDAY") or "0")
    return (force or (today.weekday() == weekday and last_d != today), "Weekly", 7)


def build_digest(dash: dict, *, window_days: int, cadence: str, coin_signals: dict | None = None) -> tuple[str, str, str]:
    """A regular status digest that ALWAYS sends (heartbeat), so a quiet week is
    never confused with a broken pipeline. Current position + changes + health."""
    mon = dash.get("monitor", {})
    meta = dash.get("meta", {})
    prov = dash.get("provenance", {})
    as_of = mon.get("as_of") or meta.get("sample_end", "?")
    holdings = mon.get("holdings") or []
    breadth = mon.get("breadth")
    exposure = mon.get("exposure")
    tier = mon.get("tier_label", "")
    n_inv = mon.get("investable_today")
    hold_items = [_fmt_holding(h) for h in holdings]
    hold_txt = ", ".join(hold_items) if hold_items else "all cash (0% invested)"
    # The ACTUAL book. `exposure` below is the GATE's target at the latest close and
    # is a different quantity — conflating the two reported "partial risk (30%
    # invested)" beside a 100%-cash book in the 2026-07-14 digest. Every line that
    # describes what we HOLD must key off held_gross; only gate/target lines may use
    # `exposure`.
    held_gross = sum(h["weight"] for h in holdings
                     if isinstance(h, dict) and isinstance(h.get("weight"), (int, float)))

    # Forward-looking target: what the strategy would hold if rebalanced at the latest
    # close. On the Tuesday send this equals the actual weekly rebalance (the signal is
    # the Monday close; execution is the Tuesday bar).
    final = dash.get("walkthrough", {}).get("final", {})
    tgt_h = final.get("holdings") or []
    tgt_pcw = final.get("per_coin_weight")
    tgt_names = [(h.get("coin") or h.get("symbol") or "?") if isinstance(h, dict) else str(h)
                 for h in tgt_h]
    if tgt_names and isinstance(tgt_pcw, (int, float)) and tgt_pcw > 0:
        tgt_txt = ", ".join(f"{n} {tgt_pcw*100:.0f}%" for n in tgt_names)
    else:
        tgt_txt = "all cash (0% invested)"

    # ---- week-over-week on the indicator history (breadth/exposure/investable/eligible)
    ih = dash.get("indicator_history", {})
    ihd = ih.get("dates", [])
    _pj = None
    if ihd:
        _last = datetime.strptime(ihd[-1], "%Y-%m-%d").date()
        _tgt = _last - timedelta(days=7)
        _pj = min(range(len(ihd)),
                  key=lambda i: abs((datetime.strptime(ihd[i], "%Y-%m-%d").date() - _tgt).days))

    def wow(key):
        vals = ih.get(key) or []
        now = vals[-1] if vals else None
        prev = vals[_pj] if (vals and _pj is not None) else None
        d = (now - prev) if isinstance(now, (int, float)) and isinstance(prev, (int, float)) else None
        return now, prev, d

    br_now, br_prev, br_d = wow("breadth")
    ex_now, ex_prev, ex_d = wow("exposure")
    iv_now, iv_prev, iv_d = wow("n_investable")
    el_now, el_prev, el_d = wow("n_eligible")

    # ---- gate proximity: distance to the next exposure tier up / down
    THR, TIER = [0.30, 0.50, 0.70], [0, 30, 60, 100]
    b = br_now if isinstance(br_now, (int, float)) else (breadth if isinstance(breadth, (int, float)) else 0.0)
    ti = sum(1 for t in THR if b >= t)
    # Phrase these in the GATE's frame, not the book's. "deploy"/"cut to" are book
    # verbs: with a 0% book and a 30% gate they rendered "De-risk: -8.5pp → cut to
    # 0%", which is incoherent when already holding nothing. The gate moves; whether
    # the book follows is decided at the next rebalance.
    gate_up = (f"+{(THR[ti]-b)*100:.1f}pp breadth → target {TIER[ti+1]}%"
               if ti < len(THR) else "already at the top tier (100%)")
    gate_dn = (f"-{(b-THR[ti-1])*100:.1f}pp breadth → target {TIER[ti-1]}%"
               if ti > 0 else "already at the bottom tier (0%)")

    # ---- strategy vs BTC over the last ~7 days: was the cash/exposure call right?
    eq = dash.get("equity", {})
    eqd = eq.get("dates", [])

    def _ret7(series):
        if not (eqd and series and len(eqd) == len(series)):
            return None
        _l = datetime.strptime(eqd[-1], "%Y-%m-%d").date()
        _t = _l - timedelta(days=7)
        j = min(range(len(eqd)),
                key=lambda i: abs((datetime.strptime(eqd[i], "%Y-%m-%d").date() - _t).days))
        return (series[-1] / series[j] - 1.0) if (series[j] and series[-1]) else None

    ret7 = _ret7(eq.get("strategy", []))
    btc7 = _ret7(eq.get("btc", []))
    rel7 = (ret7 - btc7) if isinstance(ret7, (int, float)) and isinstance(btc7, (int, float)) else None
    if rel7 is None:
        rel_tag = ""
    elif held_gross <= 0.005:
        # Key off the BOOK, not the gate: with a 30% gate and a flat-cash book, the
        # old `exposure`-based test printed "ahead"/"behind" as though an invested
        # book had raced BTC.
        rel_tag = " — cash helped" if rel7 >= 0 else " — cash cost"
    else:
        rel_tag = " — ahead" if rel7 >= 0 else " — behind"

    # ---- on the cusp: investable coins within 4% of their own 50d MA
    cusp = []
    for coin, cd in (coin_signals or {}).items():
        cl, ma, iv = cd.get("close") or [], cd.get("ma") or [], cd.get("investable") or []
        if cl and ma and cl[-1] and ma[-1] and (not iv or iv[-1]):
            dist = cl[-1] / ma[-1] - 1.0
            if abs(dist) <= 0.04:
                cusp.append((coin, dist))
    cusp.sort(key=lambda x: abs(x[1]))
    cusp = cusp[:6]

    # ---- inline charts (embedded as cid: images; skipped if matplotlib is absent)
    charts: list[tuple[str, bytes]] = []
    _breadth_png = render_breadth_chart(ih)
    if _breadth_png:
        charts.append(("digest-breadth", _breadth_png))
    _dev_png = render_deviation_chart(coin_signals or {})
    if _dev_png:
        charts.append(("digest-deviation", _dev_png))

    # ---- one-line read
    if held_gross <= 0.005:
        stance = "defensive — in cash (0% invested)"
    elif held_gross >= 0.99:
        stance = "fully risk-on (100% invested)"
    else:
        stance = f"partial risk ({held_gross*100:.0f}% invested)"
    if br_d is None:
        drift = ""
    elif br_d > 0.02:
        drift = f", breadth improving (+{br_d*100:.0f}pp w/w)"
    elif br_d < -0.02:
        drift = f", breadth deteriorating ({br_d*100:.0f}pp w/w)"
    else:
        drift = ", breadth ~flat w/w"
    # When the gate's target has moved MATERIALLY away from the book, say so and say
    # exactly when it bites — otherwise the reader cannot tell why a 30% gate sits
    # beside a 0% book. The 2pp tolerance is deliberate: a held book drifts with
    # prices between rebalances (a 30% book on four names easily reads 30.5%), and a
    # 0.5pp trigger fired on nearly every send, drowning the case this exists for.
    if isinstance(exposure, (int, float)) and abs(exposure - held_gross) > 0.02:
        _, _when, _exec_date = rebalance_timing(as_of)
        gate_note = f"; breadth now maps to a {exposure*100:.0f}% target — {_when}"
    else:
        gate_note, _exec_date = "", None
    # "X to re-engage" only makes sense while the gate itself is still at cash. If the
    # gate has already flipped, gate_note above is the operative message.
    tail = (f"; {gate_up} to re-engage."
            if (isinstance(exposure, (int, float)) and exposure <= 0.01
                and held_gross <= 0.005 and ti < len(THR)) else ".")
    read = f"{stance}{drift}{gate_note}{tail}"

    def _pct(x):
        return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "-"

    def _pp(d):
        return f"{'+' if d >= 0 else ''}{d*100:.0f}pp" if isinstance(d, (int, float)) else "-"

    def _pctd(x):
        return f"{'+' if x >= 0 else ''}{x*100:.1f}%" if isinstance(x, (int, float)) else "-"

    def _c(x):
        return f"{x:.0f}" if isinstance(x, (int, float)) else "-"

    def _dc(d):
        return f"{'+' if d >= 0 else ''}{d:.0f}" if isinstance(d, (int, float)) else "-"

    def _arr(d):
        return "→" if (not isinstance(d, (int, float)) or abs(d) < 1e-9) else ("↑" if d > 0 else "↓")

    def _acol(d):
        return "#6b727a" if (not isinstance(d, (int, float)) or abs(d) < 1e-9) else ("#1d7a3a" if d > 0 else "#b3261e")

    end_dt = datetime.strptime(meta.get("sample_end"), "%Y-%m-%d")
    changes = []
    for t in dash.get("trades", []):
        try:
            age = (end_dt - datetime.strptime(t["date"], "%Y-%m-%d")).days
        except Exception:
            continue
        if 0 <= age <= window_days:
            changes.append(t)
    changes.sort(key=lambda e: e["date"])

    def verb(a):
        return {"entry": "BUY", "exit": "SELL", "resize": "RESIZE"}.get(a, a.upper())

    def col(a):
        return "#1d7a3a" if a == "entry" else "#b3261e" if a == "exit" else "#1351b4"

    try:
        stale_days = (datetime.now(timezone.utc).date()
                      - datetime.strptime(meta.get("sample_end"), "%Y-%m-%d").date()).days
    except Exception:
        stale_days = 0
    stale_line = (f"WARNING: data is {stale_days} days old — the daily fetch may be failing; "
                  f"treat the position as indicative until it recovers."
                  if stale_days > 2 else None)
    stale_html = (f'<div style="background:#fef2f2;border:1px solid #fecaca;border-radius:6px;'
                  f'padding:10px 14px;margin:0 0 14px;color:#b3261e;font-weight:600;font-size:13px;">'
                  f'⚠ Data is {stale_days} days old — the daily fetch may be failing.</div>'
                  if stale_days > 2 else "")

    # Lead with the BOOK — that is what the reader is actually exposed to. Append the
    # pending change only when the gate has genuinely diverged, so an allocator
    # scanning an inbox can see a trade is coming without opening the mail.
    subject = f"[crypto-breadth] {cadence} signal update — {as_of}: {hold_txt}"
    if gate_note and _exec_date is not None:
        subject += f" → gate {_pct(exposure)}, trades {_exec_date}"

    L = [f"crypto-breadth — {cadence.lower()} signal update", f"As of {as_of}"]
    if stale_line:
        L += ["", stale_line]
    L += ["", "AT A GLANCE", f"  {read}"]
    # Book and gate are separate lines with separate labels. These are the two lines
    # that previously read "Holding: all cash (0% invested)" directly above "Gross
    # exposure: 30% (30% tier)" and made the gate look like the book.
    L += ["", "POSITION (what we hold now)",
          f"  Holding         : {hold_txt}",
          f"  Gross           : {_pct(held_gross)}"]
    L += ["", "GATE (what breadth targets at the latest close)",
          f"  Target exposure : {_pct(exposure)}  ({tier})",
          f"  Target if rebal : {tgt_txt}"]
    L += ["", "INDICATORS — now vs 1 week ago",
          f"  Breadth (>50d MA)   {_pct(br_now):>5}   was {_pct(br_prev):>5}   {_arr(br_d)} {_pp(br_d)}",
          f"  Target exposure     {_pct(ex_now):>5}   was {_pct(ex_prev):>5}   {_arr(ex_d)} {_pp(ex_d)}",
          f"  Investable coins    {_c(iv_now):>5}   was {_c(iv_prev):>5}   {_arr(iv_d)} {_dc(iv_d)}",
          f"  Trend-eligible      {_c(el_now):>5}   was {_c(el_prev):>5}   {_arr(el_d)} {_dc(el_d)}"]
    L += ["", "LAST 7 DAYS — SIMULATED strategy vs BTC",
          f"  Simulated {_pctd(ret7):>7}   BTC {_pctd(btc7):>7}"
          + (f"   relative {_pp(rel7)}{rel_tag}" if rel7 is not None else "")]
    L += ["", "GATE PROXIMITY",
          f"  Next tier up  : {gate_up}",
          f"  Next tier down: {gate_dn}"]
    if cusp:
        L += ["", "ON THE CUSP (within 4% of 50d MA)"]
        L += [f"  {c:<6} {'+' if d >= 0 else ''}{d*100:.1f}% vs MA  ({'above' if d >= 0 else 'below'})"
              for c, d in cusp]
    if charts:
        _clab = {"digest-breadth": "Breadth vs deployment gates (last 3 months)",
                 "digest-deviation": "Distance from 50-day MA, by coin"}
        L += ["", "CHARTS (see the HTML version)"]
        L += [f"  - {_clab.get(cid, cid)}" for cid, _ in charts]
    L += ["", f"CHANGES IN THE LAST {window_days} DAYS"]
    if changes:
        L += [f"  {t['date']}  {verb(t['action'])} {t['coin']}  "
              f"({t['trigger']}, {t['old_w']*100:.0f}%→{t['new_w']*100:.0f}%)" for t in changes]
    else:
        L.append("  None — holdings unchanged this period.")
    L += ["", f"Pipeline last ran {meta.get('generated_at','?')}; data through "
          f"{meta.get('sample_end','?')} ({prov.get('git_sha','?')}).", "",
          f"Dashboard: {DASHBOARD_URL}", "",
          "Research monitor — NOT deployed and NOT financial advice. The 2026-07 review",
          "rated this a small return-seeking satellite at most (real but modest edge; not a hedge).",
          "This email says WHAT the signal is doing, not to trade it."]
    plain = "\n".join(L)

    if changes:
        rows = "".join(
            f'<tr><td style="padding:5px 0;color:#4a5159;white-space:nowrap;">{t["date"]}</td>'
            f'<td style="padding:5px 0 5px 12px;font-weight:600;color:{col(t["action"])};">{verb(t["action"])} {t["coin"]}</td>'
            f'<td style="padding:5px 0;text-align:right;color:#4a5159;font-variant-numeric:tabular-nums;">{t["old_w"]*100:.0f}% → {t["new_w"]*100:.0f}% · {t["trigger"]}</td></tr>'
            for t in changes)
        changes_html = f'<table style="width:100%;border-collapse:collapse;font-size:13px;">{rows}</table>'
    else:
        changes_html = '<p style="margin:6px 0;color:#4a5159;font-size:14px;">None — holdings unchanged this period.</p>'
    def _irow(label, now, prev, d, is_pct):
        nows = _pct(now) if is_pct else _c(now)
        prevs = _pct(prev) if is_pct else _c(prev)
        ds = _pp(d) if is_pct else _dc(d)
        return (f'<tr><td style="padding:5px 0;color:#4a5159;">{label}</td>'
                f'<td style="padding:5px 8px;text-align:right;font-weight:600;">{nows}</td>'
                f'<td style="padding:5px 8px;text-align:right;color:#8a8a82;">{prevs}</td>'
                f'<td style="padding:5px 0 5px 8px;text-align:right;font-weight:600;color:{_acol(d)};white-space:nowrap;">{_arr(d)} {ds}</td></tr>')
    ind_rows = (_irow("Breadth (&gt;50d MA)", br_now, br_prev, br_d, True)
                + _irow("Target exposure", ex_now, ex_prev, ex_d, True)
                + _irow("Investable coins", iv_now, iv_prev, iv_d, False)
                + _irow("Trend-eligible", el_now, el_prev, el_d, False))
    def _rcol(x):
        return "#1d7a3a" if isinstance(x, (int, float)) and x >= 0 else "#b3261e"
    ret7_html = (
        '<div style="margin:8px 0 16px;padding:10px 14px;background:#f7f8fa;border-radius:6px;font-size:13px;color:#4a5159;">'
        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.06em;color:#6b727a;font-weight:700;margin-bottom:4px;">Last 7 days — simulated strategy vs BTC</div>'
        f'Simulated strategy <strong style="color:{_rcol(ret7)};">{_pctd(ret7)}</strong>'
        f' &nbsp;·&nbsp; BTC <strong style="color:{_rcol(btc7)};">{_pctd(btc7)}</strong>'
        + (f' &nbsp;·&nbsp; relative <strong style="color:{_rcol(rel7)};">{_pp(rel7)}</strong>'
           f'<span style="color:#6b727a;">{rel_tag}</span>' if rel7 is not None else '')
        + '</div>')

    def _chart_block(cid, title):
        return (f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.06em;'
                f'color:#6b727a;font-weight:700;margin-top:4px;">{title}</div>'
                f'<img src="cid:{cid}" alt="{title}" style="display:block;width:100%;max-width:600px;'
                f'height:auto;border:1px solid #e3e6ea;border-radius:6px;margin:6px 0 18px;">')
    breadth_block = _chart_block("digest-breadth", "Breadth and the deployment gate") if _breadth_png else ""
    dev_block = _chart_block("digest-deviation", "Who is above their 50-day line") if _dev_png else ""

    if cusp:
        cusp_rows = "".join(
            f'<tr><td style="padding:4px 0;font-weight:600;">{c}</td>'
            f'<td style="padding:4px 0;text-align:right;font-variant-numeric:tabular-nums;color:{"#1d7a3a" if d >= 0 else "#b3261e"};">'
            f'{"+" if d >= 0 else ""}{d*100:.1f}% vs 50d MA ({"above" if d >= 0 else "below"})</td></tr>'
            for c, d in cusp)
        cusp_html = (f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:0.06em;color:#6b727a;font-weight:700;">On the cusp (within 4% of 50d MA)</div>'
                     f'<table style="width:100%;border-collapse:collapse;font-size:13px;margin:6px 0 16px;">{cusp_rows}</table>')
    else:
        cusp_html = ""

    html = f"""<!DOCTYPE html><html><body style="font-family:'Inter',-apple-system,sans-serif;color:#111418;background:#f7f8fa;margin:0;padding:24px;">
<div style="max-width:640px;margin:0 auto;background:white;border:1px solid #e3e6ea;border-radius:8px;padding:24px 28px;">
  <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.08em;color:#6b727a;font-weight:700;">crypto-breadth · {cadence.lower()} update</div>
  <h1 style="font-size:22px;margin:4px 0 2px;">Signal update — {as_of}</h1>
  {stale_html}
  <div style="background:#f0f4fc;border-left:3px solid #1351b4;border-radius:4px;padding:11px 14px;margin:8px 0 16px;font-size:14.5px;font-weight:500;">{read}</div>
  <div style="color:#4a5159;font-size:14px;margin-bottom:3px;">Holding now: <strong>{hold_txt}</strong> · gross <strong>{_pct(held_gross)}</strong></div>
  <div style="color:#4a5159;font-size:13px;margin-bottom:16px;">Gate at latest close: target exposure <strong>{_pct(exposure)}</strong> ({tier}) · if rebalanced now: <strong>{tgt_txt}</strong></div>
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.06em;color:#6b727a;font-weight:700;">Indicators — now vs 1 week ago</div>
  <table style="width:100%;border-collapse:collapse;margin:6px 0 2px;font-size:13px;font-variant-numeric:tabular-nums;">
    <tr style="font-size:10px;text-transform:uppercase;color:#8a8a82;"><td></td><td style="text-align:right;padding:0 8px;">now</td><td style="text-align:right;padding:0 8px;">1wk ago</td><td style="text-align:right;padding:0 0 0 8px;">change</td></tr>
    {ind_rows}
  </table>
  {ret7_html}
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.06em;color:#6b727a;font-weight:700;">Gate proximity</div>
  <table style="width:100%;border-collapse:collapse;font-size:13px;margin:6px 0 16px;">
    <tr><td style="padding:4px 0;color:#4a5159;">Next tier up</td><td style="padding:4px 0;text-align:right;font-weight:600;">{gate_up}</td></tr>
    <tr><td style="padding:4px 0;color:#4a5159;">Next tier down</td><td style="padding:4px 0;text-align:right;font-weight:600;">{gate_dn}</td></tr>
  </table>
  {breadth_block}
  {cusp_html}
  {dev_block}
  <div style="font-size:12px;text-transform:uppercase;letter-spacing:0.06em;color:#6b727a;font-weight:700;">Changes in the last {window_days} days</div>
  <div style="margin:6px 0 16px;">{changes_html}</div>
  <a href="{DASHBOARD_URL}" style="display:inline-block;background:#1351b4;color:white;padding:10px 18px;border-radius:5px;text-decoration:none;font-weight:600;font-size:13px;">Open dashboard →</a>
  <hr style="border:none;border-top:1px solid #e3e6ea;margin:20px 0 12px;">
  <div style="font-size:11px;color:#6b727a;line-height:1.55;">
    Pipeline last ran {meta.get('generated_at','?')} · data through {meta.get('sample_end','?')} · {prov.get('git_sha','?')}<br>
    <strong>Research monitor — not deployed, not financial advice.</strong> The 2026-07 review rated this a small return-seeking satellite at most (real but modest edge; not a hedge). This says what the signal is doing, not to trade it.
    <a href="{REPO_URL}" style="color:#1351b4;">github.com/phuazz/crypto-breadth</a>
  </div>
</div></body></html>"""
    return subject, plain, html, charts


def maybe_send_digest(dash: dict, cfg: dict, state: dict) -> bool:
    due, label, window = _digest_due(state)
    if not due:
        return False
    cs = {}
    if COIN_SIGNALS_JSON.exists():
        try:
            cs = json.loads(COIN_SIGNALS_JSON.read_text(encoding="utf-8")).get("coins", {})
        except Exception:
            cs = {}
    subject, plain, html, charts = build_digest(dash, window_days=window, cadence=label, coin_signals=cs)
    try:
        send_email(subject, plain, html, cfg, charts=charts)
        state["last_digest_date"] = datetime.now(timezone.utc).date().isoformat()
        print(f"  digest sent: {subject}{f' (+{len(charts)} charts)' if charts else ''}", flush=True)
        return True
    except Exception as e:
        print(f"  digest FAILED: {e!r}", flush=True)
        return False


def main() -> int:
    if not DATA_JSON.exists():
        print(f"  no dashboard data at {DATA_JSON}; run pipeline.py first", flush=True)
        return 0

    dash = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    trades = dash.get("trades", [])
    state = load_state()
    seen = set(state.get("seen", []))

    # Window of interest
    sample_end = dash.get("meta", {}).get("sample_end")
    if not sample_end:
        print("  pipeline output is missing meta.sample_end; aborting", flush=True)
        return 0
    end_dt = datetime.strptime(sample_end, "%Y-%m-%d")
    cutoff_iso = (end_dt.replace(hour=0, minute=0, second=0)).strftime("%Y-%m-%d")

    # Filter to recent and unseen
    candidates = []
    for t in trades:
        d = t["date"]
        # within last ALERT_WINDOW_DAYS
        age = (end_dt - datetime.strptime(d, "%Y-%m-%d")).days
        if age > ALERT_WINDOW_DAYS:
            continue
        if event_key(t) not in seen:
            candidates.append(t)
    candidates.sort(key=lambda e: e["date"])  # oldest first

    print(f"  {len(candidates)} new trade events in last {ALERT_WINDOW_DAYS} days "
          f"(of {len(trades)} total)", flush=True)
    if not candidates:
        # No new trade events — but still send the regular digest if it is due,
        # and update last_run_at so we know the cron is alive.
        cfg = _email_cfg()
        if all(cfg[k] for k in ("from", "to", "password")):
            maybe_send_digest(dash, cfg, state)
        state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)
        return 0

    cfg = _email_cfg()
    missing = [k for k in ("from", "to", "password") if not cfg[k]]
    if missing:
        print(f"  SMTP credentials missing: {missing}. Marking events as seen "
              f"without sending — set GitHub Secrets to enable alerts.", flush=True)
        for t in candidates:
            seen.add(event_key(t))
        state["seen"] = sorted(seen)
        state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)
        return 0

    # Load per-coin signals so we can render the 1Y chart for each alert.
    coin_signals_doc = {}
    if COIN_SIGNALS_JSON.exists():
        try:
            coin_signals_doc = json.loads(
                COIN_SIGNALS_JSON.read_text(encoding="utf-8")
            ).get("coins", {})
        except Exception as e:
            print(f"  warn: could not load coin_signals.json: {e!r}")

    sent = 0
    failed = 0
    for t in candidates:
        subject, plain, html = format_email(t, dash)
        chart_png = None
        chart_cid = None
        coin_data = coin_signals_doc.get(t["coin"])
        if coin_data:
            try:
                chart_png = render_coin_chart(coin_data, t)
            except Exception as e:
                print(f"  chart render failed for {t['coin']}: {e!r}")
        if chart_png:
            # Unique CID per event so multiple emails in one run do not collide.
            chart_cid = f"chart-{t['coin']}-{t['date']}".replace(":", "-").replace(" ", "")
            img_tag = (
                f'<div style="margin: 10px 0 18px;">'
                f'<img src="cid:{chart_cid}" alt="{t["coin"]} signal chart" '
                f'style="display:block; width:100%; max-width:640px; height:auto; '
                f'border: 1px solid #e3e6ea; border-radius: 6px;" />'
                f'<div style="font-size: 11px; color: #6b727a; margin-top: 6px; '
                f'text-align: center;">3M window. Blue band = held periods. '
                f'Arrows = trade events. The annotated marker is the trade this '
                f'email is about.</div></div>'
            )
            html = html.replace("<!-- {CHART_PLACEHOLDER} -->", img_tag)
        else:
            html = html.replace("<!-- {CHART_PLACEHOLDER} -->", "")
        try:
            send_email(subject, plain, html, cfg,
                       chart_png=chart_png, chart_cid=chart_cid)
            seen.add(event_key(t))
            sent += 1
            print(f"  sent: {subject}{' (with chart)' if chart_png else ''}",
                  flush=True)
        except Exception as e:
            failed += 1
            print(f"  FAILED: {subject}: {e!r}", flush=True)

    if all(cfg[k] for k in ("from", "to", "password")):
        maybe_send_digest(dash, cfg, state)
    state["seen"] = sorted(seen)
    state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)
    print(f"  done: sent={sent}, failed={failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
