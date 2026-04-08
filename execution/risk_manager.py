"""
AutoInvest - Risk Manager
Valida cada decision de trading contra reglas de riesgo estrictas.
El Risk Manager tiene VETO sobre cualquier operacion del agente.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import func

from config import settings
from models import Trade, get_session


@dataclass
class RiskCheck:
    """Resultado de una validacion de riesgo."""

    passed: bool
    rule: str
    message: str
    original_amount: float = 0.0
    adjusted_amount: float = 0.0


@dataclass
class RiskAssessment:
    """Evaluacion completa de riesgo para una operacion propuesta."""

    approved: bool
    checks: list[RiskCheck]
    adjusted_cost: float = 0.0    # costo ajustado por riesgo
    suggested_stop_loss: float = 0.0
    suggested_take_profit: float = 0.0
    reason: str = ""


class RiskManager:
    """
    Gestor de riesgo. Valida cada operacion contra reglas configuradas.

    Reglas:
    1. Maximo % del portfolio por posicion
    2. Stop-loss obligatorio
    3. Maximo trades por dia
    4. Maximo drawdown antes de pausar
    5. Cooldown entre trades del mismo par
    """

    def __init__(self) -> None:
        self._paused = False
        self._pause_reason = ""

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    def pause(self, reason: str) -> None:
        """Pausa el trading."""
        self._paused = True
        self._pause_reason = reason
        logger.warning(f"RIESGO: Trading PAUSADO - {reason}")

    def resume(self) -> None:
        """Reanuda el trading."""
        self._paused = False
        self._pause_reason = ""
        logger.info("Trading REANUDADO")

    # ------------------------------------------------------------------
    # Validacion principal
    # ------------------------------------------------------------------

    def evaluate(
        self,
        symbol: str,
        side: str,             # "buy" | "sell"
        proposed_cost: float,  # costo propuesto en USDT
        current_price: float,
        portfolio_value: float,
        cash_available: float,
        positions: dict[str, Any],  # posiciones abiertas
        atr_percent: float = 2.0,   # ATR % para calibrar stop-loss
    ) -> RiskAssessment:
        """
        Evalua una operacion propuesta contra todas las reglas de riesgo.

        Returns:
            RiskAssessment con aprobacion/rechazo y ajustes
        """
        checks: list[RiskCheck] = []

        # 0. ¿Esta pausado?
        if self._paused:
            return RiskAssessment(
                approved=False,
                checks=[RiskCheck(False, "PAUSED", f"Trading pausado: {self._pause_reason}")],
                reason=f"Trading pausado: {self._pause_reason}",
            )

        # Solo validar compras (ventas siempre permitidas para salir de posiciones)
        if side == "sell":
            return RiskAssessment(
                approved=True,
                checks=[RiskCheck(True, "SELL", "Ventas siempre permitidas")],
                adjusted_cost=proposed_cost,
                reason="Venta aprobada",
            )

        adjusted_cost = proposed_cost

        # 1. Maximo % del portfolio por posicion
        max_cost = portfolio_value * (settings.max_position_percent / 100)
        if proposed_cost > max_cost:
            adjusted_cost = max_cost
            checks.append(RiskCheck(
                passed=True,  # aprobado pero ajustado
                rule="MAX_POSITION",
                message=f"Costo ajustado de ${proposed_cost:.2f} a ${max_cost:.2f} "
                        f"(max {settings.max_position_percent}% del portfolio)",
                original_amount=proposed_cost,
                adjusted_amount=max_cost,
            ))
        else:
            checks.append(RiskCheck(
                passed=True,
                rule="MAX_POSITION",
                message=f"Posicion OK: ${proposed_cost:.2f} / ${max_cost:.2f} max",
            ))

        # 2. ¿Hay suficiente cash?
        if adjusted_cost > cash_available:
            adjusted_cost = cash_available * 0.95  # dejar 5% de margen
            if adjusted_cost < 10:  # minimo $10 para que valga la pena
                checks.append(RiskCheck(
                    passed=False,
                    rule="INSUFFICIENT_CASH",
                    message=f"Cash insuficiente: ${cash_available:.2f} disponible",
                ))
                return RiskAssessment(
                    approved=False,
                    checks=checks,
                    reason="Cash insuficiente",
                )
            checks.append(RiskCheck(
                passed=True,
                rule="CASH_ADJUSTED",
                message=f"Ajustado al cash disponible: ${adjusted_cost:.2f}",
                original_amount=proposed_cost,
                adjusted_amount=adjusted_cost,
            ))

        # 3. Maximo trades diarios
        daily_check = self._check_daily_trades()
        checks.append(daily_check)
        if not daily_check.passed:
            return RiskAssessment(
                approved=False,
                checks=checks,
                reason=daily_check.message,
            )

        # 4. Cooldown del mismo par
        cooldown_check = self._check_cooldown(symbol)
        checks.append(cooldown_check)
        if not cooldown_check.passed:
            return RiskAssessment(
                approved=False,
                checks=checks,
                reason=cooldown_check.message,
            )

        # 5. Drawdown check
        dd_check = self._check_drawdown(portfolio_value)
        checks.append(dd_check)
        if not dd_check.passed:
            self.pause(dd_check.message)
            return RiskAssessment(
                approved=False,
                checks=checks,
                reason=dd_check.message,
            )

        # 6. Calcular stop-loss y take-profit sugeridos
        sl_pct = max(settings.stop_loss_percent, atr_percent * 1.5)
        tp_pct = settings.take_profit_percent

        stop_loss = current_price * (1 - sl_pct / 100)
        take_profit = current_price * (1 + tp_pct / 100)

        all_passed = all(c.passed for c in checks)

        return RiskAssessment(
            approved=all_passed,
            checks=checks,
            adjusted_cost=adjusted_cost,
            suggested_stop_loss=stop_loss,
            suggested_take_profit=take_profit,
            reason="Aprobado" if all_passed else "Rechazado por riesgo",
        )

    # ------------------------------------------------------------------
    # Checks individuales
    # ------------------------------------------------------------------

    def _check_daily_trades(self) -> RiskCheck:
        """Verifica que no se exceda el maximo de trades diarios."""
        session = get_session()
        try:
            today_start = _dt.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            count = (
                session.query(func.count(Trade.id))
                .filter(Trade.timestamp >= today_start, Trade.side == "buy")
                .scalar()
            )
            if count >= settings.max_daily_trades:
                return RiskCheck(
                    passed=False,
                    rule="MAX_DAILY_TRADES",
                    message=f"Limite de trades diarios alcanzado: {count}/{settings.max_daily_trades}",
                )
            return RiskCheck(
                passed=True,
                rule="MAX_DAILY_TRADES",
                message=f"Trades hoy: {count}/{settings.max_daily_trades}",
            )
        finally:
            session.close()

    def _check_cooldown(self, symbol: str) -> RiskCheck:
        """Verifica cooldown entre trades del mismo par."""
        session = get_session()
        try:
            cutoff = _dt.datetime.utcnow() - _dt.timedelta(minutes=settings.trade_cooldown_minutes)
            last_trade = (
                session.query(Trade)
                .filter(Trade.symbol == symbol, Trade.timestamp >= cutoff)
                .order_by(Trade.timestamp.desc())
                .first()
            )
            if last_trade:
                mins_ago = (_dt.datetime.utcnow() - last_trade.timestamp).total_seconds() / 60
                return RiskCheck(
                    passed=False,
                    rule="COOLDOWN",
                    message=f"Cooldown activo para {symbol}: ultimo trade hace {mins_ago:.0f} min "
                            f"(esperar {settings.trade_cooldown_minutes} min)",
                )
            return RiskCheck(
                passed=True,
                rule="COOLDOWN",
                message=f"Sin cooldown activo para {symbol}",
            )
        finally:
            session.close()

    def _check_drawdown(self, current_portfolio_value: float) -> RiskCheck:
        """Verifica que no se haya excedido el drawdown maximo."""
        session = get_session()
        try:
            from models import PortfolioSnapshot

            peak = (
                session.query(func.max(PortfolioSnapshot.total_value_usdt))
                .scalar()
            )
            if peak is None or peak == 0:
                return RiskCheck(True, "DRAWDOWN", "Sin historial de portfolio")

            drawdown_pct = ((peak - current_portfolio_value) / peak) * 100

            if drawdown_pct >= settings.max_drawdown_percent:
                return RiskCheck(
                    passed=False,
                    rule="MAX_DRAWDOWN",
                    message=f"DRAWDOWN CRITICO: {drawdown_pct:.1f}% desde peak de ${peak:.2f} "
                            f"(limite: {settings.max_drawdown_percent}%)",
                )
            return RiskCheck(
                passed=True,
                rule="DRAWDOWN",
                message=f"Drawdown actual: {drawdown_pct:.1f}% (limite: {settings.max_drawdown_percent}%)",
            )
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Stop-loss checker (se llama frecuentemente)
    # ------------------------------------------------------------------

    def check_stop_losses(
        self,
        positions: dict[str, dict],
        current_prices: dict[str, float],
    ) -> list[dict[str, Any]]:
        """
        Revisa todas las posiciones abiertas contra sus stop-loss.

        Returns:
            Lista de posiciones que deben cerrarse.
        """
        to_close: list[dict[str, Any]] = []

        for symbol, pos in positions.items():
            price = current_prices.get(symbol, 0)
            if price == 0:
                continue

            entry_price = pos.get("entry_price", 0)
            stop_loss = pos.get("stop_loss", 0)
            take_profit = pos.get("take_profit", 0)

            if entry_price == 0:
                continue

            pnl_pct = ((price - entry_price) / entry_price) * 100

            # Stop-loss
            if stop_loss > 0 and price <= stop_loss:
                logger.warning(
                    f"STOP-LOSS {symbol}: precio ${price:.2f} <= SL ${stop_loss:.2f} "
                    f"(PnL: {pnl_pct:.2f}%)"
                )
                to_close.append({
                    "symbol": symbol,
                    "reason": "stop_loss",
                    "price": price,
                    "pnl_pct": pnl_pct,
                })

            # Take-profit
            elif take_profit > 0 and price >= take_profit:
                logger.info(
                    f"TAKE-PROFIT {symbol}: precio ${price:.2f} >= TP ${take_profit:.2f} "
                    f"(PnL: {pnl_pct:.2f}%)"
                )
                to_close.append({
                    "symbol": symbol,
                    "reason": "take_profit",
                    "price": price,
                    "pnl_pct": pnl_pct,
                })

        return to_close
