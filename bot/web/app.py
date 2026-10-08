from typing import Optional, List
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse
from .static.index_html import HTML as DASH_HTML   # adjust import path if needed
from pydantic import BaseModel
from pathlib import Path

CONTROL_DIR = Path("control")
CONTROL_DIR.mkdir(exist_ok=True)
FORCE_FILE = CONTROL_DIR / "force_flat.txt"

def create_app(eqdb, state, engine=None) -> FastAPI:
    app = FastAPI(title="Trading Bot (Local • Paper)")

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "running": bool(state.get("running")),
            "last_loop_utc": state.get("last_loop_utc"),
            "broker": state.get("broker"),
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        return HTMLResponse(content=DASH_HTML)

    # --- Equity endpoints use eqdb ---
    @app.get("/equity")
    async def equity():
        row = eqdb.latest()   # (ts, equity, cash, positions_value, pnl) or None
        if not row:
            return JSONResponse(None)
        ts, equity, cash, pv, pnl = row
        return JSONResponse({
            "ts": ts,
            "equity": equity,
            "cash": cash,
            "positions_value": pv,
            "pnl": pnl
        })

    @app.get("/equity/history")
    async def equity_history(limit: int = Query(500, ge=1, le=5000)):
        rows = eqdb.recent(limit=limit)
        # rows: [(ts, equity, cash, positions_value, pnl), ...]
        payload = [
            {"ts": ts, "equity": eq, "cash": cash, "positions_value": pv, "pnl": pnl}
            for (ts, eq, cash, pv, pnl) in rows
        ][::-1]  # oldest->newest
        return JSONResponse(payload)

    # --- Optional: keep trades if your engine exposes them ---
    @app.get("/trades")
    async def trades(limit: int = 100):
        if engine and hasattr(engine, "db") and hasattr(engine.db, "fetch_trades"):
            rows = engine.db.fetch_trades(limit=limit)
            return JSONResponse(rows)
        return JSONResponse([])

    # force flat additions
    class ForceFlatReq(BaseModel):
        symbol: str | None = None
        reason: str = "manual"

    @app.post("/force-flat")
    def force_flat(req: ForceFlatReq):
        symbol = req.symbol or "all"
        FORCE_FILE.write_text(f"{symbol} {req.reason}")
        return {"ok": True, "queued": True, "symbol": symbol, "reason": req.reason}

    return app
