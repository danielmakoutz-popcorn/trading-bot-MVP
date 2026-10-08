# Trading Bot MVP (Local • Paper Trading)

A compact, production-leaning template you can run **locally**:

- ✅ Paper broker (no real money) with P&L, positions, and equity tracking
- ✅ Rotating logs in `logs/`
- ✅ SQLite snapshots of equity & trades
- ✅ FastAPI endpoints (`/health`, `/equity`, `/trades`) + tiny dashboard
- ✅ Configurable strategy (SMA crossover) + risk sizing
- ✅ Pluggable data providers: Yahoo Finance or CSV

> Default timezone: **America/Los_Angeles**.

---

## Quickstart

```bash
# 1) Create & activate a venv (recommended)
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 2) Install deps
pip install -r requirements.txt

# 3) (Optional) Edit config
nano config.yaml

# 4) Run the bot (engine + web dashboard)
python main.py
```

Open the dashboard: <http://127.0.0.1:8000>

---

## Config
See `config.yaml` for options (symbol, loop interval, SMA windows, risk, etc.).

## Using CSV data instead of Yahoo
Drop a CSV under `data/` with columns: `timestamp, open, high, low, close, volume` (UTC timestamps preferred).
In `config.yaml` set `data_provider.name: csv` and `csv_path: data/yourfile.csv`.

## tmux or systemd (choose one)

**tmux (simple):**
```bash
bash scripts/run_tmux.sh
# attach later with
tmux attach -t trading-bot
```

**systemd (advanced):**
1. Copy `scripts/trading-bot.service` to `/etc/systemd/system/trading-bot.service` and adjust paths/username.
2. `sudo systemctl daemon-reload`
3. `sudo systemctl enable --now trading-bot`
4. Check status: `systemctl status trading-bot`

---

## Endpoints (FastAPI)
- `GET /health` → basic OK
- `GET /equity?limit=500` → equity snapshots (timestamp, equity)
- `GET /trades?limit=100` → recent trades with P&L

---

## Notes
- Yahoo minute data only updates during market hours. For 24/7 experiments, switch to CSV or a crypto-friendly source.
- This is **paper trading** only by design. Routing real orders requires a separate, explicit broker integration.
