"""
AutoInvest - Modelos de Base de Datos
Usa SQLAlchemy ORM para persistir trades, senales y snapshots del portfolio.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings


# ---------------------------------------------------------------------------
# Base ORM
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class Trade(Base):
    """Registro de cada trade ejecutado (real o paper)."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=_dt.datetime.utcnow, nullable=False)
    symbol = Column(String(20), nullable=False)              # ej: BTC/USDT
    side = Column(String(4), nullable=False)                  # buy | sell
    price = Column(Float, nullable=False)                     # precio de ejecucion
    amount = Column(Float, nullable=False)                    # cantidad de crypto
    cost = Column(Float, nullable=False)                      # precio * cantidad (en USDT)
    order_type = Column(String(10), default="market")         # market | limit
    is_paper = Column(Boolean, default=True)                  # paper vs live
    reason = Column(Text, default="")                         # razonamiento del agente
    confidence = Column(Float, default=0.0)                   # 0-100
    status = Column(String(20), default="filled")             # filled | cancelled | error
    pnl = Column(Float, default=0.0)                          # ganancia/perdida realizada
    fees = Column(Float, default=0.0)                         # comisiones estimadas


class Signal(Base):
    """Senal generada por el analisis tecnico o el agente IA."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=_dt.datetime.utcnow, nullable=False)
    symbol = Column(String(20), nullable=False)
    source = Column(String(30), nullable=False)               # technical | sentiment | agent
    action = Column(String(4), nullable=False)                # buy | sell | hold
    confidence = Column(Float, default=0.0)
    details = Column(Text, default="")                        # JSON con detalles
    was_executed = Column(Boolean, default=False)


class PortfolioSnapshot(Base):
    """Snapshot periodico del estado del portfolio."""

    __tablename__ = "portfolio_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=_dt.datetime.utcnow, nullable=False)
    total_value_usdt = Column(Float, nullable=False)          # valor total en USDT
    cash_usdt = Column(Float, nullable=False)                 # efectivo disponible
    positions_json = Column(Text, default="{}")               # JSON con posiciones
    pnl_total = Column(Float, default=0.0)                    # PnL acumulado
    pnl_percent = Column(Float, default=0.0)                  # PnL %
    max_drawdown = Column(Float, default=0.0)                 # drawdown maximo observado


class MarketDataCache(Base):
    """Cache de datos de mercado para evitar hits innecesarios a la API."""

    __tablename__ = "market_data_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=_dt.datetime.utcnow, nullable=False)
    symbol = Column(String(20), nullable=False)
    data_type = Column(String(30), nullable=False)            # ohlcv | ticker | orderbook
    data_json = Column(Text, nullable=False)                  # datos serializados
    timeframe = Column(String(10), default="")


# ---------------------------------------------------------------------------
# Engine y Session Factory
# ---------------------------------------------------------------------------

engine = create_engine(settings.db_url, echo=False)
SessionLocal = sessionmaker(bind=engine, class_=Session)


def init_db() -> None:
    """Crea todas las tablas si no existen."""
    Base.metadata.create_all(engine)


def get_session() -> Session:
    """Retorna una nueva sesion de base de datos."""
    return SessionLocal()
