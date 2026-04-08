"""
AutoInvest - Analisis Tecnico
Calcula indicadores sobre datos OHLCV usando la libreria 'ta'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import ta as ta_lib
from loguru import logger


@dataclass
class IndicatorSignal:
    """Senal individual de un indicador."""

    name: str                   # nombre del indicador
    value: float                # valor actual
    signal: str                 # "buy", "sell", "neutral"
    strength: float = 0.0      # -1.0 (fuerte sell) a +1.0 (fuerte buy)
    detail: str = ""            # descripcion legible


@dataclass
class TechnicalSummary:
    """Resumen completo del analisis tecnico de un par."""

    symbol: str
    signals: list[IndicatorSignal] = field(default_factory=list)
    overall_signal: str = "neutral"    # buy | sell | neutral
    overall_score: float = 0.0         # -1.0 a +1.0
    indicators_raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "overall_signal": self.overall_signal,
            "overall_score": round(self.overall_score, 3),
            "signals": [
                {
                    "name": s.name,
                    "value": round(s.value, 4),
                    "signal": s.signal,
                    "strength": round(s.strength, 3),
                    "detail": s.detail,
                }
                for s in self.signals
            ],
        }


class TechnicalAnalyzer:
    """Analiza datos OHLCV y genera senales de trading basadas en indicadores tecnicos."""

    def analyze(self, symbol: str, df: pd.DataFrame) -> TechnicalSummary:
        """
        Ejecuta analisis tecnico completo sobre un DataFrame OHLCV.

        Args:
            symbol: Par de trading (ej: BTC/USDT)
            df: DataFrame con columnas open, high, low, close, volume

        Returns:
            TechnicalSummary con todas las senales
        """
        if df.empty or len(df) < 50:
            logger.warning(f"Datos insuficientes para analisis tecnico de {symbol}: {len(df)} velas")
            return TechnicalSummary(symbol=symbol)

        summary = TechnicalSummary(symbol=symbol)

        # Calcular cada indicador
        summary.signals.append(self._rsi(df))
        summary.signals.append(self._macd(df))
        summary.signals.append(self._bollinger(df))
        summary.signals.append(self._ema_cross(df))
        summary.signals.append(self._volume_analysis(df))
        summary.signals.append(self._atr(df))
        summary.signals.append(self._stochastic(df))

        # Score global: promedio ponderado de las strengths
        weights = {
            "RSI": 1.5,
            "MACD": 2.0,
            "Bollinger Bands": 1.5,
            "EMA Cross": 2.0,
            "Volume": 1.0,
            "ATR": 0.5,
            "Stochastic": 1.0,
        }

        total_weight = 0.0
        weighted_sum = 0.0
        for sig in summary.signals:
            w = weights.get(sig.name, 1.0)
            weighted_sum += sig.strength * w
            total_weight += w

        if total_weight > 0:
            summary.overall_score = weighted_sum / total_weight

        # Determinar senal
        if summary.overall_score > 0.25:
            summary.overall_signal = "buy"
        elif summary.overall_score < -0.25:
            summary.overall_signal = "sell"
        else:
            summary.overall_signal = "neutral"

        logger.info(
            f"TA {symbol}: {summary.overall_signal.upper()} "
            f"(score: {summary.overall_score:.3f})"
        )
        return summary

    # ------------------------------------------------------------------
    # Indicadores individuales (usando libreria 'ta')
    # ------------------------------------------------------------------

    def _rsi(self, df: pd.DataFrame, period: int = 14) -> IndicatorSignal:
        """RSI - Relative Strength Index."""
        rsi_series = ta_lib.momentum.RSIIndicator(close=df["close"], window=period).rsi()
        if rsi_series is None or rsi_series.empty or rsi_series.isna().all():
            return IndicatorSignal("RSI", 50, "neutral", 0.0, "Sin datos")

        current = rsi_series.dropna().iloc[-1]

        if current < 30:
            signal = "buy"
            strength = min((30 - current) / 30, 1.0)
            detail = f"RSI={current:.1f} - SOBREVENTA (fuerte senal de compra)"
        elif current < 40:
            signal = "buy"
            strength = 0.3
            detail = f"RSI={current:.1f} - Zona baja, posible rebote"
        elif current > 70:
            signal = "sell"
            strength = -min((current - 70) / 30, 1.0)
            detail = f"RSI={current:.1f} - SOBRECOMPRA (fuerte senal de venta)"
        elif current > 60:
            signal = "sell"
            strength = -0.3
            detail = f"RSI={current:.1f} - Zona alta, posible correccion"
        else:
            signal = "neutral"
            strength = 0.0
            detail = f"RSI={current:.1f} - Zona neutral"

        return IndicatorSignal("RSI", current, signal, strength, detail)

    def _macd(self, df: pd.DataFrame) -> IndicatorSignal:
        """MACD - Moving Average Convergence Divergence."""
        macd_ind = ta_lib.trend.MACD(close=df["close"], window_slow=26, window_fast=12, window_sign=9)
        macd_line = macd_ind.macd()
        signal_line = macd_ind.macd_signal()
        histogram = macd_ind.macd_diff()

        if histogram is None or histogram.empty or histogram.isna().all():
            return IndicatorSignal("MACD", 0, "neutral", 0.0, "Sin datos")

        hist_clean = histogram.dropna()
        if len(hist_clean) < 2:
            return IndicatorSignal("MACD", 0, "neutral", 0.0, "Datos insuficientes")

        current_hist = hist_clean.iloc[-1]
        prev_hist = hist_clean.iloc[-2]
        current_macd = macd_line.dropna().iloc[-1] if not macd_line.dropna().empty else 0

        if current_hist > 0 and prev_hist <= 0:
            signal = "buy"
            strength = 0.7
            detail = f"MACD cruce alcista (hist: {current_hist:.4f})"
        elif current_hist < 0 and prev_hist >= 0:
            signal = "sell"
            strength = -0.7
            detail = f"MACD cruce bajista (hist: {current_hist:.4f})"
        elif current_hist > 0:
            signal = "buy"
            strength = min(0.5, abs(current_hist) / abs(current_macd) if current_macd != 0 else 0.3)
            detail = f"MACD positivo (hist: {current_hist:.4f})"
        elif current_hist < 0:
            signal = "sell"
            strength = -min(0.5, abs(current_hist) / abs(current_macd) if current_macd != 0 else 0.3)
            detail = f"MACD negativo (hist: {current_hist:.4f})"
        else:
            signal = "neutral"
            strength = 0.0
            detail = "MACD neutral"

        return IndicatorSignal("MACD", current_hist, signal, strength, detail)

    def _bollinger(self, df: pd.DataFrame, period: int = 20, std: float = 2.0) -> IndicatorSignal:
        """Bollinger Bands - Volatilidad y zonas de precio."""
        bb = ta_lib.volatility.BollingerBands(close=df["close"], window=period, window_dev=std)
        upper = bb.bollinger_hband()
        lower = bb.bollinger_lband()

        if upper is None or lower is None or upper.isna().all() or lower.isna().all():
            return IndicatorSignal("Bollinger Bands", 0, "neutral", 0.0, "Sin datos")

        close = df["close"].iloc[-1]
        upper_val = upper.dropna().iloc[-1]
        lower_val = lower.dropna().iloc[-1]

        band_width = upper_val - lower_val
        if band_width == 0:
            return IndicatorSignal("Bollinger Bands", 0, "neutral", 0.0, "Bandas cerradas")

        position = (close - lower_val) / band_width

        if position < 0.1:
            signal = "buy"
            strength = 0.8
            detail = f"Precio DEBAJO de banda inferior ({position:.2%})"
        elif position < 0.3:
            signal = "buy"
            strength = 0.4
            detail = f"Precio cerca de banda inferior ({position:.2%})"
        elif position > 0.9:
            signal = "sell"
            strength = -0.8
            detail = f"Precio ENCIMA de banda superior ({position:.2%})"
        elif position > 0.7:
            signal = "sell"
            strength = -0.4
            detail = f"Precio cerca de banda superior ({position:.2%})"
        else:
            signal = "neutral"
            strength = 0.0
            detail = f"Precio en zona media ({position:.2%})"

        return IndicatorSignal("Bollinger Bands", position, signal, strength, detail)

    def _ema_cross(self, df: pd.DataFrame) -> IndicatorSignal:
        """EMA Cross - Cruce de medias moviles exponenciales (20/50)."""
        ema_fast = ta_lib.trend.EMAIndicator(close=df["close"], window=20).ema_indicator()
        ema_slow = ta_lib.trend.EMAIndicator(close=df["close"], window=50).ema_indicator()

        if ema_fast is None or ema_slow is None or ema_fast.isna().all() or ema_slow.isna().all():
            return IndicatorSignal("EMA Cross", 0, "neutral", 0.0, "Sin datos")

        fast_clean = ema_fast.dropna()
        slow_clean = ema_slow.dropna()

        if len(fast_clean) < 2 or len(slow_clean) < 2:
            return IndicatorSignal("EMA Cross", 0, "neutral", 0.0, "Datos insuficientes")

        fast_now = fast_clean.iloc[-1]
        slow_now = slow_clean.iloc[-1]
        fast_prev = fast_clean.iloc[-2]
        slow_prev = slow_clean.iloc[-2]

        diff_pct = ((fast_now - slow_now) / slow_now) * 100 if slow_now != 0 else 0

        if fast_prev <= slow_prev and fast_now > slow_now:
            signal = "buy"
            strength = 0.9
            detail = f"GOLDEN CROSS - EMA20 cruza sobre EMA50 (diff: {diff_pct:.2f}%)"
        elif fast_prev >= slow_prev and fast_now < slow_now:
            signal = "sell"
            strength = -0.9
            detail = f"DEATH CROSS - EMA20 cruza debajo de EMA50 (diff: {diff_pct:.2f}%)"
        elif fast_now > slow_now:
            signal = "buy"
            strength = min(0.5, abs(diff_pct) / 5)
            detail = f"EMA20 > EMA50 (tendencia alcista, diff: {diff_pct:.2f}%)"
        elif fast_now < slow_now:
            signal = "sell"
            strength = -min(0.5, abs(diff_pct) / 5)
            detail = f"EMA20 < EMA50 (tendencia bajista, diff: {diff_pct:.2f}%)"
        else:
            signal = "neutral"
            strength = 0.0
            detail = "EMAs convergentes"

        return IndicatorSignal("EMA Cross", diff_pct, signal, strength, detail)

    def _volume_analysis(self, df: pd.DataFrame, period: int = 20) -> IndicatorSignal:
        """Analisis de volumen relativo al promedio."""
        if len(df) < period:
            return IndicatorSignal("Volume", 0, "neutral", 0.0, "Sin datos")

        vol_sma = df["volume"].rolling(window=period).mean()
        current_vol = df["volume"].iloc[-1]
        avg_vol = vol_sma.iloc[-1]

        if avg_vol == 0:
            return IndicatorSignal("Volume", 0, "neutral", 0.0, "Volumen cero")

        ratio = current_vol / avg_vol
        price_change = (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2] if len(df) > 1 else 0

        if ratio > 2.0 and price_change > 0:
            signal = "buy"
            strength = 0.6
            detail = f"Volumen {ratio:.1f}x el promedio con precio subiendo"
        elif ratio > 2.0 and price_change < 0:
            signal = "sell"
            strength = -0.6
            detail = f"Volumen {ratio:.1f}x el promedio con precio bajando"
        elif ratio > 1.5 and price_change > 0:
            signal = "buy"
            strength = 0.3
            detail = f"Volumen elevado ({ratio:.1f}x) con tendencia positiva"
        elif ratio > 1.5 and price_change < 0:
            signal = "sell"
            strength = -0.3
            detail = f"Volumen elevado ({ratio:.1f}x) con tendencia negativa"
        else:
            signal = "neutral"
            strength = 0.0
            detail = f"Volumen normal ({ratio:.1f}x promedio)"

        return IndicatorSignal("Volume", ratio, signal, strength, detail)

    def _atr(self, df: pd.DataFrame, period: int = 14) -> IndicatorSignal:
        """ATR - Average True Range (volatilidad)."""
        atr_series = ta_lib.volatility.AverageTrueRange(
            high=df["high"], low=df["low"], close=df["close"], window=period
        ).average_true_range()

        if atr_series is None or atr_series.empty or atr_series.isna().all():
            return IndicatorSignal("ATR", 0, "neutral", 0.0, "Sin datos")

        current_atr = atr_series.dropna().iloc[-1]
        close = df["close"].iloc[-1]
        atr_pct = (current_atr / close) * 100 if close != 0 else 0

        if atr_pct > 5:
            detail = f"ATR={atr_pct:.2f}% - ALTA volatilidad (cuidado con posiciones grandes)"
        elif atr_pct > 2:
            detail = f"ATR={atr_pct:.2f}% - Volatilidad moderada"
        else:
            detail = f"ATR={atr_pct:.2f}% - Baja volatilidad"

        return IndicatorSignal("ATR", atr_pct, "neutral", 0.0, detail)

    def _stochastic(self, df: pd.DataFrame) -> IndicatorSignal:
        """Stochastic Oscillator (%K / %D)."""
        stoch = ta_lib.momentum.StochasticOscillator(
            high=df["high"], low=df["low"], close=df["close"]
        )
        k_series = stoch.stoch()
        d_series = stoch.stoch_signal()

        if k_series is None or k_series.isna().all():
            return IndicatorSignal("Stochastic", 50, "neutral", 0.0, "Sin datos")

        k = k_series.dropna().iloc[-1]
        d = d_series.dropna().iloc[-1] if d_series is not None and not d_series.isna().all() else k

        if k < 20 and d < 20:
            signal = "buy"
            strength = 0.6
            detail = f"Stoch %K={k:.1f} %D={d:.1f} - SOBREVENTA"
        elif k < 30:
            signal = "buy"
            strength = 0.3
            detail = f"Stoch %K={k:.1f} %D={d:.1f} - Zona baja"
        elif k > 80 and d > 80:
            signal = "sell"
            strength = -0.6
            detail = f"Stoch %K={k:.1f} %D={d:.1f} - SOBRECOMPRA"
        elif k > 70:
            signal = "sell"
            strength = -0.3
            detail = f"Stoch %K={k:.1f} %D={d:.1f} - Zona alta"
        else:
            signal = "neutral"
            strength = 0.0
            detail = f"Stoch %K={k:.1f} %D={d:.1f} - Zona neutral"

        return IndicatorSignal("Stochastic", k, signal, strength, detail)

    # ------------------------------------------------------------------
    # Batch
    # ------------------------------------------------------------------

    def analyze_all(self, ohlcv_data: dict[str, pd.DataFrame]) -> dict[str, TechnicalSummary]:
        """Analiza todos los pares y retorna un dict de summaries."""
        results: dict[str, TechnicalSummary] = {}
        for symbol, df in ohlcv_data.items():
            results[symbol] = self.analyze(symbol, df)
        return results
