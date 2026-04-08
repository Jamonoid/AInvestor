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

        if self.mode == "paper":
            # #2 - Slippage simulado: compras ejecutan a precio ligeramente mayor
            slippage_pct = settings.simulated_slippage_percent / 100
            exec_price = current_price * (1 + slippage_pct)

            # #1 - Fees simulados
            fees_pct = settings.simulated_fees_percent / 100
            fees = cost_usdt * fees_pct

            # Cantidad efectiva de crypto (despues de fees y slippage)
            effective_cost = cost_usdt  # lo que se descuenta del cash (sin fees, fees se descuentan aparte)
            amount = effective_cost / exec_price

            return self._paper_trade(
                symbol, "buy", exec_price, amount, effective_cost, fees, reason, confidence,
            )
        else:
            amount = cost_usdt / current_price
            return self._live_trade(
                symbol, "buy", current_price, amount, cost_usdt, reason, confidence,
            )

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

        if self.mode == "paper":
            # #2 - Slippage simulado: ventas ejecutan a precio ligeramente menor
            slippage_pct = settings.simulated_slippage_percent / 100
            exec_price = current_price * (1 - slippage_pct)

            # #1 - Fees simulados
            cost = amount * exec_price
            fees = cost * (settings.simulated_fees_percent / 100)

            # PnL incluyendo fees y slippage
            pnl = (exec_price - entry_price) * amount - fees if entry_price > 0 else 0

            return self._paper_trade(
                symbol, "sell", exec_price, amount, cost, fees, reason, confidence, pnl,
            )
        else:
            cost = amount * current_price
            return self._live_trade(
                symbol, "sell", current_price, amount, cost, reason, confidence,
            )

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

            side_label = "COMPRA" if side == "buy" else "VENTA"
            logger.info(
                f"PAPER {side_label} {symbol}: "
                f"{amount:.6f} @ ${price:.2f} = ${cost:.2f} "
                f"(fees: ${fees:.2f})"
                f"{'  PnL: $' + f'{pnl:.2f}' if pnl != 0 else ''}"
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
        order_type: str = "market",
    ) -> Trade | None:
        """Ejecuta un trade real en Binance via CCXT."""
        if not self.exchange:
            logger.error("Exchange no configurado para live trading")
            return None

        try:
            # #13 - Soporte para limit orders
            if order_type == "limit" and side == "buy":
                limit_price = price * 1.001
                order = self.exchange.create_limit_buy_order(symbol, amount, limit_price)
                logger.info(f"Limit buy order enviada: {symbol} x{amount:.6f} @ ${limit_price:.2f}")
            elif order_type == "limit" and side == "sell":
                limit_price = price * 0.999
                order = self.exchange.create_limit_sell_order(symbol, amount, limit_price)
                logger.info(f"Limit sell order enviada: {symbol} x{amount:.6f} @ ${limit_price:.2f}")
            elif side == "buy":
                order = self.exchange.create_market_buy_order(symbol, amount)
            else:
                order = self.exchange.create_market_sell_order(symbol, amount)

            # #3 - Para limit orders: esperar a que se llene
            if order_type == "limit":
                order = self._wait_for_fill(symbol, order, max_wait_seconds=30)
                if order is None:
                    return None  # orden cancelada o timeout

            # Extraer datos de la orden ejecutada
            filled_amount = order.get("filled", 0) or 0
            filled_price = order.get("average", price) or price
            filled_cost = order.get("cost", 0) or 0

            # #3 - Validar que la orden se lleno antes de crear posicion
            if filled_amount <= 0:
                logger.warning(
                    f"Orden {side} {symbol} no se lleno (filled=0). "
                    f"Status: {order.get('status', 'unknown')}. NO se creara posicion."
                )
                return None
            fees_total = 0.0
            if order.get("fees"):
                fees_total = sum(f.get("cost", 0) for f in order["fees"])
            elif order.get("fee"):
                fees_total = order["fee"].get("cost", 0)

            trade = Trade(
                timestamp=_dt.datetime.utcnow(),
                symbol=symbol,
                side=side,
                price=filled_price,
                amount=filled_amount,
                cost=filled_cost,
                order_type=order_type,
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
                    f"{filled_amount:.6f} @ ${filled_price:.2f} = ${filled_cost:.2f} "
                    f"(fees: ${fees_total:.4f})"
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

    def _wait_for_fill(
        self,
        symbol: str,
        order: dict,
        max_wait_seconds: int = 30,
        poll_interval: float = 2.0,
    ) -> dict | None:
        """
        #3 - Espera activamente a que una limit order se llene.

        Args:
            symbol: Par de trading
            order: Orden retornada por CCXT
            max_wait_seconds: Tiempo maximo de espera
            poll_interval: Intervalo de polling en segundos

        Returns:
            Orden actualizada si se lleno, None si timeout/error
        """
        import time as _time

        order_id = order.get("id")
        if not order_id:
            logger.error("Orden sin ID, no se puede rastrear")
            return None

        elapsed = 0.0
        while elapsed < max_wait_seconds:
            try:
                updated = self.exchange.fetch_order(order_id, symbol)
                status = updated.get("status", "open")
                filled = updated.get("filled", 0) or 0

                if status == "closed" or filled > 0:
                    logger.info(
                        f"Limit order llenada: {symbol} filled={filled:.6f} "
                        f"@ avg=${updated.get('average', 0):.2f}"
                    )
                    return updated
                elif status == "canceled" or status == "cancelled":
                    logger.warning(f"Limit order cancelada externamente: {symbol}")
                    return None

                _time.sleep(poll_interval)
                elapsed += poll_interval

            except Exception as exc:
                logger.error(f"Error verificando limit order {symbol}: {exc}")
                _time.sleep(poll_interval)
                elapsed += poll_interval

        # Timeout: cancelar la orden
        logger.warning(f"Timeout esperando limit order {symbol} ({max_wait_seconds}s). Cancelando...")
        try:
            self.exchange.cancel_order(order_id, symbol)
            logger.info(f"Limit order cancelada: {symbol} #{order_id}")
        except Exception as exc:
            logger.error(f"Error cancelando limit order {symbol}: {exc}")

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
                    "fees": t.fees,
                }
                for t in trades
            ]
        finally:
            session.close()
