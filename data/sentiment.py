"""
AutoInvest - Recopilador de Sentimiento
Obtiene el Fear & Greed Index y noticias crypto de RSS feeds.
"""

from __future__ import annotations

import datetime as _dt
import time
from typing import Any

import feedparser
import requests
from loguru import logger
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


class SentimentCollector:
    """Recopila datos de sentimiento del mercado crypto."""

    # RSS feeds de noticias crypto
    _RSS_FEEDS = [
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("CoinTelegraph", "https://cointelegraph.com/rss"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("The Block", "https://www.theblock.co/rss.xml"),
    ]

    # Fear & Greed Index API
    _FNG_URL = "https://api.alternative.me/fng/"

    def __init__(self) -> None:
        self._vader = SentimentIntensityAnalyzer()
        self._news_cache: list[dict] = []
        self._last_news_fetch = 0.0

    # ------------------------------------------------------------------
    # Fear & Greed Index
    # ------------------------------------------------------------------

    def fetch_fear_greed(self, days: int = 7) -> dict[str, Any]:
        """
        Obtiene el Fear & Greed Index actual y de los ultimos N dias.

        Returns:
            {
                "current_value": 45,
                "current_label": "Fear",
                "history": [{"value": 45, "label": "Fear", "date": "2026-04-08"}, ...]
            }
        """
        try:
            resp = requests.get(self._FNG_URL, params={"limit": days}, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            entries = data.get("data", [])
            if not entries:
                return {"current_value": 50, "current_label": "Neutral", "history": []}

            current = entries[0]
            history = [
                {
                    "value": int(e.get("value", 50)),
                    "label": e.get("value_classification", "Neutral"),
                    "date": _dt.datetime.fromtimestamp(int(e.get("timestamp", 0))).strftime("%Y-%m-%d"),
                }
                for e in entries
            ]

            result = {
                "current_value": int(current.get("value", 50)),
                "current_label": current.get("value_classification", "Neutral"),
                "history": history,
            }

            logger.info(f"Fear & Greed: {result['current_value']} ({result['current_label']})")
            return result

        except requests.RequestException as exc:
            logger.error(f"Error obteniendo Fear & Greed Index: {exc}")
            return {"current_value": 50, "current_label": "Neutral", "history": []}

    # ------------------------------------------------------------------
    # Noticias RSS
    # ------------------------------------------------------------------

    def fetch_news(self, max_per_feed: int = 10) -> list[dict[str, Any]]:
        """
        Recopila noticias recientes de RSS feeds crypto.

        Returns:
            Lista de noticias con titulo, fuente, fecha y score de sentimiento.
        """
        # Cache de 10 minutos
        if time.time() - self._last_news_fetch < 600 and self._news_cache:
            return self._news_cache

        all_news: list[dict[str, Any]] = []

        for source_name, feed_url in self._RSS_FEEDS:
            try:
                feed = feedparser.parse(feed_url)
                entries = feed.get("entries", [])[:max_per_feed]

                for entry in entries:
                    title = entry.get("title", "")
                    summary = entry.get("summary", "")
                    published = entry.get("published", "")
                    link = entry.get("link", "")

                    # Analisis de sentimiento con VADER
                    text = f"{title}. {summary}"
                    sentiment = self._vader.polarity_scores(text)

                    all_news.append({
                        "source": source_name,
                        "title": title,
                        "summary": summary[:200] if summary else "",
                        "link": link,
                        "published": published,
                        "sentiment_score": sentiment["compound"],  # -1 a +1
                        "sentiment_pos": sentiment["pos"],
                        "sentiment_neg": sentiment["neg"],
                        "sentiment_neu": sentiment["neu"],
                    })

            except Exception as exc:
                logger.warning(f"Error leyendo feed {source_name}: {exc}")
                continue

        # Ordenar por fecha (mas recientes primero)
        all_news.sort(key=lambda x: x.get("published", ""), reverse=True)

        self._news_cache = all_news
        self._last_news_fetch = time.time()

        logger.info(f"Noticias recopiladas: {len(all_news)} articulos de {len(self._RSS_FEEDS)} feeds")
        return all_news

    # ------------------------------------------------------------------
    # Sentimiento consolidado
    # ------------------------------------------------------------------

    def get_market_sentiment(self) -> dict[str, Any]:
        """
        Genera un score consolidado de sentimiento del mercado.

        Combina:
        - Fear & Greed Index (60% peso) - mas confiable que NLP basico
        - Sentimiento de noticias (40% peso) - con decaimiento temporal

        Returns:
            {
                "overall_score": 0.35,         # -1 (extreme fear/bearish) a +1 (extreme greed/bullish)
                "overall_label": "Slightly Bullish",
                "fear_greed": {...},
                "news_sentiment": {...},
                "top_positive_news": [...],
                "top_negative_news": [...],
            }
        """
        # Fear & Greed
        fng = self.fetch_fear_greed()
        # Normalizar a -1 ... +1 (0=extreme fear, 100=extreme greed)
        fng_normalized = (fng["current_value"] - 50) / 50

        # Noticias con decaimiento temporal (#9)
        news = self.fetch_news()
        if news:
            weighted_sentiment = 0.0
            total_weight = 0.0

            for n in news:
                # Calcular peso temporal: noticias viejas pesan menos
                weight = self._calculate_time_weight(n.get("published", ""))
                weighted_sentiment += n["sentiment_score"] * weight
                total_weight += weight

            avg_sentiment = weighted_sentiment / total_weight if total_weight > 0 else 0.0
        else:
            avg_sentiment = 0.0

        # #9 - Score consolidado (60% FnG + 40% noticias)
        overall = fng_normalized * 0.6 + avg_sentiment * 0.4

        # Label
        if overall > 0.5:
            label = "Muy Alcista"
        elif overall > 0.2:
            label = "Alcista"
        elif overall > -0.2:
            label = "Neutral"
        elif overall > -0.5:
            label = "Bajista"
        else:
            label = "Muy Bajista"

        # Top noticias
        positive = sorted(news, key=lambda x: x["sentiment_score"], reverse=True)[:5]
        negative = sorted(news, key=lambda x: x["sentiment_score"])[:5]

        result = {
            "overall_score": round(overall, 3),
            "overall_label": label,
            "fear_greed": fng,
            "news_avg_sentiment": round(avg_sentiment, 3),
            "news_count": len(news),
            "top_positive_news": [
                {"title": n["title"], "source": n["source"], "score": round(n["sentiment_score"], 3)}
                for n in positive
            ],
            "top_negative_news": [
                {"title": n["title"], "source": n["source"], "score": round(n["sentiment_score"], 3)}
                for n in negative
            ],
        }

        logger.info(f"Sentimiento del mercado: {overall:.3f} ({label})")
        return result

    @staticmethod
    def _calculate_time_weight(published_str: str) -> float:
        """
        Calcula un peso temporal para una noticia.
        Noticias recientes (<2h) pesan 1.0, de 2-4h pesan 0.75,
        de 4-8h pesan 0.5, mas de 8h pesan 0.25.
        """
        if not published_str:
            return 0.5  # peso neutral si no hay fecha

        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(published_str)
            age_hours = (_dt.datetime.now(pub_dt.tzinfo) - pub_dt).total_seconds() / 3600
        except Exception:
            return 0.5

        if age_hours < 2:
            return 1.0
        elif age_hours < 4:
            return 0.75
        elif age_hours < 8:
            return 0.5
        else:
            return 0.25

