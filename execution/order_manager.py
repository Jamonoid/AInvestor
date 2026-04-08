"""
AutoInvest - Order Manager
Ejecuta ordenes de compra/venta (paper o live) y registra en la DB.
"""

from __future__ import annotations

import datetime as _dt
import json
from typing import Any

from loguru import logger

from config import settings
from models import Trade, get_session


class OrderManager:
    """Gestiona la ejecucion de ordenes. Soporta paper y live trading."""

    def __init__(self, exchange: Any = None) -> None:
        """
        Args:
            exchange: instancia de ccxt exchange (para live trading)
        """
        self.exchange = exchange
        self.mode = settings.trading_mode

    # ------------------------------------------------------------------
    # Ejecucion de ordenes
    # ------------------------------------------------------------------

    def execute_buy(
        self,
        symbol: str,
        cost_usdt: float,
        current_price: float,
        reason: str = "",
        confidence: float = 0.0,
    ) -> Trade | None:
        """
        Ejecuta una orden de compra.

        Args:
            symbol: Par (ej: BTC/USDT)
            cost_usdt: Cuanto gastar en USDT
            current_price: Precio actual del activo
            reason: Razonamiento del agente
            confidence: Nivel de confianza (0-100)

        Returns:
            Trade registrado o None si fallo
        """
        if current_price <= 0:
            logger.error(f"Precio invalido para {symbol}: {current_price}")
            return None

        amount = cost_usdt / current_price  # cantidad de crypto a comprar
        fees = cost_usdt * 0.001  # 0.1% fee estimado de Binance

        if self.mode == "paper":
            return self._paper_trade(symbol, "buy", current_price, amount, cost_usdt, fees, reason, confidence)
        else:
            return self._live_trade(symbol, "buy", current_price, amount, cost_usdt, reason, confidence)

    def execute_sell(
        self,
        symbol: str,
        amount: float,
        current_price: float,
        entry_price: float = 0.0,
        reason: str = "",
        confidence: float = 0.0,
    ) -> Trade | None:
        """
        Ejecuta una orden de venta.

        Args:
            symbol: Par (ej: BTC/USDT)
            amount: Cantidad de crypto a vender
            current_price: Precio actual
            entry_price: Precio de entrada (para calcular PnL)
            reason: Razonamiento
            confidence: Nivel de confianza
        """
        if current_price <= 0 or amount <= 0:
            logger.error(f"Parametros invalidos para venta de {symbol}")
            return None

        cost = amount * current_price
        fees = cost * 0.001
        pnl = (current_price - entry_price) * amount if entry_price > 0 else 0

        if self.mode == "paper":
            return self._paper_trade(symbol, "sell", current_price, amount, cost, fees, reason, confidence, pnl)
        else:
            return self._live_trade(symbol, "sell", current_price, amount, cost, reason, confidence)

    # ------------------------------------------------------------------
    # Paper trading
    # ------------------------------------------------------------------

    def _paper_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        amount: float,
        cost: float,
        fees: float,
        reason: str,
        confidence: float,
        pnl: float = 0.0,
    ) -> Trade:
        """Simula un trade sin tocar el exchange."""
        trade = Trade(
            timestamp=_dt.datetime.utcnow(),
            symbol=symbol,
            side=side,
            price=price,
            amount=amount,
            cost=cost,
            order_type="market",
            is_paper=True,
            reason=reason,
            confidence=confidence,
            status="filled",
            pnl=pnl,
            fees=fees,
        )

        session = get_session()
        try:
            session.add(trade)
            session.commit()
            session.refresh(trade)

            emoji = "🟢" if side == "buy" else "🔴"
            logger.info(
                f"{emoji} PAPER {side.upper()} {symbol}: "
                f"{amount:.6f} @ ${price:.2f} = ${cost:.2f} "
                f"{'(PnL: $' + f'{pnl:.2f})' if pnl != 0 else ''}"
            )
            return trade
        except Exception as exc:
            session.rollback()
            logger.error(f"Error guardando paper trade: {exc}")
            return None
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Live trading (via CCXT)
    # ------------------------------------------------------------------

    def _live_trade(
        self,
        symbol: str,
        side: str,
        price: float,
        amount: float,
        cost: float,
        reason: str,
        confidence: float,
    ) -> Trade | None:
        """Ejecuta un trade real en Binance via CCXT."""
        if not self.exchange:
            logger.error("Exchange no configurado para live trading")
            return None

        try:
            if side == "buy":
                order = self.exchange.create_market_buy_order(symbol, amount)
            else:
                order = self.exchange.create_market_sell_order(symbol, amount)

            # Extraer datos de la orden ejecutada
            filled_price = order.get("average", price)
            filled_amount = order.get("filled", amount)
            filled_cost = order.get("cost", cost)
            fees_total = 0.0
            if order.get("fees"):
                fees_total = sum(f.get("cost", 0) for f in order["fees"])

            trade = Trade(
                timestamp=_dt.datetime.utcnow(),
                symbol=symbol,
                side=side,
                price=filled_price,
                amount=filled_amount,
                cost=filled_cost,
                order_type="market",
                is_paper=False,
                reason=reason,
                confidence=confidence,
                status=order.get("status", "filled"),
                fees=fees_total,
            )

            session = get_session()
            try:
                session.add(trade)
                session.commit()
                session.refresh(trade)
                logger.info(
                    f"LIVE {side.upper()} {symbol}: "
                    f"{filled_amount:.6f} @ ${filled_price:.2f} = ${filled_cost:.2f}"
                )
                return trade
            except Exception as exc:
                session.rollback()
                logger.error(f"Error guardando live trade: {exc}")
                return None
            finally:
                session.close()

        except Exception as exc:
            logger.error(f"Error ejecutando orden LIVE {side} {symbol}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retorna los trades mas recientes."""
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
                    "timestamp": t.timestamp.isoformat(),
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
