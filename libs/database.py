from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, JSON, Text, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import os

Base = declarative_base()

class PriceData(Base):
    __tablename__ = 'price_data'

    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    date = Column(DateTime, nullable=False, index=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Integer)
    vwap = Column(Float)

    __table_args__ = (
        Index('idx_symbol_date', 'symbol', 'date', unique=True),
    )

class CorporateEvent(Base):
    __tablename__ = 'corporate_events'

    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    event_date = Column(DateTime, nullable=False, index=True)
    event_type = Column(String(50))  # earnings, dividend, split
    data = Column(JSON)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class TradePlan(Base):
    __tablename__ = 'trade_plans'

    id = Column(Integer, primary_key=True)
    plan_date = Column(DateTime, nullable=False, index=True)
    mode = Column(String(10))  # paper or live
    universe = Column(JSON)
    orders = Column(JSON)
    risk_metrics = Column(JSON)
    performance_metrics = Column(JSON)
    notes = Column(Text)
    approved = Column(Boolean, default=False)
    executed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Order(Base):
    __tablename__ = 'orders'

    id = Column(Integer, primary_key=True)
    order_id = Column(String(100), unique=True, index=True)
    plan_id = Column(Integer, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    side = Column(String(10))  # buy or sell
    qty = Column(Integer)
    order_type = Column(String(20))  # market, limit, stop
    limit_price = Column(Float)
    stop_price = Column(Float)
    time_in_force = Column(String(10))  # day, gtc, ioc, fok
    status = Column(String(20))  # pending, filled, cancelled, rejected
    filled_qty = Column(Integer)
    filled_avg_price = Column(Float)
    submitted_at = Column(DateTime)
    filled_at = Column(DateTime)
    cancelled_at = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Position(Base):
    __tablename__ = 'positions'

    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    qty = Column(Integer)
    avg_entry_price = Column(Float)
    market_value = Column(Float)
    cost_basis = Column(Float)
    unrealized_pl = Column(Float)
    unrealized_plpc = Column(Float)
    current_price = Column(Float)
    lastday_price = Column(Float)
    change_today = Column(Float)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Signal(Base):
    __tablename__ = 'signals'

    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, index=True)
    signal_date = Column(DateTime, nullable=False, index=True)
    factor_name = Column(String(50))
    raw_value = Column(Float)
    normalized_score = Column(Float)
    weight = Column(Float)
    rationale = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Watchlist(Base):
    __tablename__ = 'watchlist'

    id = Column(Integer, primary_key=True)
    symbol = Column(String(10), nullable=False, unique=True, index=True)
    source = Column(String(50))  # rss, sec, yahoo, finviz, etc
    score = Column(Float, default=0.0)  # weighted mention score
    mention_count = Column(Integer, default=0)
    categories = Column(JSON)  # list of categories like 'trending', 'high_volume', etc
    last_seen = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class RSSFeedState(Base):
    __tablename__ = 'rss_feed_state'

    id = Column(Integer, primary_key=True)
    feed_name = Column(String(100), nullable=False, unique=True, index=True)
    etag = Column(String(200))
    last_modified = Column(String(200))
    last_fetch = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class SeenArticle(Base):
    __tablename__ = 'seen_articles'

    id = Column(Integer, primary_key=True)
    article_hash = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class NewsArticle(Base):
    __tablename__ = 'news_articles'

    id = Column(Integer, primary_key=True)
    article_id = Column(String(100), unique=True, index=True)
    symbol = Column(String(10), nullable=False, index=True)
    published_at = Column(DateTime, nullable=False, index=True)
    title = Column(Text, nullable=False)
    author = Column(String(100))
    url = Column(Text)
    summary = Column(Text)
    source = Column(String(50))  # polygon, alphavantage, etc
    tickers = Column(JSON)  # List of all mentioned tickers
    sentiment_score = Column(Float)  # Overall sentiment -1 to 1
    keywords = Column(JSON)  # List of keywords/tags
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        Index('idx_symbol_published', 'symbol', 'published_at'),
    )

def get_db_engine(db_url=None):
    if not db_url:
        db_url = os.getenv('DB_URL', 'postgresql://trader:trader@localhost:5432/trader')
    return create_engine(db_url)

def init_db(engine=None):
    if not engine:
        engine = get_db_engine()
    Base.metadata.create_all(engine)
    return engine

def get_session(engine=None):
    if not engine:
        engine = get_db_engine()
    Session = sessionmaker(bind=engine)
    return Session()