"""
AutoInvest - Configuracion Centralizada
Usa pydantic-settings para cargar variables de entorno desde .env
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Directorio raiz del proyecto
BASE_DIR = Path(__file__).resolve().parent

# Directorio de base de datos
DB_DIR = BASE_DIR / "db"
DB_DIR.mkdir(exist_ok=True)

# Directorio de logs
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


class Settings(BaseSettings):
    """Configuracion global del bot, cargada desde .env"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Binance ---
    binance_api_key: str = ""
    binance_api_secret: str = ""

    # --- Gemini ---
    gemini_api_key: str = ""

    # --- OpenRouter (fallback) ---
    openrouter_api_key: str = ""

    # --- Modo de operacion ---
    trading_mode: Literal["paper", "live"] = "paper"

    # --- Capital paper trading ---
    paper_trading_balance: float = 10_000.0

    # --- Pares a monitorear (contra USDT en Binance) ---
    symbols: list[str] = Field(default_factory=lambda: [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "BNB/USDT",
        "ADA/USDT",
        "AVAX/USDT",
        "DOT/USDT",
        "MATIC/USDT",
        "LINK/USDT",
    ])

    # --- Intervalos de tiempo para el scheduler (en minutos) ---
    market_data_interval: int = 5      # Cada cuantos minutos recopilar datos
    analysis_interval: int = 15        # Cada cuantos minutos analizar
    sentiment_interval: int = 30       # Cada cuantos minutos sentimiento
    agent_interval: int = 60           # Cada cuantos minutos consultar agente IA
    stoploss_check_interval: int = 1   # Cada cuantos minutos revisar stop-loss

    # --- Riesgo ---
    max_position_percent: float = 5.0    # Maximo % del portfolio por posicion
    stop_loss_percent: float = 3.0       # Stop-loss por defecto (%)
    take_profit_percent: float = 8.0     # Take-profit por defecto (%)
    max_daily_trades: int = 10           # Maximo de trades por dia
    max_drawdown_percent: float = 15.0   # Maximo drawdown antes de pausar (%)
    trade_cooldown_minutes: int = 30     # Cooldown entre trades del mismo par

    # --- Trailing Stop ---
    trailing_stop_enabled: bool = True   # Activar trailing stop-loss
    trailing_stop_percent: float = 2.0   # Trailing % desde el maximo alcanzado

    # --- Simulacion (paper trading) ---
    simulated_slippage_percent: float = 0.1  # Slippage simulado (%)
    simulated_fees_percent: float = 0.1      # Fees simulados (%)

    # --- Take-Profit parcial ---
    take_profit_partial_percent: float = 50.0  # % de la posicion a cerrar en primer TP

    # --- Confianza flexible ---
    min_confidence_small: float = 50.0   # Confianza minima para trades chicos (max 2%)
    min_confidence_normal: float = 60.0  # Confianza minima para trades normales

    # --- Liquidez ---
    min_liquidity_ratio: float = 10.0    # Volumen 24h debe ser N veces la orden

    # --- OHLCV ---
    ohlcv_timeframe: str = "1h"          # Timeframe para velas (1m, 5m, 15m, 1h, 4h, 1d)
    ohlcv_limit: int = 200               # Cantidad de velas a obtener

    # --- Dashboard ---
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8888

    # --- Discord (fase posterior) ---
    discord_webhook_url: str = ""

    # --- Database ---
    @property
    def db_url(self) -> str:
        return f"sqlite:///{DB_DIR / 'autoinvest.db'}"

    # --- LLM ---
    openrouter_model: str = "moonshotai/kimi-k2"  # Modelo OpenRouter (principal)
    gemini_model: str = "gemini-2.5-flash"         # Modelo Gemini (fallback)
    llm_temperature: float = 0.3       # Creatividad (0=determinista, 1=creativo). Bajo para trading.
    llm_top_p: float = 0.9             # Nucleus sampling. 0.9 = considera top 90% de tokens.
    llm_top_k: int = 40                # Top-K sampling. 40 = considera los 40 tokens mas probables.
    llm_max_tokens: int = 4096         # Max tokens de respuesta


# Singleton de configuracion
settings = Settings()
