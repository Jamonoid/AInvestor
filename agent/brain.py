"""
AutoInvest - Trading Brain (Agente IA)
Usa Google Gemini como LLM principal y OpenRouter como fallback
para analizar datos del mercado y tomar decisiones de trading.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from dataclasses import dataclass, field
from typing import Any

from google import genai
from google.genai import types
from loguru import logger

from agent.prompts import ANALYSIS_PROMPT_TEMPLATE, SYSTEM_PROMPT
from config import settings


@dataclass
class TradeDecision:
    """Decision de trading generada por el agente."""

    symbol: str
    action: str                   # BUY, SELL, HOLD
    confidence: float             # 0-100
    portfolio_percent: float      # % del portfolio a usar
    reasoning: str
    stop_loss_pct: float = 3.0
    take_profit_pct: float = 5.0


@dataclass
class AgentResponse:
    """Respuesta completa del agente IA."""

    decisions: list[TradeDecision] = field(default_factory=list)
    market_outlook: str = ""
    risk_level: str = "MEDIUM"
    raw_response: str = ""
    error: str = ""
    timestamp: str = ""


class TradingBrain:
    """
    El "cerebro" del bot. Usa Gemini para razonar sobre datos del mercado
    y generar decisiones de trading.
    """

    # Modelo de OpenRouter para fallback (rapido y barato)
    OPENROUTER_MODEL = "google/gemini-2.5-flash"

    def __init__(self) -> None:
        # Gemini (principal)
        if not settings.gemini_api_key:
            logger.warning("GEMINI_API_KEY no configurada.")
            self._client = None
        else:
            self._client = genai.Client(api_key=settings.gemini_api_key)

        # OpenRouter (fallback)
        self._openrouter = None
        if settings.openrouter_api_key:
            try:
                from openai import OpenAI
                self._openrouter = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=settings.openrouter_api_key,
                )
                logger.info("OpenRouter configurado como fallback")
            except Exception as exc:
                logger.warning(f"No se pudo inicializar OpenRouter: {exc}")

        if not self._client and not self._openrouter:
            logger.error("Sin Gemini ni OpenRouter. El agente no podra funcionar.")

        self._decision_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Analisis principal
    # ------------------------------------------------------------------

    def analyze_and_decide(
        self,
        portfolio_status: dict[str, Any],
        technical_analysis: dict[str, Any],
        sentiment_data: dict[str, Any],
        tickers: list[dict[str, Any]],
        recent_trades: list[dict[str, Any]],
    ) -> AgentResponse:
        """
        Envia toda la informacion al LLM y obtiene decisiones de trading.

        Args:
            portfolio_status: Estado actual del portfolio
            technical_analysis: Resumen del analisis tecnico por par
            sentiment_data: Datos de sentimiento del mercado
            tickers: Precios actuales de todos los pares
            recent_trades: Ultimos trades ejecutados

        Returns:
            AgentResponse con decisiones
        """
        if not self._client and not self._openrouter:
            return AgentResponse(error="Sin LLM configurado (ni Gemini ni OpenRouter)")

        timestamp = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

        # Formatear analisis tecnico como texto legible
        ta_text = self._format_technical_analysis(technical_analysis)

        # Construir prompt de contexto
        user_prompt = ANALYSIS_PROMPT_TEMPLATE.format(
            timestamp=timestamp,
            portfolio_status=json.dumps(portfolio_status, indent=2, default=str),
            technical_analysis=ta_text,
            sentiment_data=json.dumps(sentiment_data, indent=2, default=str),
            tickers=json.dumps(tickers, indent=2, default=str),
            recent_trades=json.dumps(recent_trades[-10:], indent=2, default=str),
        )

        # --- Intentar con Gemini primero ---
        if self._client:
            result = self._try_gemini(user_prompt, timestamp)
            if result:
                return result

        # --- Fallback: OpenRouter ---
        if self._openrouter:
            result = self._try_openrouter(user_prompt, timestamp)
            if result:
                return result

        return AgentResponse(error="Todos los proveedores LLM fallaron")

    # ------------------------------------------------------------------
    # Proveedores LLM
    # ------------------------------------------------------------------

    def _try_gemini(self, user_prompt: str, timestamp: str) -> AgentResponse | None:
        """Intenta obtener respuesta de Gemini API (principal + fallback lite)."""
        models = [settings.gemini_model, "gemini-2.5-flash-lite"]

        for model_name in models:
            for attempt in range(3):
                try:
                    logger.info(f"Consultando Gemini ({model_name}, intento {attempt + 1})...")
                    response = self._client.models.generate_content(
                        model=model_name,
                        contents=user_prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT,
                            temperature=0.3,
                            max_output_tokens=4096,
                        ),
                    )
                    raw_text = response.text.strip()
                    return self._finalize_response(raw_text, timestamp)

                except Exception as exc:
                    wait = (attempt + 1) * 5
                    logger.warning(f"Error Gemini ({model_name}): {exc}. Reintentando en {wait}s...")
                    time.sleep(wait)

        logger.warning("Gemini agotado. Pasando a OpenRouter...")
        return None

    def _try_openrouter(self, user_prompt: str, timestamp: str) -> AgentResponse | None:
        """Fallback: usa OpenRouter (API compatible con OpenAI)."""
        for attempt in range(3):
            try:
                logger.info(f"Consultando OpenRouter ({self.OPENROUTER_MODEL}, intento {attempt + 1})...")
                response = self._openrouter.chat.completions.create(
                    model=self.OPENROUTER_MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.3,
                    max_tokens=4096,
                )
                raw_text = response.choices[0].message.content.strip()
                logger.info(f"Respuesta de OpenRouter ({len(raw_text)} chars)")
                return self._finalize_response(raw_text, timestamp)

            except Exception as exc:
                wait = (attempt + 1) * 5
                logger.warning(f"Error OpenRouter: {exc}. Reintentando en {wait}s...")
                time.sleep(wait)

        logger.error("OpenRouter tambien agotado.")
        return None

    def _finalize_response(self, raw_text: str, timestamp: str) -> AgentResponse:
        """Parsea y guarda la respuesta del LLM."""
        agent_response = self._parse_response(raw_text)
        agent_response.timestamp = timestamp

        self._decision_history.append({
            "timestamp": timestamp,
            "decisions": [
                {"symbol": d.symbol, "action": d.action, "confidence": d.confidence}
                for d in agent_response.decisions
            ],
            "market_outlook": agent_response.market_outlook,
        })
        self._decision_history = self._decision_history[-50:]

        return agent_response

    # ------------------------------------------------------------------
    # Parsing de respuesta
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> AgentResponse:
        """Parsea la respuesta JSON del LLM."""
        # Limpiar markdown code blocks si existen
        text = raw
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            logger.error(f"Error parseando JSON del agente: {exc}\nRaw: {raw[:500]}")
            return AgentResponse(raw_response=raw, error=f"JSON invalido: {exc}")

        decisions = []
        for d in data.get("decisions", []):
            action = d.get("action", "HOLD").upper()
            if action not in ("BUY", "SELL", "HOLD"):
                action = "HOLD"

            confidence = float(d.get("confidence", 0))
            portfolio_pct = min(float(d.get("portfolio_percent", 3)), settings.max_position_percent)

            decisions.append(TradeDecision(
                symbol=d.get("symbol", ""),
                action=action,
                confidence=confidence,
                portfolio_percent=portfolio_pct,
                reasoning=d.get("reasoning", ""),
                stop_loss_pct=float(d.get("stop_loss_pct", settings.stop_loss_percent)),
                take_profit_pct=float(d.get("take_profit_pct", settings.take_profit_percent)),
            ))

        return AgentResponse(
            decisions=decisions,
            market_outlook=data.get("market_outlook", ""),
            risk_level=data.get("risk_level", "MEDIUM"),
            raw_response=raw,
        )

    # ------------------------------------------------------------------
    # Formateo
    # ------------------------------------------------------------------

    def _format_technical_analysis(self, ta_data: dict[str, Any]) -> str:
        """Formatea el analisis tecnico como texto legible para el LLM."""
        lines = []
        for symbol, summary in ta_data.items():
            if isinstance(summary, dict):
                lines.append(f"\n#### {symbol}")
                lines.append(f"**Senal general**: {summary.get('overall_signal', 'neutral').upper()} "
                           f"(score: {summary.get('overall_score', 0):.3f})")
                for sig in summary.get("signals", []):
                    emoji = "🟢" if sig["signal"] == "buy" else "🔴" if sig["signal"] == "sell" else "⚪"
                    lines.append(f"  {emoji} {sig['name']}: {sig['detail']}")
            else:
                lines.append(f"\n#### {symbol}: {summary}")

        return "\n".join(lines) if lines else "Sin datos de analisis tecnico disponibles."

    # ------------------------------------------------------------------
    # Filtrar decisiones accionables
    # ------------------------------------------------------------------

    def get_actionable_decisions(self, response: AgentResponse, min_confidence: float = 60) -> list[TradeDecision]:
        """Filtra solo las decisiones con confianza suficiente y accion clara."""
        actionable = []
        for d in response.decisions:
            if d.action in ("BUY", "SELL") and d.confidence >= min_confidence and d.symbol:
                actionable.append(d)
                logger.info(
                    f"Decision accionable: {d.action} {d.symbol} "
                    f"(confianza: {d.confidence}%, razon: {d.reasoning[:80]})"
                )
        return actionable

    @property
    def decision_history(self) -> list[dict[str, Any]]:
        return self._decision_history.copy()
