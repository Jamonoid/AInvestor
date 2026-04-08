# AutoInvest

Bot autonomo de inversion en criptomonedas impulsado por IA. Utiliza Google Gemini como cerebro para analizar datos del mercado y tomar decisiones de trading automaticas en Binance.

## Caracteristicas

- **Analisis tecnico**: RSI, MACD, Bollinger Bands, EMA, ATR, Stochastic y mas, calculados automaticamente sobre datos OHLCV.
- **Analisis de sentimiento**: Fear & Greed Index, RSS feeds de noticias crypto y NLP con VADER.
- **Agente IA**: Google Gemini analiza toda la informacion y emite decisiones BUY / SELL / HOLD con razonamiento.
- **Gestion de riesgo**: Stop-loss, take-profit, limites de posicion, drawdown maximo y cooldown entre trades. El Risk Manager tiene poder de veto sobre cualquier decision del agente.
- **Paper trading**: Modo simulado por defecto con $10,000 USD virtuales. Modo live disponible.
- **Dashboard web**: Interfaz en tiempo real con FastAPI y WebSockets para monitorear mercado, posiciones, PnL y decisiones del agente.
- **Registro completo**: Cada trade queda registrado en SQLite con el razonamiento del agente.

## Stack

| Componente | Tecnologia |
|---|---|
| Lenguaje | Python 3.11+ |
| Exchange | Binance via ccxt |
| IA | Google Gemini API (google-genai) |
| Analisis tecnico | ta (technical analysis) |
| Sentimiento | VADER + Fear & Greed Index + RSS |
| Base de datos | SQLite via SQLAlchemy |
| Dashboard | FastAPI + HTML/JS/CSS + WebSockets |
| Scheduler | APScheduler |
| Logs | loguru |

## Estructura del proyecto

```
AutoInvest/
├── main.py                # Entry point - orquestador con APScheduler
├── config.py              # Configuracion centralizada (pydantic-settings)
├── models.py              # Modelos SQLAlchemy
├── data/
│   ├── market_data.py     # OHLCV y tickers via CCXT (Binance)
│   └── sentiment.py       # Fear & Greed + RSS + VADER
├── analysis/
│   └── technical.py       # Indicadores tecnicos
├── agent/
│   ├── brain.py           # TradingBrain - consulta Gemini
│   └── prompts.py         # Prompts del sistema
├── execution/
│   ├── order_manager.py   # Paper y live trading
│   ├── risk_manager.py    # Validacion de riesgo
│   └── portfolio.py       # Posiciones, cash, PnL
├── dashboard/
│   ├── app.py             # FastAPI + WebSocket
│   └── static/            # Frontend
└── notifications/         # Alertas (futuro)
```

## Pares monitoreados

BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, BNB/USDT, ADA/USDT, AVAX/USDT, DOT/USDT, MATIC/USDT, LINK/USDT

## Instalacion

```bash
# Clonar el repositorio
git clone https://github.com/Jamonoid/AInvestor.git
cd AInvestor

# Crear entorno virtual
py -m venv .venv
.venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
copy .env.example .env
# Editar .env con tus API keys
```

## Configuracion

Editar el archivo `.env` con las credenciales necesarias:

```env
BINANCE_API_KEY=tu_api_key
BINANCE_API_SECRET=tu_api_secret
GEMINI_API_KEY=tu_gemini_api_key
TRADING_MODE=paper
```

## Uso

```bash
py main.py
```

El bot inicia en modo paper por defecto, recopila datos, ejecuta analisis y abre el dashboard en `http://127.0.0.1:8888`.

## Intervalos del scheduler

| Tarea | Intervalo |
|---|---|
| Datos de mercado | 5 min |
| Analisis tecnico | 15 min |
| Sentimiento | 30 min |
| Decision del agente IA | 60 min |
| Check stop-loss / take-profit | 1 min |
| Reporte diario + snapshot | Medianoche |

## Licencia

MIT
