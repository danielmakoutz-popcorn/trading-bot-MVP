# Bot Boi — paper-trading operations prototype

A local **Python / FastAPI / SQLite** automation project with Alpaca paper-mode clients, a multi-symbol engine, a small web dashboard, rotating logs, and background heartbeat and account-snapshot tasks.

The portfolio value is the operational plumbing: configuration, durable records, diagnostic endpoints, bounded log files, and a service template. No profitability, uptime, or production-readiness claim is made.

**Start with the evidence:** [recorded validation and operational runbook](docs/VALIDATION.md). Offline checks passed for SQLite persistence, position upserts, a sizing helper, and configured log rotation. The engine, API, and broker were not started in that validation.

## Implemented and planned

| Area | Current evidence | Status |
| --- | --- | --- |
| Account snapshots | [equity_store.py](equity_store.py) stores timestamp, equity, cash, position value and P&L | Offline persistence check passed with synthetic values |
| Engine records | [bot/core/db.py](bot/core/db.py) stores trades, equity and positions | Offline persistence/upsert checks passed |
| Rotating diagnostics | [bot/core/logs.py](bot/core/logs.py), [logging_setup.py](logging_setup.py) | Configured core-log rotation checked; full-process logging not exercised |
| Web dashboard and API | [bot/web/app.py](bot/web/app.py) | Implemented in source; HTTP behavior not tested here |
| Background automation | [main.py](main.py): heartbeat every 5 seconds; snapshot attempts every 60 seconds | Source inspection; no runtime timing evidence |
| Alpaca paper integration | [alpaca_client.py](alpaca_client.py), [broker](bot/broker/alpaca_broker.py), [data provider](bot/data/alpaca_provider.py) | Source inspection; no order submitted during validation |
| Service supervision | [systemd template](scripts/trading-bot.service) with `Restart=on-failure` | Template present; installation and crash recovery not validated |
| Clean install, replay mode, and CI | See limitations below | Pending |

## Architecture

`main.py` loads configuration and starts the engine plus the web server. The engine consumes market data and uses the broker adapter; its records live in `marketbot.db`. A separate snapshot task writes account observations to `equity.db`, which the API reads. Logs and optional shadow/research output are separate local files.

| Runtime piece | Role | Operational limit |
| --- | --- | --- |
| `TradingEngine` | Per-symbol state, strategy evaluation, order adapter and error logging | External SDK calls are synchronous; an async function does not make those calls nonblocking |
| Heartbeat task | Updates `last_loop_utc` in shared state | Timestamp does not prove a successful engine tick or broker request |
| Snapshot task | Reads account values and saves them to SQLite | Failures are logged; data freshness must also be checked |
| FastAPI thread | Dashboard, health, equity/history and trades | Local API has no authentication; `/force-flat` queues a control-file request |
| SQLite stores | Durable local account and engine records | Separate stores; no distributed database or exactly-once order guarantee |

## Run the verified offline checks

Python 3.12 is sufficient; these checks use only the standard library and do not require broker credentials:

```bash
python3 scripts/validate_local.py
```

The script creates temporary databases and logs, then removes them. It does not execute `main.py`, start the trading engine, or submit orders.

## Development setup for the full prototype

The commands below are the **source-derived startup path**, not a recorded fresh-install success.

```bash
git clone https://github.com/danielmakoutz-popcorn/trading-bot-MVP.git
cd trading-bot-MVP
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.yaml config.yaml
```

The source imports the **`alpaca-py` SDK**, which is currently missing from `requirements.txt`. Install a compatible SDK in the local environment before startup; the tested SDK version has not been recorded or pinned. Requirements alone are not a complete installation specification.

Set **`APCA_API_KEY_ID`** and **`APCA_API_SECRET_KEY`** in the local environment using credentials for a dedicated Alpaca paper account. The runtime uses those names, despite an older docstring referring to `ALPACA_...`. Keep credentials out of YAML and Git.

Review the local `config.yaml`: the supplied example selects Alpaca, binds the web server to `127.0.0.1:8000`, and uses `America/Los_Angeles` with a configured trading window. Starting the full engine can place **paper orders**.

```bash
python main.py --config config.yaml
```

Open `http://127.0.0.1:8000`. Windows virtual-environment activation: `.venv\Scripts\Activate.ps1` in PowerShell.

## API and operations

| Endpoint | Current behavior in source |
| --- | --- |
| `GET /health` | `ok`, `running`, heartbeat timestamp, broker label |
| `GET /equity` | Latest account snapshot or `null` |
| `GET /equity/history?limit=500` | Recent account snapshots |
| `GET /trades?limit=100` | Engine trade records, or an empty list when unavailable |
| `POST /force-flat` | Writes a control file for the engine; not exercised during validation |

The active app is `bot/web/app.py`, constructed by `main.py`; `api_server.py` is an alternate helper rather than the current entry point. `ok: true` is a liveness signal, not a broker or trading-loop readiness check.

For service use, replace the username and paths in `scripts/trading-bot.service`, supply credentials through a protected local environment file referenced by the unit, and verify startup before enabling it. The checked-in unit does not include that credential setup. See the [runbook](docs/VALIDATION.md#runtime-checks-to-execute-locally).

## Current limitations and next improvements

- **Fresh installation:** record and pin the missing Alpaca SDK, then verify a clean paper-account startup.
- **Offline replay:** `CSVProvider` exists in [bot/core/data.py](bot/core/data.py), but the current engine's CSV branch does not import it. Yahoo is not accepted by the current engine selector. Neither mode is a verified quickstart.
- **Readiness:** record successful engine ticks and snapshot freshness independently of the heartbeat.
- **Risk checks:** the offline script checks `position_size()` in isolation. It does not validate engine-wide risk enforcement or order execution.
- **Evidence:** broker integration, HTTP endpoints, restart recovery, and CI remain unvalidated in the recorded checks.

These are explicit gaps and proposed next work, not completed features.
