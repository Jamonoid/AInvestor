"""
AutoInvest - Test de ciclo completo
Ejecuta una iteracion del pipeline: datos -> analisis -> sentimiento -> agente -> decision
"""

import json
import sys
import time

from loguru import logger

logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")

from config import settings
from models import init_db
from data.market_data import MarketDataCollector
from data.sentiment import SentimentCollector
from analysis.technical import TechnicalAnalyzer
from agent.brain import TradingBrain
from execution.risk_manager import RiskManager
from execution.portfolio import PortfolioManager

print("=" * 60)
print("  AutoInvest - TEST DE CICLO COMPLETO")
print(f"  Modo: {settings.trading_mode}")
print(f"  Balance: ${settings.paper_trading_balance:,.2f}")
print("=" * 60)
print()

# 1. Init DB
init_db()
print("[1/6] DB inicializada")

# 2. Datos de mercado (solo 3 pares para test rapido)
print("\n[2/6] Obteniendo datos de mercado...")
mc = MarketDataCollector()
test_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

tickers = []
for sym in test_symbols:
    t = mc.fetch_ticker(sym)
    if t:
        tickers.append(t)
        print(f"  {sym}: ${t['price']:,.2f} ({t['change_24h_pct']:+.2f}%)")
    time.sleep(0.2)

ohlcv = {}
for sym in test_symbols:
    df = mc.fetch_ohlcv(sym, limit=100)
    if not df.empty:
        ohlcv[sym] = df
        print(f"  {sym}: {len(df)} velas OHLCV")
    time.sleep(0.2)

# 3. Analisis tecnico
print("\n[3/6] Ejecutando analisis tecnico...")
ta = TechnicalAnalyzer()
ta_results = ta.analyze_all(ohlcv)
ta_dicts = {}
for sym, summary in ta_results.items():
    ta_dicts[sym] = summary.to_dict()
    emoji = {
        "buy": "\U0001f7e2",
        "sell": "\U0001f534",
        "neutral": "\u26aa"
    }.get(summary.overall_signal, "\u26aa")
    print(f"  {emoji} {sym}: {summary.overall_signal.upper()} (score: {summary.overall_score:.3f})")
    for sig in summary.signals:
        s_emoji = "\U0001f7e2" if sig.signal == "buy" else "\U0001f534" if sig.signal == "sell" else "\u26aa"
        print(f"    {s_emoji} {sig.name}: {sig.detail}")

# 4. Sentimiento
print("\n[4/6] Analizando sentimiento del mercado...")
sc = SentimentCollector()
sentiment = sc.get_market_sentiment()
print(f"  Fear & Greed: {sentiment['fear_greed']['current_value']} ({sentiment['fear_greed']['current_label']})")
print(f"  Noticias: {sentiment['news_count']} articulos, avg sentiment: {sentiment['news_avg_sentiment']:.3f}")
print(f"  Score consolidado: {sentiment['overall_score']:.3f} ({sentiment['overall_label']})")

# 5. Portfolio
print("\n[5/6] Estado del portfolio...")
pm = PortfolioManager()
prices = {t["symbol"]: t["price"] for t in tickers}
status = pm.get_status_for_agent(prices)
print(f"  Cash: ${status['cash_usdt']:,.2f}")
print(f"  Posiciones abiertas: {status['num_open_positions']}")

# 6. Agente IA
print("\n[6/6] Consultando agente Gemini...")
brain = TradingBrain()
recent_trades = []

response = brain.analyze_and_decide(
    portfolio_status=status,
    technical_analysis=ta_dicts,
    sentiment_data=sentiment,
    tickers=tickers,
    recent_trades=recent_trades,
)

if response.error:
    print(f"  ERROR: {response.error}")
else:
    print(f"  Perspectiva: {response.market_outlook}")
    print(f"  Riesgo: {response.risk_level}")
    print(f"  Decisiones:")
    for d in response.decisions:
        emoji = "\U0001f7e2" if d.action == "BUY" else "\U0001f534" if d.action == "SELL" else "\u26aa"
        print(f"    {emoji} {d.action} {d.symbol} | Confianza: {d.confidence}% | {d.portfolio_percent}% portfolio")
        print(f"      Razon: {d.reasoning}")
        print(f"      SL: {d.stop_loss_pct}% | TP: {d.take_profit_pct}%")

    # Filtrar accionables
    actionable = brain.get_actionable_decisions(response)
    if actionable:
        print(f"\n  Decisiones accionables: {len(actionable)}")
        for d in actionable:
            # Simular validacion del risk manager
            rm = RiskManager()
            portfolio_value = pm.calculate_total_value(prices)
            proposed_cost = portfolio_value * (d.portfolio_percent / 100)
            assessment = rm.evaluate(
                symbol=d.symbol,
                side=d.action.lower(),
                proposed_cost=proposed_cost,
                current_price=prices.get(d.symbol, 0),
                portfolio_value=portfolio_value,
                cash_available=pm.cash,
                positions=pm.positions,
            )
            rm_emoji = "\u2705" if assessment.approved else "\u274c"
            print(f"    {rm_emoji} Risk Manager: {assessment.reason}")
            if assessment.approved:
                print(f"       Costo ajustado: ${assessment.adjusted_cost:.2f}")
                print(f"       SL: ${assessment.suggested_stop_loss:.2f} | TP: ${assessment.suggested_take_profit:.2f}")
    else:
        print(f"\n  Sin decisiones accionables (todo HOLD o confianza baja)")

print("\n" + "=" * 60)
print("  TEST COMPLETADO EXITOSAMENTE")
print("=" * 60)
