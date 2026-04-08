"""
AutoInvest - Recopilacion de Datos de Mercado
Usa CCXT para datos del exchange (Binance) y CoinGecko para datos complementarios.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from typing import Any

import ccxt
import pandas as pd
import requests
from loguru import logger

from config import settings


class MarketDataCollector:
    """Recopila datos de mercado de Binance (via CCXT) y CoinGecko."""

    # CoinGecko base URL (API publica, no requiere key)
    _CG_BASE = "https://api.coingecko.com/api/v3"

    # Mapeo de simbolos CCXT a IDs de CoinGecko
    _CG_IDS = {
        "BTC/USDT": "bitcoin",
        "ETH/USDT": "ethereum",
        "SOL/USDT": "solana",
        "XRP/USDT": "ripple",
        "BNB/USDT": "binancecoin",
        "ADA/USDT": "cardano",
        "AVAX/USDT": "avalanche-2",
        "DOT/USDT": "polkadot",
        "MATIC/USDT": "matic-network",
        "LINK/USDT": "chainlink",
    }

    def __init__(self) -> None:
        # Inicializar exchange CCXT (Binance)
        # Siempre conectar al exchange real para obtener datos de mercado.
        # Paper trading se simula SOLO en la capa de ordenes (order_manager.py).
        self.exchange = ccxt.binance({
            "apiKey": settings.binance_api_key or None,
            "secret": settings.binance_api_secret or None,
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        })
        self._last_cg_request = 0.0  # rate limit para CoinGecko

    # ------------------------------------------------------------------
    # OHLCV (velas) via CCXT
    # ------------------------------------------------------------------

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        Obtiene velas OHLCV de Binance.

        Retorna DataFrame con columnas:
            timestamp, open, high, low, close, volume
        """
        tf = timeframe or settings.ohlcv_timeframe
        lim = limit or settings.ohlcv_limit

        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe=tf, limit=lim)
        except ccxt.BaseError as exc:
            logger.error(f"Error OHLCV {symbol}: {exc}")
            return pd.DataFrame()

        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        logger.debug(f"OHLCV {symbol} [{tf}]: {len(df)} velas")
        return df

    def fetch_all_ohlcv(self) -> dict[str, pd.DataFrame]:
        """Obtiene OHLCV de todos los pares configurados."""
        result: dict[str, pd.DataFrame] = {}
        for symbol in settings.symbols:
            df = self.fetch_ohlcv(symbol)
            if not df.empty:
                result[symbol] = df
            time.sleep(0.2)  # respetar rate limit
        return result

    # ------------------------------------------------------------------
    # Ticker actual via CCXT
    # ------------------------------------------------------------------

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        """Obtiene el ticker actual (precio, volumen 24h, cambio %)."""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return {
                "symbol": symbol,
                "price": ticker.get("last", 0),
                "bid": ticker.get("bid", 0),
                "ask": ticker.get("ask", 0),
                "volume_24h": ticker.get("quoteVolume", 0),
                "change_24h_pct": ticker.get("percentage", 0),
                "high_24h": ticker.get("high", 0),
                "low_24h": ticker.get("low", 0),
                "timestamp": _dt.datetime.utcnow().isoformat(),
            }
        except ccxt.BaseError as exc:
            logger.error(f"Error ticker {symbol}: {exc}")
            return {}

    def fetch_all_tickers(self) -> list[dict[str, Any]]:
        """Obtiene tickers de todos los pares configurados."""
        tickers = []
        for symbol in settings.symbols:
            t = self.fetch_ticker(symbol)
            if t:
                tickers.append(t)
            time.sleep(0.1)
        return tickers

    # ------------------------------------------------------------------
    # Order Book via CCXT
    # ------------------------------------------------------------------

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        """Obtiene el order book (bids y asks)."""
        try:
            book = self.exchange.fetch_order_book(symbol, limit=limit)
            return {
                "symbol": symbol,
                "bids": book.get("bids", [])[:10],
                "asks": book.get("asks", [])[:10],
                "spread": (book["asks"][0][0] - book["bids"][0][0]) if book.get("asks") and book.get("bids") else 0,
                "timestamp": _dt.datetime.utcnow().isoformat(),
            }
        except (ccxt.BaseError, IndexError) as exc:
            logger.error(f"Error order book {symbol}: {exc}")
            return {}

    # ------------------------------------------------------------------
    # CoinGecko - Datos complementarios
    # ------------------------------------------------------------------

    def _cg_get(self, endpoint: str, params: dict | None = None) -> Any:
        """Request a CoinGecko con rate limiting (30 req/min en free tier)."""
        elapsed = time.time() - self._last_cg_request
        if elapsed < 2.1:  # ~28 req/min para ser seguros
            time.sleep(2.1 - elapsed)

        url = f"{self._CG_BASE}/{endpoint}"
        try:
            resp = requests.get(url, params=params or {}, timeout=15)
            self._last_cg_request = time.time()
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.error(f"Error CoinGecko {endpoint}: {exc}")
            return None

    def fetch_global_market(self) -> dict[str, Any]:
        """Datos globales del mercado crypto (market cap total, dominio BTC, etc)."""
        data = self._cg_get("global")
        if not data or "data" not in data:
            return {}
        gd = data["data"]
        return {
            "total_market_cap_usd": gd.get("total_market_cap", {}).get("usd", 0),
            "total_volume_24h_usd": gd.get("total_volume", {}).get("usd", 0),
            "btc_dominance": gd.get("market_cap_percentage", {}).get("btc", 0),
            "eth_dominance": gd.get("market_cap_percentage", {}).get("eth", 0),
            "active_coins": gd.get("active_cryptocurrencies", 0),
            "market_cap_change_24h_pct": gd.get("market_cap_change_percentage_24h_usd", 0),
        }

    def fetch_coin_info(self, symbol: str) -> dict[str, Any]:
        """Info detallada de una moneda via CoinGecko."""
        cg_id = self._CG_IDS.get(symbol)
        if not cg_id:
            return {}

        data = self._cg_get(f"coins/{cg_id}", {
            "localization": "false",
            "tickers": "false",
            "community_data": "true",
            "developer_data": "false",
        })
        if not data:
            return {}

        md = data.get("market_data", {})
        return {
            "symbol": symbol,
            "name": data.get("name", ""),
            "market_cap_rank": data.get("market_cap_rank", 0),
            "market_cap_usd": md.get("market_cap", {}).get("usd", 0),
            "price_change_7d_pct": md.get("price_change_percentage_7d", 0),
            "price_change_30d_pct": md.get("price_change_percentage_30d", 0),
            "ath_usd": md.get("ath", {}).get("usd", 0),
            "ath_change_pct": md.get("ath_change_percentage", {}).get("usd", 0),
            "circulating_supply": md.get("circulating_supply", 0),
            "total_supply": md.get("total_supply", 0),
        }

    # ------------------------------------------------------------------
    # Resumen consolidado
    # ------------------------------------------------------------------

    def collect_full_snapshot(self) -> dict[str, Any]:
        """
        Recopila un snapshot completo del mercado:
        - Tickers de todos los pares
        - OHLCV de todos los pares
        - Datos globales del mercado
        """
        logger.info("Recopilando snapshot completo del mercado...")

        snapshot = {
            "timestamp": _dt.datetime.utcnow().isoformat(),
            "tickers": self.fetch_all_tickers(),
            "ohlcv": {},
            "global_market": self.fetch_global_market(),
        }

        # OHLCV como diccionario de listas (para serializar)
        all_ohlcv = self.fetch_all_ohlcv()
        for sym, df in all_ohlcv.items():
            snapshot["ohlcv"][sym] = df.reset_index().to_dict(orient="records")

        logger.info(
            f"Snapshot listo: {len(snapshot['tickers'])} tickers, "
            f"{len(snapshot['ohlcv'])} OHLCV"
        )
        return snapshot
