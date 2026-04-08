"""
AutoInvest - Loop Principal
Entry point del sistema. Orquesta todos los componentes y ejecuta el ciclo de trading.
"""

from __future__ import annotations

import datetime as _dt
import json
import signal
import sys
import time
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger

from config import LOG_DIR, settings
from models import init_db

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logger.remove()  # remover handler por defecto
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add(
    str(LOG_DIR / "bot_{time:YYYY-MM-DD}.log"),
    level="DEBUG",
    rotation="1 day",
    retention="30 days",
    compression="zip",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {module}:{function}:{line} | {message}",
)


class AutoInvestBot:
    """Orquestador principal del bot de trading."""

    def __init__(self) -> None:
        logger.info("=" * 60)
        logger.info("  AutoInvest Bot - Inicializando...")
        logger.info(f"  Modo: {settings.trading_mode.upper()}")
        logger.info(f"  Pares: {', '.join(settings.symbols)}")
        logger.info("=" * 60)

        # Inicializar DB
        init_db()
        logger.info("Base de datos inicializada")

        # Componentes
        from agent.brain import TradingBrain
        from analysis.technical import TechnicalAnalyzer
        from data.market_data import MarketDataCollector
        from data.sentiment import SentimentCollector
        from execution.order_manager import OrderManager
        from execution.portfolio import PortfolioManager
        from execution.risk_manager import RiskManager

        self.market_data = MarketDataCollector()
        self.technical = TechnicalAnalyzer()
        self.sentiment = SentimentCollector()
        self.brain = TradingBrain()
        self.risk_manager = RiskManager()
        self.portfolio = PortfolioManager(exchange=self.market_data.exchange)
        self.order_manager = OrderManager(exchange=self.market_data.exchange)

        # Cache de datos
        self._ohlcv_cache: dict[str, Any] = {}
        self._tickers_cache: list[dict] = []
        self._ta_cache: dict[str, Any] = {}
        self._sentiment_cache: dict[str, Any] = {}
        self._current_prices: dict[str, float] = {}

        # Control
        self._running = True
        self._cycle_count = 0

        # Dashboard state sync
        from dashboard.app import update_dashboard_state
        self._sync_dashboard = update_dashboard_state

        logger.info("Todos los componentes inicializados correctamente")

    # ------------------------------------------------------------------
    # Tareas del scheduler
    # ------------------------------------------------------------------

    def task_fetch_market_data(self) -> None:
        """Tarea: Recopilar datos de mercado (cada 5 min)."""
        try:
            logger.info("--- Recopilando datos de mercado ---")

            # OHLCV
            self._ohlcv_cache = self.market_data.fetch_all_ohlcv()

            # Tickers
            self._tickers_cache = self.market_data.fetch_all_tickers()

            # Actualizar precios actuales
            for t in self._tickers_cache:
                if t.get("symbol") and t.get("price"):
                    self._current_prices[t["symbol"]] = t["price"]

            # Sync dashboard
            self._sync_dashboard("tickers", self._tickers_cache)
            self._sync_dashboard("current_prices", self._current_prices)
            self._sync_dashboard("portfolio", self.portfolio.calculate_pnl(self._current_prices))

            logger.info(
                f"Datos actualizados: {len(self._ohlcv_cache)} OHLCV, "
                f"{len(self._tickers_cache)} tickers"
            )
        except Exception as exc:
            logger.error(f"Error recopilando datos de mercado: {exc}")

    def task_technical_analysis(self) -> None:
        """Tarea: Ejecutar analisis tecnico (cada 15 min)."""
        try:
            if not self._ohlcv_cache:
                logger.warning("Sin datos OHLCV. Saltando analisis tecnico.")
                return

            logger.info("--- Ejecutando analisis tecnico ---")
            import pandas as pd

            # Convertir cache a DataFrames si es necesario
            ohlcv_dfs = {}
            for symbol, data in self._ohlcv_cache.items():
                if isinstance(data, pd.DataFrame):
                    ohlcv_dfs[symbol] = data
                elif isinstance(data, list):
                    df = pd.DataFrame(data)
                    if "timestamp" in df.columns:
                        df.set_index("timestamp", inplace=True)
                    ohlcv_dfs[symbol] = df

            results = self.technical.analyze_all(ohlcv_dfs)
            self._ta_cache = {sym: s.to_dict() for sym, s in results.items()}

            # Sync dashboard
            self._sync_dashboard("technical_analysis", self._ta_cache)

            for sym, s in results.items():
                emoji = "🟢" if s.overall_signal == "buy" else "🔴" if s.overall_signal == "sell" else "⚪"
                logger.info(f"  {emoji} {sym}: {s.overall_signal.upper()} (score: {s.overall_score:.3f})")

        except Exception as exc:
            logger.error(f"Error en analisis tecnico: {exc}")

    def task_sentiment_analysis(self) -> None:
        """Tarea: Analisis de sentimiento (cada 30 min)."""
        try:
            logger.info("--- Analizando sentimiento del mercado ---")
            self._sentiment_cache = self.sentiment.get_market_sentiment()
            self._sync_dashboard("sentiment", self._sentiment_cache)
        except Exception as exc:
            logger.error(f"Error en analisis de sentimiento: {exc}")

    def task_agent_decision(self) -> None:
        """Tarea: Consultar al agente IA para decisiones (cada 60 min)."""
        try:
            if not self._ta_cache or not self._tickers_cache:
                logger.warning("Datos insuficientes para el agente. Saltando.")
                return

            logger.info("--- Consultando agente IA ---")

            # Preparar datos para el agente
            portfolio_status = self.portfolio.get_status_for_agent(self._current_prices)
            recent_trades = self.order_manager.get_recent_trades(limit=10)

            # Consultar al agente
            response = self.brain.analyze_and_decide(
                portfolio_status=portfolio_status,
                technical_analysis=self._ta_cache,
                sentiment_data=self._sentiment_cache,
                tickers=self._tickers_cache,
                recent_trades=recent_trades,
            )

            if response.error:
                logger.error(f"Error del agente: {response.error}")
                return

            logger.info(f"Perspectiva del mercado: {response.market_outlook}")
            logger.info(f"Nivel de riesgo: {response.risk_level}")

            # Sync dashboard
            self._sync_dashboard("agent_outlook", response.market_outlook)
            self._sync_dashboard("risk_level", response.risk_level)
            self._sync_dashboard("agent_last_decision", {
                "decisions": [
                    {
                        "symbol": d.symbol,
                        "action": d.action,
                        "confidence": d.confidence,
                        "reasoning": d.reasoning,
                        "portfolio_percent": d.portfolio_percent,
                    }
                    for d in response.decisions
                ],
            })

            # Procesar decisiones accionables
            actionable = self.brain.get_actionable_decisions(response)

            for decision in actionable:
                self._execute_decision(decision)

        except Exception as exc:
            logger.error(f"Error en decision del agente: {exc}")

    def task_check_stop_losses(self) -> None:
        """Tarea: Verificar stop-loss y take-profit (cada 1 min)."""
        try:
            if not self._current_prices or not self.portfolio.positions:
                return

            to_close = self.risk_manager.check_stop_losses(
                positions=self.portfolio.positions,
                current_prices=self._current_prices,
            )

            for item in to_close:
                symbol = item["symbol"]
                price = item["price"]
                reason = item["reason"]
                pos = self.portfolio.get_position(symbol)

                if pos:
                    logger.warning(f"Cerrando {symbol} por {reason} @ ${price:.2f}")
                    trade = self.order_manager.execute_sell(
                        symbol=symbol,
                        amount=pos["amount"],
                        current_price=price,
                        entry_price=pos["entry_price"],
                        reason=f"Auto {reason}: PnL {item['pnl_pct']:.2f}%",
                    )
                    if trade:
                        self.portfolio.close_position(symbol, price)

        except Exception as exc:
            logger.error(f"Error verificando stop-losses: {exc}")

    def task_daily_report(self) -> None:
        """Tarea: Reporte diario y snapshot (cada 24 horas)."""
        try:
            logger.info("=" * 60)
            logger.info("  REPORTE DIARIO")
            logger.info("=" * 60)

            pnl = self.portfolio.calculate_pnl(self._current_prices)

            logger.info(f"  Valor total: ${pnl['total_value']:.2f}")
            logger.info(f"  Cash: ${pnl['cash']:.2f}")
            logger.info(f"  PnL total: ${pnl['total_pnl']:.2f} ({pnl['total_pnl_pct']:+.2f}%)")
            logger.info(f"  Posiciones abiertas: {pnl['num_positions']}")

            for sym, p in pnl.get("positions", {}).items():
                emoji = "✅" if p["unrealized_pnl"] >= 0 else "❌"
                logger.info(
                    f"    {emoji} {sym}: ${p['value']:.2f} "
                    f"(PnL: {p['unrealized_pnl_pct']:+.2f}%)"
                )

            # Guardar snapshot
            self.portfolio.save_snapshot(self._current_prices)

            logger.info("=" * 60)

        except Exception as exc:
            logger.error(f"Error generando reporte diario: {exc}")

    # ------------------------------------------------------------------
    # Ejecucion de decisiones
    # ------------------------------------------------------------------

    def _execute_decision(self, decision) -> None:
        """Ejecuta una decision del agente, validandola contra el risk manager."""
        from agent.brain import TradeDecision

        symbol = decision.symbol
        action = decision.action

        if action == "HOLD":
            return

        current_price = self._current_prices.get(symbol, 0)
        if current_price <= 0:
            logger.warning(f"Sin precio actual para {symbol}. Saltando.")
            return

        if action == "BUY":
            # Calcular costo propuesto
            portfolio_value = self.portfolio.calculate_total_value(self._current_prices)
            proposed_cost = portfolio_value * (decision.portfolio_percent / 100)

            # ATR para calibrar SL
            atr_pct = 2.0
            ta = self._ta_cache.get(symbol, {})
            for sig in ta.get("signals", []):
                if sig.get("name") == "ATR":
                    atr_pct = sig.get("value", 2.0)

            # Validar con risk manager
            assessment = self.risk_manager.evaluate(
                symbol=symbol,
                side="buy",
                proposed_cost=proposed_cost,
                current_price=current_price,
                portfolio_value=portfolio_value,
                cash_available=self.portfolio.cash,
                positions=self.portfolio.positions,
                atr_percent=atr_pct,
            )

            if not assessment.approved:
                logger.warning(f"Risk Manager RECHAZO compra de {symbol}: {assessment.reason}")
                for check in assessment.checks:
                    if not check.passed:
                        logger.warning(f"  ❌ {check.rule}: {check.message}")
                return

            # Ejecutar compra
            cost = assessment.adjusted_cost
            trade = self.order_manager.execute_buy(
                symbol=symbol,
                cost_usdt=cost,
                current_price=current_price,
                reason=decision.reasoning,
                confidence=decision.confidence,
            )

            if trade:
                amount = cost / current_price
                self.portfolio.open_position(
                    symbol=symbol,
                    amount=amount,
                    price=current_price,
                    cost=cost,
                    stop_loss=assessment.suggested_stop_loss,
                    take_profit=assessment.suggested_take_profit,
                )

        elif action == "SELL":
            pos = self.portfolio.get_position(symbol)
            if not pos:
                logger.info(f"No hay posicion de {symbol} para vender. Saltando.")
                return

            trade = self.order_manager.execute_sell(
                symbol=symbol,
                amount=pos["amount"],
                current_price=current_price,
                entry_price=pos["entry_price"],
                reason=decision.reasoning,
                confidence=decision.confidence,
            )

            if trade:
                self.portfolio.close_position(symbol, current_price)

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Inicia el bot con el scheduler + dashboard web."""
        import threading
        import uvicorn
        from dashboard.app import app as dashboard_app, update_dashboard_state

        logger.info("Iniciando AutoInvest Bot...")

        # Marcar bot como activo
        update_dashboard_state("bot_running", True)

        # Ejecutar tareas iniciales
        logger.info("Ejecutando recopilacion inicial de datos...")
        self.task_fetch_market_data()
        self.task_technical_analysis()
        self.task_sentiment_analysis()

        # Snapshot inicial
        self.portfolio.save_snapshot(self._current_prices)

        # Configurar scheduler (Background para que no bloquee)
        scheduler = BackgroundScheduler()

        scheduler.add_job(
            self.task_fetch_market_data,
            "interval",
            minutes=settings.market_data_interval,
            id="market_data",
            name="Recopilar datos de mercado",
        )
        scheduler.add_job(
            self.task_technical_analysis,
            "interval",
            minutes=settings.analysis_interval,
            id="technical_analysis",
            name="Analisis tecnico",
        )
        scheduler.add_job(
            self.task_sentiment_analysis,
            "interval",
            minutes=settings.sentiment_interval,
            id="sentiment_analysis",
            name="Analisis de sentimiento",
        )
        scheduler.add_job(
            self.task_agent_decision,
            "interval",
            minutes=settings.agent_interval,
            id="agent_decision",
            name="Decision del agente IA",
        )
        scheduler.add_job(
            self.task_check_stop_losses,
            "interval",
            minutes=settings.stoploss_check_interval,
            id="stop_loss_check",
            name="Verificar stop-loss",
        )
        scheduler.add_job(
            self.task_daily_report,
            "cron",
            hour=0,
            minute=0,
            id="daily_report",
            name="Reporte diario",
        )

        # Primera ejecucion del agente tras 2 minutos (para tener datos)
        scheduler.add_job(
            self.task_agent_decision,
            "date",
            run_date=_dt.datetime.now() + _dt.timedelta(minutes=2),
            id="agent_first_run",
            name="Primera decision del agente",
        )

        scheduler.start()

        logger.info("")
        logger.info("AutoInvest Bot ACTIVO - corriendo 24/7")
        logger.info(f"   Datos de mercado cada {settings.market_data_interval} min")
        logger.info(f"   Analisis tecnico cada {settings.analysis_interval} min")
        logger.info(f"   Sentimiento cada {settings.sentiment_interval} min")
        logger.info(f"   Agente IA cada {settings.agent_interval} min")
        logger.info(f"   Stop-loss check cada {settings.stoploss_check_interval} min")
        logger.info(f"   Dashboard: http://{settings.dashboard_host}:{settings.dashboard_port}")
        logger.info("")

        # Lanzar dashboard en el hilo principal (uvicorn maneja SIGINT)
        try:
            uvicorn.run(
                dashboard_app,
                host=settings.dashboard_host,
                port=settings.dashboard_port,
                log_level="warning",
            )
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("Apagando bot...")
            update_dashboard_state("bot_running", False)
            self.portfolio.save_snapshot(self._current_prices)
            scheduler.shutdown(wait=False)
            logger.info("AutoInvest Bot apagado correctamente.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    bot = AutoInvestBot()
    bot.run()


if __name__ == "__main__":
    main()
