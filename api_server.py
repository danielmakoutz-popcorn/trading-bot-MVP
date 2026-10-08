# api_server.py
from fastapi import FastAPI, Query
from pydantic import BaseModel
from typing import Optional, List
import threading, uvicorn

class BotState(BaseModel):
    running: bool = False
    last_loop_utc: Optional[str] = None
    broker: Optional[str] = None

def start_api(eqdb, state: BotState, host="127.0.0.1", port=8000):
    app = FastAPI(title="Trading Bot API", version="0.1.0")

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "running": state.running,
            "last_loop_utc": state.last_loop_utc,
            "broker": state.broker
        }

    class EquityPoint(BaseModel):
        ts: str
        equity: float
        cash: Optional[float] = None
        positions_value: Optional[float] = None
        pnl: Optional[float] = None

    @app.get("/equity", response_model=Optional[EquityPoint])
    def get_equity():
        row = eqdb.latest()
        if not row: return None
        ts, equity, cash, pv, pnl = row
        return EquityPoint(ts=ts, equity=equity, cash=cash, positions_value=pv, pnl=pnl)

    @app.get("/equity/history", response_model=List[EquityPoint])
    def history(limit: int = Query(200, ge=1, le=5000)):
        rows = eqdb.recent(limit=limit)
        return [EquityPoint(ts=r[0], equity=r[1], cash=r[2], positions_value=r[3], pnl=r[4]) for r in rows][::-1]

    # Optional: wire this when you have live positions via Alpaca integration
    @app.get("/positions")
    def positions():
        return {"positions": []}

    def run():
        uvicorn.run(app, host=host, port=port, log_level="info")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
