# Bot Boi — validation evidence

Recorded **2026-10-09 UTC / 2026-10-08 Pacific**. Application source inspected at [`5d92a70`](https://github.com/danielmakoutz-popcorn/trading-bot-MVP/commit/5d92a7020de8a7a2e90f917f0410c6efc396cc6e). The validator was added with this documentation update; application code was unchanged.

## Executed: offline components

Environment: Python **3.12.14**, standard library only. The script uses temporary SQLite files, a synthetic symbol, synthetic values, and a temporary log directory. It does not import the engine, initialize Alpaca clients, start HTTP, or use credentials.

```bash
python3 scripts/validate_local.py
```

Actual result: exit code **0**.

```text
PASS source syntax: 38 Python files (including this validator)
PASS new equity store has no snapshot
PASS two synthetic equity snapshots survive closing and reopening SQLite
PASS trade, equity, and updated position survive reopening without a duplicate position
PASS sizing helper handles positive sizing, zero price, and negative allocation
PASS configured rotation retains the active log and two backup files
COMPLETE offline component checks; engine, API, and broker were not started
```

The log check uses a deliberately small 180-byte threshold and two backups to exercise the actual `setup_logging()` configuration. It is a bounded-file behavior check, not a sustained-load or disk-exhaustion test. Closing and reopening SQLite verifies local persistence, not end-to-end broker reconciliation.

## Inspected: implemented runtime pieces

| Area | Source evidence | What remains unverified |
| --- | --- | --- |
| Paper-mode clients | `paper=True` in `alpaca_client.py`; paper default in broker adapter | Authentication, external data and paper-order behavior |
| Background tasks | Separate heartbeat and snapshot tasks in `main.py` | Timing, blocking behavior and failure recovery under real SDK calls |
| Health and data endpoints | `bot/web/app.py` | HTTP responses and data freshness in a running process |
| Error handling | Engine tick exceptions and snapshot exceptions are logged | Behavior under timeouts, rate limits or network loss |
| Service restart | `Restart=on-failure`, `RestartSec=5` in unit template | Unit installation and a measured crash/recovery exercise |

No GitHub Actions workflow or run was present at inspection. The transcript above is a locally executed component check, not a CI badge or full-engine validation.

## Runtime checks to execute locally

This is a **future runbook**, not recorded results. Use a dedicated paper account after resolving the installation/configuration gaps in the README.

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/equity
curl -fsS 'http://127.0.0.1:8000/equity/history?limit=5'
curl -fsS 'http://127.0.0.1:8000/trades?limit=5'
```

Check the heartbeat timestamp, then separately confirm that new account snapshots are being stored and engine ticks are succeeding. A heartbeat can advance without a successful tick; `ok: true` is not proof of broker connectivity.

For the optional systemd deployment, first fill the unit's placeholders and add a protected environment file for the two `APCA_...` variables. Then use `systemctl status trading-bot` and `journalctl -u trading-bot -n 100` to inspect it. The service template alone is not proof of deployment or automatic recovery. Record a controlled process-failure/recovery exercise only on the dedicated paper lab before making that claim.

Keep runtime databases and logs outside Git. Preserve both `equity.db` and the configured engine database using SQLite backups or a stopped process; a copied database while writes are active is not the demonstrated recovery path here.

## Pending evidence

- Complete, pinned dependency specification and clean environment startup.
- HTTP tests, paper-account integration, successful-tick readiness, and snapshot freshness.
- Repaired CSV replay wiring, measured restart recovery, and automated CI checks.

The repository was private at inspection. Documentation changes preserve that visibility; an external reviewer needs access or an approved public extract.
