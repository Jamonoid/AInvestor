# AutoInvest - Agent Guidelines

## Proyecto

Bot autonomo de inversion en criptomonedas con IA. Usa Gemini como cerebro para analizar datos del mercado y tomar decisiones de trading automaticas en Binance.

## Stack

- **Lenguaje**: Python 3.11+ (usar `py` como comando, NO `python`)
- **Exchange**: Binance via `ccxt`
- **IA**: Google Gemini API via `google-genai`
- **Analisis Tecnico**: `ta` (technical analysis library)
- **Sentimiento**: VADER + Fear & Greed Index API + RSS feeds
- **Base de Datos**: SQLite via SQLAlchemy
- **Dashboard**: FastAPI + HTML/JS/CSS
- **Scheduler**: APScheduler
- **Logs**: loguru

## Arquitectura

```
AutoInvest/
├── config.py              # Configuracion centralizada (pydantic-settings, carga .env)
├── models.py              # Modelos SQLAlchemy (Trade, Signal, PortfolioSnapshot, MarketDataCache)
├── main.py                # Entry point - loop del bot con APScheduler
├── data/                  # Recopilacion de datos
│   ├── market_data.py     # CCXT (Binance) + CoinGecko API
│   └── sentiment.py       # Fear & Greed Index + RSS feeds + VADER NLP
├── analysis/              # Procesamiento
│   └── technical.py       # Indicadores: RSI, MACD, Bollinger, EMA Cross, Volume, ATR, Stochastic
├── agent/                 # Agente IA (cerebro)
│   ├── brain.py           # TradingBrain - consulta Gemini para decisiones BUY/SELL/HOLD
│   └── prompts.py         # System prompt, analysis template, reflection prompt
├── execution/             # Ejecucion de trades
│   ├── order_manager.py   # Paper trading + live trading via CCXT
│   ├── risk_manager.py    # Reglas de riesgo (max posicion, SL, TP, drawdown, cooldown)
│   └── portfolio.py       # Tracking de posiciones, cash, PnL, snapshots
├── dashboard/             # Dashboard web (FastAPI)
│   ├── app.py
│   └── static/            # HTML/CSS/JS
└── notifications/         # Alertas (Discord, futuro)
```

## Flujo de datos

1. `MarketDataCollector` obtiene OHLCV, tickers, order book de Binance
2. `TechnicalAnalyzer` calcula indicadores y genera senales (score -1 a +1)
3. `SentimentCollector` agrega Fear & Greed + NLP de noticias
4. `TradingBrain` recibe todo y consulta Gemini para decisiones
5. `RiskManager` valida cada decision contra reglas de riesgo (tiene VETO)
6. `OrderManager` ejecuta trades (paper o live)
7. `PortfolioManager` actualiza posiciones, cash, PnL

## Convenciones

- Idioma del codigo: ingles (nombres de clases, funciones, variables)
- Idioma de logs y comentarios: espanol neutro sin acentos
- Modo por defecto: `paper` (simulado)
- Todas las API keys van en `.env` (nunca hardcodeadas)
- Cada trade se registra en SQLite con razonamiento del agente
- El Risk Manager SIEMPRE tiene la ultima palabra

## Pares monitoreados

BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, BNB/USDT, ADA/USDT, AVAX/USDT, DOT/USDT, MATIC/USDT, LINK/USDT

## Intervalos del scheduler

| Tarea | Intervalo |
|---|---|
| Datos de mercado | 5 min |
| Analisis tecnico | 15 min |
| Sentimiento | 30 min |
| Agente IA (decision) | 60 min |
| Check stop-loss/take-profit | 1 min |
| Reporte diario + snapshot | Medianoche |
