"""
AutoInvest - Dashboard Web (FastAPI)
API REST + WebSocket para monitoreo en tiempo real.
"""

from __future__ import annotations

import asyncio
import copy
import datetime as _dt
import json
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import func

from config import settings
from models import PortfolioSnapshot, Signal, Trade, get_session, init_db

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------------------
# Estado compartido (se actualiza desde el bot principal)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "bot_running": False,
    "last_update": None,
    "current_prices": {},
    "tickers": [],
    "technical_analysis": {},
    "sentiment": {},
    "portfolio": {},
    "agent_last_decision": {},
    "agent_outlook": "",
    "risk_level": "UNKNOWN",
}

# #2 - Lock para proteger _state de escrituras concurrentes (APScheduler threads + FastAPI async)
_state_lock = threading.Lock()


def update_dashboard_state(key: str, value: Any) -> None:
    """Actualiza el estado del dashboard (llamado desde main.py en threads del scheduler)."""
    with _state_lock:
        _state[key] = value
        _state["last_update"] = _dt.datetime.utcnow().isoformat()


def get_dashboard_state() -> dict[str, Any]:
    with _state_lock:
        return copy.deepcopy(_state)


# ---------------------------------------------------------------------------
# WebSocket connections manager
# ---------------------------------------------------------------------------

class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)

    async def broadcast(self, data: dict) -> None:
        # #2 - Iterar sobre copia para evitar RuntimeError si la lista cambia
        async with self._lock:
            snapshot = self.active.copy()
        dead = []
        for ws in snapshot:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self.active:
                        self.active.remove(ws)


ws_manager = ConnectionManager()


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Dashboard iniciado")
    yield
    logger.info("Dashboard detenido")


app = FastAPI(title="AutoInvest Dashboard", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/status")
async def api_status():
    return {
        "bot_running": _state["bot_running"],
        "mode": settings.trading_mode,
        "last_update": _state["last_update"],
        "symbols": settings.symbols,
        "risk_level": _state.get("risk_level", "UNKNOWN"),
        "agent_outlook": _state.get("agent_outlook", ""),
    }


@app.get("/api/portfolio")
async def api_portfolio():
    return _state.get("portfolio", {
        "total_value": settings.paper_trading_balance,
        "cash": settings.paper_trading_balance,
        "total_pnl": 0,
        "total_pnl_pct": 0,
        "positions": {},
        "num_positions": 0,
    })


@app.get("/api/tickers")
async def api_tickers():
    return _state.get("tickers", [])


@app.get("/api/analysis")
async def api_analysis():
    return _state.get("technical_analysis", {})


@app.get("/api/sentiment")
async def api_sentiment():
    return _state.get("sentiment", {})


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    session = get_session()
    try:
        trades = (
            session.query(Trade)
            .order_by(Trade.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": t.id,
                "timestamp": t.timestamp.isoformat() if t.timestamp else "",
                "symbol": t.symbol,
                "side": t.side,
                "price": t.price,
                "amount": t.amount,
                "cost": t.cost,
                "is_paper": t.is_paper,
                "reason": t.reason,
                "confidence": t.confidence,
                "pnl": t.pnl,
                "status": t.status,
            }
            for t in trades
        ]
    finally:
        session.close()


@app.get("/api/snapshots")
async def api_snapshots(limit: int = 100):
    session = get_session()
    try:
        snaps = (
            session.query(PortfolioSnapshot)
            .order_by(PortfolioSnapshot.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "timestamp": s.timestamp.isoformat() if s.timestamp else "",
                "total_value": s.total_value_usdt,
                "cash": s.cash_usdt,
                "pnl_total": s.pnl_total,
                "pnl_percent": s.pnl_percent,
                "max_drawdown": s.max_drawdown,
            }
            for s in reversed(snaps)
        ]
    finally:
        session.close()


@app.get("/api/agent")
async def api_agent():
    return {
        "last_decision": _state.get("agent_last_decision", {}),
        "outlook": _state.get("agent_outlook", "Sin datos aun"),
        "risk_level": _state.get("risk_level", "UNKNOWN"),
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            # #2 - Leer _state de forma thread-safe
            with _state_lock:
                state_snapshot = {
                    "portfolio": copy.deepcopy(_state.get("portfolio", {})),
                    "tickers": copy.deepcopy(_state.get("tickers", [])),
                    "bot_running": _state["bot_running"],
                    "last_update": _state["last_update"],
                    "risk_level": _state.get("risk_level", "UNKNOWN"),
                }
            await ws.send_json({
                "type": "state_update",
                "data": state_snapshot,
            })
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Standalone runner (para desarrollo)
# ---------------------------------------------------------------------------

def run_dashboard() -> None:
    import uvicorn
    uvicorn.run(app, host=settings.dashboard_host, port=settings.dashboard_port)


if __name__ == "__main__":
    run_dashboard()
