"""
AutoInvest - Portfolio Manager
Rastrea posiciones abiertas, balance, PnL y snapshots.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from loguru import logger

from config import settings
from models import PortfolioSnapshot, Trade, get_session


class PortfolioManager:
    """
    Gestiona el estado del portfolio (paper y live).
    Para paper trading mantiene un estado interno.
    Para live, consulta al exchange.
    """

    def __init__(self, exchange: Any = None) -> None:
        self.exchange = exchange
        self.mode = settings.trading_mode

        # Estado paper trading
        self._cash: float = settings.paper_trading_balance
        self._positions: dict[str, dict[str, Any]] = {}  # symbol -> {amount, entry_price, stop_loss, take_profit}
        self._initial_balance: float = settings.paper_trading_balance

    # ------------------------------------------------------------------
    # Propiedades
    # ------------------------------------------------------------------

    @property
    def cash(self) -> float:
        return self._cash

    @property
    def positions(self) -> dict[str, dict[str, Any]]:
        return self._positions.copy()

    def get_position(self, symbol: str) -> dict[str, Any] | None:
        return self._positions.get(symbol)

    def has_position(self, symbol: str) -> bool:
        return symbol in self._positions and self._positions[symbol].get("amount", 0) > 0

    # ------------------------------------------------------------------
    # Operaciones de portfolio (paper)
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        amount: float,
        price: float,
        cost: float,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> bool:
        """Abre o incrementa una posicion."""
        if cost > self._cash:
            logger.error(f"Cash insuficiente: ${self._cash:.2f} < ${cost:.2f}")
            return False

        if symbol in self._positions:
            # Promediar entrada
            existing = self._positions[symbol]
            total_amount = existing["amount"] + amount
            total_cost = (existing["entry_price"] * existing["amount"]) + (price * amount)
            avg_price = total_cost / total_amount if total_amount > 0 else price

            self._positions[symbol] = {
                "amount": total_amount,
                "entry_price": avg_price,
                "stop_loss": stop_loss or existing.get("stop_loss", 0),
                "take_profit": take_profit or existing.get("take_profit", 0),
                "opened_at": existing.get("opened_at", _dt.datetime.utcnow().isoformat()),
                "last_updated": _dt.datetime.utcnow().isoformat(),
            }
        else:
            self._positions[symbol] = {
                "amount": amount,
                "entry_price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "opened_at": _dt.datetime.utcnow().isoformat(),
                "last_updated": _dt.datetime.utcnow().isoformat(),
            }

        self._cash -= cost
        logger.info(
            f"Posicion abierta: {symbol} x{amount:.6f} @ ${price:.2f} | "
            f"Cash restante: ${self._cash:.2f}"
        )
        return True

    def close_position(self, symbol: str, price: float) -> float:
        """
        Cierra completamente una posicion.

        Returns:
            PnL realizado
        """
        if symbol not in self._positions:
            logger.warning(f"No hay posicion abierta de {symbol}")
            return 0.0

        pos = self._positions.pop(symbol)
        amount = pos["amount"]
        entry_price = pos["entry_price"]
        proceeds = amount * price
        pnl = (price - entry_price) * amount

        self._cash += proceeds

        emoji = "✅" if pnl >= 0 else "❌"
        pnl_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        logger.info(
            f"{emoji} Posicion cerrada: {symbol} | "
            f"Entrada: ${entry_price:.2f} → Salida: ${price:.2f} | "
            f"PnL: ${pnl:.2f} ({pnl_pct:+.2f}%) | "
            f"Cash: ${self._cash:.2f}"
        )
        return pnl

    # ------------------------------------------------------------------
    # Calculo de valor total
    # ------------------------------------------------------------------

    def calculate_total_value(self, current_prices: dict[str, float]) -> float:
        """Calcula el valor total del portfolio (cash + posiciones a precio actual)."""
        total = self._cash
        for symbol, pos in self._positions.items():
            price = current_prices.get(symbol, pos["entry_price"])
            total += pos["amount"] * price
        return total

    def calculate_pnl(self, current_prices: dict[str, float]) -> dict[str, Any]:
        """Calcula PnL total y por posicion."""
        total_value = self.calculate_total_value(current_prices)
        total_pnl = total_value - self._initial_balance
        total_pnl_pct = (total_pnl / self._initial_balance) * 100 if self._initial_balance > 0 else 0

        positions_pnl = {}
        for symbol, pos in self._positions.items():
            price = current_prices.get(symbol, pos["entry_price"])
            entry = pos["entry_price"]
            amount = pos["amount"]
            unrealized = (price - entry) * amount
            unrealized_pct = ((price - entry) / entry) * 100 if entry > 0 else 0
            positions_pnl[symbol] = {
                "amount": amount,
                "entry_price": entry,
                "current_price": price,
                "unrealized_pnl": unrealized,
                "unrealized_pnl_pct": unrealized_pct,
                "value": amount * price,
            }

        return {
            "total_value": total_value,
            "cash": self._cash,
            "initial_balance": self._initial_balance,
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl_pct,
            "positions": positions_pnl,
            "num_positions": len(self._positions),
        }

    # ------------------------------------------------------------------
    # Snapshots
    # ------------------------------------------------------------------

    def save_snapshot(self, current_prices: dict[str, float]) -> None:
        """Guarda un snapshot del portfolio en la DB."""
        total_value = self.calculate_total_value(current_prices)
        total_pnl = total_value - self._initial_balance
        total_pnl_pct = (total_pnl / self._initial_balance) * 100 if self._initial_balance > 0 else 0

        # Calcular max drawdown
        session = get_session()
        try:
            from sqlalchemy import func
            peak = session.query(func.max(PortfolioSnapshot.total_value_usdt)).scalar()
            if peak is None:
                peak = self._initial_balance
            max_dd = ((peak - total_value) / peak) * 100 if peak > 0 else 0
            max_dd = max(max_dd, 0)

            snapshot = PortfolioSnapshot(
                timestamp=_dt.datetime.utcnow(),
                total_value_usdt=total_value,
                cash_usdt=self._cash,
                positions_json=json.dumps(self._positions, default=str),
                pnl_total=total_pnl,
                pnl_percent=total_pnl_pct,
                max_drawdown=max_dd,
            )
            session.add(snapshot)
            session.commit()
            logger.debug(
                f"Snapshot guardado: ${total_value:.2f} | "
                f"PnL: ${total_pnl:.2f} ({total_pnl_pct:+.2f}%)"
            )
        except Exception as exc:
            session.rollback()
            logger.error(f"Error guardando snapshot: {exc}")
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Estado para el agente IA
    # ------------------------------------------------------------------

    def get_status_for_agent(self, current_prices: dict[str, float]) -> dict[str, Any]:
        """Genera un resumen del portfolio para el agente IA."""
        pnl_data = self.calculate_pnl(current_prices)
        return {
            "mode": self.mode,
            "total_value_usdt": round(pnl_data["total_value"], 2),
            "cash_usdt": round(self._cash, 2),
            "total_pnl_usdt": round(pnl_data["total_pnl"], 2),
            "total_pnl_percent": round(pnl_data["total_pnl_pct"], 2),
            "num_open_positions": len(self._positions),
            "positions": {
                sym: {
                    "amount": round(p["amount"], 6),
                    "entry_price": round(p["entry_price"], 2),
                    "current_price": round(current_prices.get(sym, p["entry_price"]), 2),
                    "pnl_percent": round(
                        ((current_prices.get(sym, p["entry_price"]) - p["entry_price"]) / p["entry_price"]) * 100
                        if p["entry_price"] > 0 else 0,
                        2,
                    ),
                    "stop_loss": p.get("stop_loss", 0),
                    "take_profit": p.get("take_profit", 0),
                }
                for sym, p in self._positions.items()
            },
        }
