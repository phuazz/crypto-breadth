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

import json
import os
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_JSON = PROJECT_ROOT / "data" / "dashboard_data.json"
STATE_FILE = PROJECT_ROOT / "data" / "last_alert_state.json"

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


def send_email(subject: str, plain: str, html: str, cfg: dict) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(cfg["host"], cfg["port"]) as server:
        server.starttls(context=context)
        server.login(cfg["from"], cfg["password"])
        server.sendmail(cfg["from"], [cfg["to"]], msg.as_string())


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
        # Still update last_run_at so we know the cron is alive
        state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(state)
        return 0

    cfg = {
        "from": os.environ.get("EMAIL_FROM"),
        "to": os.environ.get("EMAIL_TO"),
        "password": os.environ.get("EMAIL_PASSWORD"),
        "host": os.environ.get("EMAIL_SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("EMAIL_SMTP_PORT", "587")),
    }
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

    sent = 0
    failed = 0
    for t in candidates:
        subject, plain, html = format_email(t, dash)
        try:
            send_email(subject, plain, html, cfg)
            seen.add(event_key(t))
            sent += 1
            print(f"  sent: {subject}", flush=True)
        except Exception as e:
            failed += 1
            print(f"  FAILED: {subject}: {e!r}", flush=True)

    state["seen"] = sorted(seen)
    state["last_run_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)
    print(f"  done: sent={sent}, failed={failed}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
