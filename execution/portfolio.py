"""
AutoInvest - Portfolio Manager
Rastrea posiciones abiertas, balance, PnL y snapshots.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any

from loguru import logger

from config import DB_DIR, settings
from models import PortfolioSnapshot, Trade, get_session


# Archivo de persistencia de estado
_STATE_FILE = DB_DIR / "portfolio_state.json"


class PortfolioManager:
    """
    Gestiona el estado del portfolio (paper y live).
    Para paper trading mantiene un estado interno.
    Para live, consulta al exchange.
    """

    def __init__(self, exchange: Any = None) -> None:
        self.exchange = exchange
        self.mode = settings.trading_mode

        # Estado paper trading (defaults)
        self._cash: float = settings.paper_trading_balance
        self._positions: dict[str, dict[str, Any]] = {}  # symbol -> {amount, entry_price, stop_loss, take_profit, highest_price}
        self._initial_balance: float = settings.paper_trading_balance

        # #12 - Intentar cargar estado persistido
        self._load_state()

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
    # Persistencia de estado (#12)
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """Persiste el estado del portfolio a disco."""
        state = {
            "cash": self._cash,
            "initial_balance": self._initial_balance,
            "positions": self._positions,
            "saved_at": _dt.datetime.utcnow().isoformat(),
        }
        try:
            _STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
            logger.debug(f"Estado del portfolio guardado: cash=${self._cash:.2f}, {len(self._positions)} posiciones")
        except Exception as exc:
            logger.error(f"Error guardando estado del portfolio: {exc}")

    def _load_state(self) -> None:
        """Carga el estado del portfolio desde disco."""
        if not _STATE_FILE.exists():
            logger.info("Sin estado previo de portfolio. Usando defaults.")
            return

        try:
            state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            self._cash = float(state.get("cash", settings.paper_trading_balance))
            self._initial_balance = float(state.get("initial_balance", settings.paper_trading_balance))
            self._positions = state.get("positions", {})

            # Asegurar que todas las posiciones tengan highest_price
            for symbol, pos in self._positions.items():
                if "highest_price" not in pos:
                    pos["highest_price"] = pos.get("entry_price", 0)

            logger.info(
                f"Estado del portfolio restaurado: cash=${self._cash:.2f}, "
                f"{len(self._positions)} posiciones abiertas"
            )
            for sym, pos in self._positions.items():
                logger.info(
                    f"  Restaurada: {sym} x{pos['amount']:.6f} @ ${pos['entry_price']:.2f} "
                    f"(SL: ${pos.get('stop_loss', 0):.2f} / TP: ${pos.get('take_profit', 0):.2f})"
                )
        except Exception as exc:
            logger.error(f"Error cargando estado del portfolio: {exc}. Usando defaults.")

    # ------------------------------------------------------------------
    # Operaciones de portfolio (paper)
    # ------------------------------------------------------------------

    def open_position(
        self,
        symbol: str,
        amount: float,
        price: float,
        cost: float,
        fees: float = 0.0,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
    ) -> bool:
        """Abre o incrementa una posicion. Descuenta fees del cash (#1)."""
        total_deduction = cost + fees  # #1 - fees se descuentan del cash
        if total_deduction > self._cash:
            logger.error(f"Cash insuficiente: ${self._cash:.2f} < ${total_deduction:.2f} (costo + fees)")
            return False

        if symbol in self._positions:
            # Promediar entrada
            existing = self._positions[symbol]
            total_amount = existing["amount"] + amount
            total_cost = (existing["entry_price"] * existing["amount"]) + (price * amount)
            avg_price = total_cost / total_amount if total_amount > 0 else price

            # #7 - Recalcular SL/TP basandose en nuevo avg_price
            sl_pct = settings.stop_loss_percent
            tp_pct = settings.take_profit_percent

            # Si ya habia SL, calcular el porcentaje original para mantener proporcion
            if existing.get("stop_loss", 0) > 0 and existing["entry_price"] > 0:
                sl_pct = ((existing["entry_price"] - existing["stop_loss"]) / existing["entry_price"]) * 100

            if existing.get("take_profit", 0) > 0 and existing["entry_price"] > 0:
                tp_pct = ((existing["take_profit"] - existing["entry_price"]) / existing["entry_price"]) * 100

            new_sl = avg_price * (1 - sl_pct / 100)
            new_tp = avg_price * (1 + tp_pct / 100)

            self._positions[symbol] = {
                "amount": total_amount,
                "entry_price": avg_price,
                "stop_loss": new_sl,
                "take_profit": new_tp,
                "highest_price": max(price, existing.get("highest_price", price)),  # #3 - tracking max price
                "opened_at": existing.get("opened_at", _dt.datetime.utcnow().isoformat()),
                "last_updated": _dt.datetime.utcnow().isoformat(),
            }
        else:
            self._positions[symbol] = {
                "amount": amount,
                "entry_price": price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "highest_price": price,  # #3 - tracking max price
                "opened_at": _dt.datetime.utcnow().isoformat(),
                "last_updated": _dt.datetime.utcnow().isoformat(),
            }

        self._cash -= total_deduction
        logger.info(
            f"Posicion abierta: {symbol} x{amount:.6f} @ ${price:.2f} | "
            f"Fees: ${fees:.2f} | Cash restante: ${self._cash:.2f}"
        )

        self._save_state()  # #12 - persistir
        return True

    def close_position(self, symbol: str, price: float, partial_percent: float = 100.0) -> float:
        """
        Cierra total o parcialmente una posicion.

        Args:
            symbol: Par a cerrar
            price: Precio de cierre
            partial_percent: % de la posicion a cerrar (100 = cierre total)

        Returns:
            PnL realizado
        """
        if symbol not in self._positions:
            logger.warning(f"No hay posicion abierta de {symbol}")
            return 0.0

        pos = self._positions[symbol]
        close_ratio = min(partial_percent, 100.0) / 100.0
        close_amount = pos["amount"] * close_ratio
        entry_price = pos["entry_price"]

        # Fees de venta
        proceeds_gross = close_amount * price
        fees = proceeds_gross * (settings.simulated_fees_percent / 100) if self.mode == "paper" else 0.0
        proceeds_net = proceeds_gross - fees

        pnl = (price - entry_price) * close_amount - fees

        if close_ratio >= 1.0:
            # Cierre total
            self._positions.pop(symbol)
        else:
            # Cierre parcial - actualizar posicion restante
            self._positions[symbol]["amount"] = pos["amount"] - close_amount
            self._positions[symbol]["last_updated"] = _dt.datetime.utcnow().isoformat()

        self._cash += proceeds_net

        pnl_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
        close_type = "total" if close_ratio >= 1.0 else f"parcial ({partial_percent:.0f}%)"
        logger.info(
            f"Posicion cerrada ({close_type}): {symbol} | "
            f"Entrada: ${entry_price:.2f} -> Salida: ${price:.2f} | "
            f"PnL: ${pnl:.2f} ({pnl_pct:+.2f}%) | Fees: ${fees:.2f} | "
            f"Cash: ${self._cash:.2f}"
        )

        self._save_state()  # #12 - persistir
        return pnl

    # ------------------------------------------------------------------
    # Trailing stop-loss (#3)
    # ------------------------------------------------------------------

    def update_trailing_stops(self, current_prices: dict[str, float]) -> list[dict[str, Any]]:
        """
        Actualiza el highest_price y trailing SL de todas las posiciones.

        Returns:
            Lista de posiciones donde el SL fue actualizado.
        """
        if not settings.trailing_stop_enabled:
            return []

        updated: list[dict[str, Any]] = []
        trailing_pct = settings.trailing_stop_percent

        for symbol, pos in self._positions.items():
            price = current_prices.get(symbol, 0)
            if price <= 0:
                continue

            highest = pos.get("highest_price", pos["entry_price"])

            # Actualizar highest_price si el precio actual es mayor
            if price > highest:
                old_highest = highest
                pos["highest_price"] = price

                # Calcular nuevo trailing SL
                new_trailing_sl = price * (1 - trailing_pct / 100)
                current_sl = pos.get("stop_loss", 0)

                # Solo mover SL hacia arriba, nunca hacia abajo
                if new_trailing_sl > current_sl:
                    pos["stop_loss"] = new_trailing_sl
                    updated.append({
                        "symbol": symbol,
                        "old_sl": current_sl,
                        "new_sl": new_trailing_sl,
                        "highest_price": price,
                    })
                    logger.info(
                        f"Trailing SL {symbol}: ${current_sl:.2f} -> ${new_trailing_sl:.2f} "
                        f"(precio maximo: ${price:.2f})"
                    )

        if updated:
            self._save_state()  # Persistir cambios

        return updated

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
                "stop_loss": pos.get("stop_loss", 0),
                "take_profit": pos.get("take_profit", 0),
                "highest_price": pos.get("highest_price", entry),
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
                    "highest_price": p.get("highest_price", p["entry_price"]),
                }
                for sym, p in self._positions.items()
            },
        }
