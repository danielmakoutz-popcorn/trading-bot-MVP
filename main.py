import argparse
import asyncio
import threading
import uvicorn
import os
import yaml
from datetime import datetime, timezone
from alpaca_client import make_clients

from bot.core.engine import TradingEngine
from bot.web.app import create_app          # make sure create_app(eqdb, state) is implemented
from bot.core.utils import load_config

from equity_store import EquityDB
from logging_setup import app_logger, trade_logger


def run_uvicorn(app, host, port):
    uvicorn.run(app, host=host, port=port, log_level="info")


def utcnow_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def main():
    # --- args / config ---
    parser = argparse.ArgumentParser(description="Local Trading Bot (Paper)")
    parser.add_argument("--config", default="config.yaml", help="Path to config")
    args = parser.parse_args()
    cfg = load_config(args.config)
    print("CONFIG FILE:", os.path.abspath(args.config))                 # whatever var you use for the path
    print("CFG keys:", list(cfg.keys()))
    print("CFG symbols (top-level):", cfg.get("symbols"))
    print("CFG strategy.symbols:", cfg.get("strategy", {}).get("symbols"))
    print("CFG strategy.symbol:", cfg.get("strategy", {}).get("symbol"))
    # --- engine ---
    engine = TradingEngine(cfg)

    # --- state + equity DB (NEW) ---
    state = {"running": False, "last_loop_utc": None, "broker": "alpaca (paper)"}
    eqdb = EquityDB()

    # --- web app (NEW) ---
    app = create_app(eqdb, state, engine)  # ensure bot/web/app.py accepts (eqdb, state, engine)
    web_cfg  = cfg.get("web", {})
    web_host = web_cfg.get("host", "127.0.0.1")
    web_port = int(web_cfg.get("port", 8000))
    t = threading.Thread(target=run_uvicorn, args=(app, web_host, web_port), daemon=True)
    t.start()
    app_logger.info(f"api started on http://{web_host}:{web_port}")

    from alpaca_client import make_clients
    trading_client, data_client = make_clients()
    # --- background tasks (NEW) ---
    async def heartbeat():
        # keeps /health -> last_loop_utc fresh even if engine loop is busy
        while True:
            state["last_loop_utc"] = utcnow_iso()
            await asyncio.sleep(5)

    async def snapshotter():
        # TODO: replace dummy numbers with real equity/cash/positions once Alpaca is wired
        while True:
            try:
                acct = trading_client.get_account()
                equity = float(acct.equity)
                cash = float(acct.cash)
                positions_value = float(acct.portfolio_value) - cash
                pnl = equity - 100000.0
                eqdb.insert_snapshot(equity, cash, positions_value, pnl)
            except Exception as e:
                app_logger.exception(f"snapshotter error: {e}")
            await asyncio.sleep(60)

    asyncio.create_task(heartbeat())
    asyncio.create_task(snapshotter())

    # --- run trading loop until Ctrl+C ---
    try:
        state["running"] = True
        await engine.start()
    except KeyboardInterrupt:
        app_logger.info("Shutting down…")
    finally:
        state["running"] = False
        await engine.close()


if __name__ == "__main__":
    asyncio.run(main())

