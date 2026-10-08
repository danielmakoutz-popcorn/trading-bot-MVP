#!/usr/bin/env python3
"""
AI Boi Dashboard v2

Local-only FastAPI dashboard for viewing and running Bot Boi / AI Boi reports.

Safe rules:
  - Runs local research/report scripts only.
  - Does NOT edit config.yaml.
  - Does NOT place trades.
  - Does NOT restart live Bot Boi.

Run from project root:
  python3 scripts/dashboard.py

Open:
  http://127.0.0.1:8081
"""

from __future__ import annotations

import ast
import csv
import html
import json
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIRS = {
    "daily": PROJECT_ROOT / "reports" / "daily",
    "research": PROJECT_ROOT / "reports" / "research",
    "ai": PROJECT_ROOT / "reports" / "ai",
    "weekly": PROJECT_ROOT / "reports" / "weekly",
    "sandbox": PROJECT_ROOT / "reports" / "sandbox",
    "experiments": PROJECT_ROOT / "experiments",
    "trades": PROJECT_ROOT / "logs" / "trades",
    "learning": PROJECT_ROOT / "logs" / "learning",
    "shadow": PROJECT_ROOT / "logs" / "shadow",
}

ALLOWED_EXTENSIONS = {".md", ".txt", ".csv", ".yaml", ".yml", ".log"}

# Whitelisted commands only. No user-supplied shell commands.
COMMANDS = {
    "daily": {
        "label": "Run Daily Summary",
        "cmd": [sys.executable, "scripts/daily_summary.py"],
        "description": "Builds reports/daily for today.",
    },
    "nightly": {
        "label": "Run Nightly Research",
        "cmd": [sys.executable, "scripts/nightly_research.py"],
        "description": "Builds reports/research for today.",
    },
    "ai_memo": {
        "label": "Run Daily AI Memo",
        "cmd": [sys.executable, "scripts/ai_memo.py"],
        "description": "Builds reports/ai memo for today.",
    },
    "daily_all": {
        "label": "Run Full Daily AI Boi Flow",
        "cmd": [
            sys.executable,
            "-c",
            "import subprocess, sys; cmds=[[sys.executable,'scripts/daily_summary.py'],[sys.executable,'scripts/nightly_research.py'],[sys.executable,'scripts/ai_memo.py']];\nfor c in cmds: print('RUN:', ' '.join(c)); subprocess.run(c, check=True)",
        ],
        "description": "Daily summary + nightly research + AI memo.",
    },
    "weekly": {
        "label": "Run Weekly Research",
        "cmd": [sys.executable, "scripts/weekly_research.py"],
        "description": "Builds weekly research for the current week.",
    },
    "weekly_memo": {
        "label": "Run Weekly AI Memo",
        "cmd": [sys.executable, "scripts/weekly_ai_memo.py"],
        "description": "Builds weekly AI memo for the current week.",
    },
    "weekly_all": {
        "label": "Run Full Weekly AI Boi Flow",
        "cmd": [
            sys.executable,
            "-c",
            "import subprocess, sys; cmds=[[sys.executable,'scripts/weekly_research.py'],[sys.executable,'scripts/weekly_ai_memo.py']];\nfor c in cmds: print('RUN:', ' '.join(c)); subprocess.run(c, check=True)",
        ],
        "description": "Weekly research + weekly AI memo.",
    },
    "sandbox_small": {
        "label": "Run Small Sandbox Test",
        "cmd": [sys.executable, "scripts/strategy_sandbox.py", "--max-symbols", "3", "--min-rows", "100", "--min-trades", "2"],
        "description": "Small sandbox test. Good sanity check before heavy runs.",
    },
    "sandbox_learning": {
        "label": "Run Learning-Only Sandbox",
        "cmd": [sys.executable, "scripts/strategy_sandbox.py", "--source", "learning", "--heavy", "--min-rows", "100", "--min-trades", "3"],
        "description": "Heavy sandbox on actual Bot Boi learning logs only.",
    },
    "sandbox_shadow_limited": {
        "label": "Run Shadow Sandbox, 20 Symbols",
        "cmd": [sys.executable, "scripts/strategy_sandbox.py", "--source", "shadow", "--heavy", "--max-symbols", "20", "--min-rows", "100", "--min-trades", "3"],
        "description": "Heavier shadow test but limited to 20 symbols.",
    },
}

app = FastAPI(title="AI Boi Dashboard v2")

LAST_RUN = {
    "command": None,
    "returncode": None,
    "stdout": "",
    "stderr": "",
    "finished_at": None,
}


def css() -> str:
    return """
    <style>
      :root { color-scheme: dark; }
      body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #0d1117; color: #e6edf3; }
      header { padding: 24px; background: linear-gradient(135deg, #151b23, #1f2937); border-bottom: 1px solid #30363d; }
      h1 { margin: 0; font-size: 32px; }
      h2 { margin-top: 28px; border-bottom: 1px solid #30363d; padding-bottom: 8px; }
      a { color: #7dd3fc; text-decoration: none; }
      a:hover { text-decoration: underline; }
      .wrap { max-width: 1200px; margin: 0 auto; padding: 24px; }
      .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 16px; }
      .card { background: #151b23; border: 1px solid #30363d; border-radius: 16px; padding: 16px; box-shadow: 0 8px 28px rgba(0,0,0,0.25); }
      .muted { color: #9da7b1; font-size: 14px; }
      button { background: #2563eb; color: white; border: none; padding: 10px 12px; border-radius: 10px; cursor: pointer; font-weight: 650; }
      button:hover { background: #1d4ed8; }
      .danger { background: #7f1d1d; }
      .danger:hover { background: #991b1b; }
      .success { color: #86efac; }
      .fail { color: #fca5a5; }
      pre { white-space: pre-wrap; word-wrap: break-word; background: #0b0f14; border: 1px solid #30363d; border-radius: 14px; padding: 16px; overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; }
      th, td { border-bottom: 1px solid #30363d; padding: 8px; text-align: left; }
      .pill { display: inline-block; padding: 3px 8px; border: 1px solid #30363d; border-radius: 999px; color: #cbd5e1; font-size: 12px; }
      .row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
      input, select { background: #0b0f14; color: #e6edf3; border: 1px solid #30363d; border-radius: 8px; padding: 8px; }
      .cards-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; }
      .metric { font-size: 28px; font-weight: 800; margin: 4px 0; }
      .positive { color: #86efac; }
      .negative { color: #fca5a5; }
      .neutral { color: #cbd5e1; }
      .chart-wrap { background: #0b0f14; border: 1px solid #30363d; border-radius: 14px; padding: 12px; overflow-x: auto; }
      .mini-table { font-size: 14px; }
      .right { text-align: right; }
    </style>
    """


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(title)}</title>
  {css()}
</head>
<body>
  <header>
    <div class=\"wrap\">
      <h1>AI Boi Dashboard</h1>
      <div class=\"muted\">Local research cockpit. No trades. No live config edits. Only reports, sandbox, and experiments.</div>
    </div>
  </header>
  <main class=\"wrap\">
    <p><a href=\"/\">Home</a> · <a href=\"/files\">Files</a> · <a href=\"/experiments\">Experiments</a> · <a href=\"/last-run\">Last Run</a></p>
    {body}
  </main>
</body>
</html>"""
    )


def safe_file(category: str, filename: str) -> Path:
    if category not in REPORT_DIRS:
        raise HTTPException(status_code=404, detail="Unknown category")
    base = REPORT_DIRS[category].resolve()
    path = (base / filename).resolve()
    if not str(path).startswith(str(base)):
        raise HTTPException(status_code=403, detail="Invalid path")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if path.suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail="Unsupported file type")
    return path


def list_files(category: str, limit: int = 10) -> list[Path]:
    base = REPORT_DIRS.get(category)
    if base is None or not base.exists():
        return []
    files = [p for p in base.iterdir() if p.is_file() and p.suffix in ALLOWED_EXTENSIONS]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files[:limit]


def latest_link(category: str, label: str) -> str:
    files = list_files(category, 1)
    if not files:
        return f"<div class='muted'>No {html.escape(label)} yet.</div>"
    f = files[0]
    return f"<a href='/view/{category}/{html.escape(f.name)}'>{html.escape(f.name)}</a><div class='muted'>{f.stat().st_size:,} bytes</div>"


def command_card(command_id: str, meta: dict) -> str:
    return f"""
    <div class=\"card\">
      <h3>{html.escape(meta['label'])}</h3>
      <p class=\"muted\">{html.escape(meta['description'])}</p>
      <form method=\"post\" action=\"/run\">
        <input type=\"hidden\" name=\"command_id\" value=\"{html.escape(command_id)}\" />
        <button type=\"submit\">Run</button>
      </form>
    </div>
    """


def parse_extra(raw: str) -> dict:
    if raw is None:
        return {}
    text = str(raw).strip()
    if not text:
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    try:
        value = ast.literal_eval(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def safe_float(value, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def fmt_money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def fmt_pct(value) -> str:
    try:
        if value is None:
            return ""
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return ""


def pnl_class(value: float) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


def load_trades_for_dashboard(limit: int = 2500) -> list[dict]:
    path = PROJECT_ROOT / "logs" / "trades" / "trades.csv"
    if not path.exists() or path.stat().st_size == 0:
        return []

    rows: list[dict] = []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            extra = parse_extra(raw.get("extra", ""))
            ts_raw = raw.get("timestamp", "")
            ts = None
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except Exception:
                pass

            event = str(raw.get("event", "")).upper()
            qty = safe_float(raw.get("qty"))
            price = safe_float(raw.get("price"))
            avg_price = safe_float(extra.get("avg_price"), 0.0)
            pnl_pct = extra.get("pnl_pct")

            realized = 0.0
            if event == "SELL" and avg_price > 0 and qty > 0:
                realized = (price - avg_price) * qty

            rows.append({
                "timestamp_raw": ts_raw,
                "timestamp": ts,
                "symbol": str(raw.get("symbol", "")),
                "event": event,
                "qty": qty,
                "price": price,
                "reason": str(raw.get("reason", "")),
                "pnl_pct": safe_float(pnl_pct, 0.0) if pnl_pct not in (None, "") else None,
                "realized_pnl": realized,
                "avg_price": avg_price,
                "order_id": str(extra.get("order_id", "")),
            })

    rows = rows[-limit:]
    rows.sort(key=lambda r: r["timestamp"] or datetime.min)
    return rows


def make_equity_svg(points: list[tuple[datetime, float]], width: int = 980, height: int = 260) -> str:
    if len(points) < 2:
        return "<p class='muted'>Not enough closed SELL data for an equity curve yet.</p>"

    values = [p[1] for p in points]
    min_v = min(values)
    max_v = max(values)
    if min_v == max_v:
        min_v -= 1
        max_v += 1

    pad_l, pad_r, pad_t, pad_b = 55, 18, 18, 36
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    coords = []
    denom = max(1, len(points) - 1)
    for i, (_ts, value) in enumerate(points):
        x = pad_l + (i / denom) * chart_w
        y = pad_t + (1 - ((value - min_v) / (max_v - min_v))) * chart_h
        coords.append((x, y))

    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    zero_y = None
    if min_v <= 0 <= max_v:
        zero_y = pad_t + (1 - ((0 - min_v) / (max_v - min_v))) * chart_h

    last_x, last_y = coords[-1]
    last_val = values[-1]
    first_ts = points[0][0].strftime("%m-%d %H:%M")
    last_ts = points[-1][0].strftime("%m-%d %H:%M")

    zero_line = f"<line x1='{pad_l}' y1='{zero_y:.1f}' x2='{width-pad_r}' y2='{zero_y:.1f}' stroke='#374151' stroke-dasharray='4 4' />" if zero_y is not None else ""

    return f"""
    <svg viewBox="0 0 {width} {height}" width="100%" height="{height}" role="img" aria-label="Cumulative realized P/L curve">
      <rect x="0" y="0" width="{width}" height="{height}" rx="14" fill="#0b0f14" />
      {zero_line}
      <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{height-pad_b}" stroke="#30363d" />
      <line x1="{pad_l}" y1="{height-pad_b}" x2="{width-pad_r}" y2="{height-pad_b}" stroke="#30363d" />
      <text x="8" y="{pad_t+10}" fill="#9da7b1" font-size="12">{html.escape(fmt_money(max_v))}</text>
      <text x="8" y="{height-pad_b}" fill="#9da7b1" font-size="12">{html.escape(fmt_money(min_v))}</text>
      <polyline points="{poly}" fill="none" stroke="#7dd3fc" stroke-width="3" stroke-linejoin="round" stroke-linecap="round" />
      <circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="5" fill="#86efac" />
      <text x="{pad_l}" y="{height-10}" fill="#9da7b1" font-size="12">{html.escape(first_ts)}</text>
      <text x="{width-145}" y="{height-10}" fill="#9da7b1" font-size="12">{html.escape(last_ts)}</text>
      <text x="{max(pad_l, last_x-120):.1f}" y="{max(18, last_y-10):.1f}" fill="#e6edf3" font-size="13">{html.escape(fmt_money(last_val))}</text>
    </svg>
    """


def trade_dashboard_html() -> str:
    trades = load_trades_for_dashboard()
    if not trades:
        return """
        <h2>Equity + Trades</h2>
        <div class='card'><p class='muted'>No trades.csv data found yet.</p></div>
        """

    today_str = date.today().isoformat()
    today_rows = [r for r in trades if r["timestamp"] and r["timestamp"].date().isoformat() == today_str]
    sell_rows = [r for r in trades if r["event"] == "SELL"]
    today_sells = [r for r in today_rows if r["event"] == "SELL"]
    today_buys = [r for r in today_rows if r["event"] == "BUY"]

    realized_today = sum(r["realized_pnl"] for r in today_sells)
    realized_all = 0.0
    equity_points = []
    for r in sell_rows:
        realized_all += r["realized_pnl"]
        if r["timestamp"]:
            equity_points.append((r["timestamp"], realized_all))

    today_wins = sum(1 for r in today_sells if r["realized_pnl"] > 0)
    today_losses = sum(1 for r in today_sells if r["realized_pnl"] < 0)
    today_win_rate = today_wins / max(1, today_wins + today_losses)

    cards = f"""
    <div class="cards-4">
      <div class="card"><div class="muted">Today Realized P/L</div><div class="metric {pnl_class(realized_today)}">{html.escape(fmt_money(realized_today))}</div></div>
      <div class="card"><div class="muted">Today Buys / Sells</div><div class="metric neutral">{len(today_buys)} / {len(today_sells)}</div></div>
      <div class="card"><div class="muted">Today Closed Win Rate</div><div class="metric neutral">{today_win_rate * 100:.1f}%</div></div>
      <div class="card"><div class="muted">All-Time Realized P/L</div><div class="metric {pnl_class(realized_all)}">{html.escape(fmt_money(realized_all))}</div></div>
    </div>
    """

    svg = make_equity_svg(equity_points)

    recent = list(reversed(trades[-25:]))
    rows = []
    for r in recent:
        ts = r["timestamp"].strftime("%Y-%m-%d %H:%M") if r["timestamp"] else r["timestamp_raw"]
        pnl = r["realized_pnl"] if r["event"] == "SELL" else 0.0
        pnl_text = fmt_money(pnl) if r["event"] == "SELL" else ""
        pnl_pct_text = fmt_pct(r["pnl_pct"]) if r["event"] == "SELL" else ""
        rows.append(
            f"<tr>"
            f"<td>{html.escape(ts)}</td>"
            f"<td>{html.escape(r['symbol'])}</td>"
            f"<td>{html.escape(r['event'])}</td>"
            f"<td class='right'>{r['qty']:.6f}</td>"
            f"<td class='right'>${r['price']:,.2f}</td>"
            f"<td>{html.escape(r['reason'])}</td>"
            f"<td class='right {pnl_class(pnl)}'>{html.escape(pnl_text)}</td>"
            f"<td class='right {pnl_class(pnl)}'>{html.escape(pnl_pct_text)}</td>"
            f"</tr>"
        )

    table = "<table class='mini-table'><tr><th>Time</th><th>Symbol</th><th>Event</th><th class='right'>Qty</th><th class='right'>Price</th><th>Reason</th><th class='right'>P/L</th><th class='right'>P/L %</th></tr>" + "".join(rows) + "</table>"

    return f"""
    <h2>Equity + Trades</h2>
    {cards}
    <div class="card" style="margin-top: 16px;">
      <h3>Cumulative Realized P/L</h3>
      <p class="muted">Built from SELL rows in logs/trades/trades.csv. This does not include open/unrealized positions yet.</p>
      <div class="chart-wrap">{svg}</div>
    </div>
    <div class="card" style="margin-top: 16px;">
      <h3>Recent Trades</h3>
      {table}
    </div>
    """


@app.get("/", response_class=HTMLResponse)
def home() -> HTMLResponse:
    latest_cards = "".join(
        f"""
        <div class=\"card\">
          <h3>{label}</h3>
          {latest_link(category, label)}
        </div>
        """
        for category, label in [
            ("ai", "Latest Daily AI Memo"),
            ("research", "Latest Nightly Research"),
            ("weekly", "Latest Weekly / Weekend Memo"),
            ("sandbox", "Latest Sandbox Output"),
            ("experiments", "Experiment Log"),
            ("trades", "Trades CSV"),
        ]
    )

    daily_commands = "".join(command_card(k, COMMANDS[k]) for k in ["daily_all", "daily", "nightly", "ai_memo"])
    weekly_commands = "".join(command_card(k, COMMANDS[k]) for k in ["weekly_all", "weekly", "weekly_memo"])
    sandbox_commands = "".join(command_card(k, COMMANDS[k]) for k in ["sandbox_small", "sandbox_learning", "sandbox_shadow_limited"])

    last_status = ""
    if LAST_RUN["finished_at"]:
        cls = "success" if LAST_RUN["returncode"] == 0 else "fail"
        last_status = f"<p>Last run: <span class='{cls}'>{html.escape(str(LAST_RUN['command']))}</span> at {html.escape(str(LAST_RUN['finished_at']))}. Return code: {LAST_RUN['returncode']}. <a href='/last-run'>View output</a></p>"

    trade_panel = trade_dashboard_html()

    body = f"""
    {trade_panel}

    {last_status}
    <h2>Latest Reports</h2>
    <div class=\"grid\">{latest_cards}</div>

    <h2>Run Daily Reports</h2>
    <div class=\"grid\">{daily_commands}</div>

    <h2>Run Weekly Reports</h2>
    <div class=\"grid\">{weekly_commands}</div>

    <h2>Run Sandbox</h2>
    <p class=\"muted\">Sandbox can take minutes or longer. Start small before running heavier tests.</p>
    <div class=\"grid\">{sandbox_commands}</div>

    <h2>Log Experiment</h2>
    <div class=\"card\">
      <form method=\"post\" action=\"/experiment/add\">
        <p><input name=\"name\" placeholder=\"Experiment name\" size=\"50\" required /></p>
        <p><input name=\"remove\" placeholder=\"Removed symbols, e.g. NVDA,NFLX\" size=\"50\" /></p>
        <p><input name=\"add\" placeholder=\"Added symbols, e.g. NKE,NOW\" size=\"50\" /></p>
        <p><input name=\"reason\" placeholder=\"Reason\" size=\"80\" /></p>
        <p><input name=\"source_report\" placeholder=\"Source report path\" size=\"80\" /></p>
        <p><input name=\"status\" value=\"active\" /></p>
        <button type=\"submit\">Log Experiment</button>
      </form>
    </div>
    """
    return page("AI Boi Dashboard", body)


@app.get("/files", response_class=HTMLResponse)
def files() -> HTMLResponse:
    sections = []
    for category in REPORT_DIRS:
        files = list_files(category, 50)
        rows = []
        for f in files:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(
                f"<tr><td><a href='/view/{category}/{html.escape(f.name)}'>{html.escape(f.name)}</a></td><td>{f.stat().st_size:,}</td><td>{mtime}</td></tr>"
            )
        table = "<p class='muted'>No files.</p>" if not rows else "<table><tr><th>File</th><th>Bytes</th><th>Modified</th></tr>" + "".join(rows) + "</table>"
        sections.append(f"<h2>{html.escape(category)}</h2>{table}")
    return page("Files", "".join(sections))


@app.get("/view/{category}/{filename}", response_class=HTMLResponse)
def view_file(category: str, filename: str) -> HTMLResponse:
    path = safe_file(category, filename)
    text = path.read_text(encoding="utf-8", errors="replace")
    escaped = html.escape(text)
    body = f"""
    <h2>{html.escape(category)} / {html.escape(filename)}</h2>
    <p class=\"row\"><span class=\"pill\">{path.stat().st_size:,} bytes</span><a href=\"/raw/{category}/{html.escape(filename)}\">Raw</a></p>
    <pre>{escaped}</pre>
    """
    return page(filename, body)


@app.get("/raw/{category}/{filename}")
def raw_file(category: str, filename: str) -> PlainTextResponse:
    path = safe_file(category, filename)
    text = path.read_text(encoding="utf-8", errors="replace")
    return PlainTextResponse(text)


@app.post("/run")
def run_command(command_id: str = Form(...)) -> RedirectResponse:
    if command_id not in COMMANDS:
        raise HTTPException(status_code=400, detail="Unknown command")

    meta = COMMANDS[command_id]
    cmd = meta["cmd"]

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=None,
            check=False,
        )
        LAST_RUN.update({
            "command": meta["label"],
            "returncode": result.returncode,
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as exc:
        LAST_RUN.update({
            "command": meta["label"],
            "returncode": -1,
            "stdout": "",
            "stderr": repr(exc),
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    return RedirectResponse("/last-run", status_code=303)


@app.get("/last-run", response_class=HTMLResponse)
def last_run() -> HTMLResponse:
    body = f"""
    <h2>Last Run</h2>
    <div class=\"card\">
      <p><b>Command:</b> {html.escape(str(LAST_RUN['command']))}</p>
      <p><b>Return code:</b> {html.escape(str(LAST_RUN['returncode']))}</p>
      <p><b>Finished:</b> {html.escape(str(LAST_RUN['finished_at']))}</p>
    </div>
    <h3>STDOUT</h3>
    <pre>{html.escape(LAST_RUN['stdout'])}</pre>
    <h3>STDERR</h3>
    <pre>{html.escape(LAST_RUN['stderr'])}</pre>
    """
    return page("Last Run", body)


@app.post("/experiment/add")
def add_experiment(
    name: str = Form(...),
    remove: str = Form(""),
    add: str = Form(""),
    reason: str = Form(""),
    source_report: str = Form(""),
    status: str = Form("active"),
) -> RedirectResponse:
    script = PROJECT_ROOT / "scripts" / "log_experiment.py"
    if not script.exists():
        raise HTTPException(status_code=404, detail="scripts/log_experiment.py not found")

    cmd = [
        sys.executable,
        "scripts/log_experiment.py",
        "add",
        "--name",
        name,
        "--status",
        status,
        "--remove",
        remove,
        "--add",
        add,
        "--reason",
        reason,
        "--source-report",
        source_report,
        "--start-date",
        date.today().isoformat(),
    ]

    result = subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
    LAST_RUN.update({
        "command": "Log Experiment",
        "returncode": result.returncode,
        "stdout": result.stdout[-12000:],
        "stderr": result.stderr[-12000:],
        "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    return RedirectResponse("/experiments", status_code=303)


@app.get("/experiments", response_class=HTMLResponse)
def experiments() -> HTMLResponse:
    exp_path = PROJECT_ROOT / "experiments" / "experiment_log.csv"
    if not exp_path.exists():
        body = "<h2>Experiments</h2><p class='muted'>No experiment log yet.</p>"
    else:
        text = exp_path.read_text(encoding="utf-8", errors="replace")
        body = f"<h2>Experiments</h2><p><a href='/view/experiments/experiment_log.csv'>Open experiment_log.csv</a></p><pre>{html.escape(text)}</pre>"
    return page("Experiments", body)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("AI_BOI_DASHBOARD_HOST", "127.0.0.1")
    port = int(os.environ.get("AI_BOI_DASHBOARD_PORT", "8081"))
    uvicorn.run(app, host=host, port=port)
