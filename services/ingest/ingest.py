import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import yfinance as yf
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
import yaml

sys.path.append('/app')
from libs.database import get_session, PriceData, CorporateEvent
from libs.data_providers import DataProviderChain

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class MarketDataIngestor:
    def __init__(self):
        self.alpaca_client = self._get_alpaca_client()
        self.universe = self._load_universe()
        self.data_provider_chain = DataProviderChain()

        # Initialize Alpha Vantage - try MCP first, fallback to direct API
        self.alpha = None
        self.use_alpha = False

        # Try MCP client first (preferred)
        try:
            from libs.mcp_client import AlphaVantageMCPProvider
            self.alpha = AlphaVantageMCPProvider()
            self.use_alpha = self.alpha.is_available()
            if self.use_alpha:
                logger.info(f"Alpha Vantage MCP Client available (calls: {self.alpha.client.daily_calls_made}/{self.alpha.client.max_daily_calls})")
            else:
                logger.info("Alpha Vantage MCP not available or limit reached")
        except Exception as e:
            logger.info(f"MCP client not available: {e}, trying direct API")

            # Fallback to direct API
            try:
                from libs.alpha_vantage import AlphaVantageProvider
                self.alpha = AlphaVantageProvider()
                self.use_alpha = self.alpha.is_available()
                if self.use_alpha:
                    logger.info(f"Alpha Vantage Direct API available ({self.alpha.daily_calls_made}/{self.alpha.max_daily_calls} calls used today)")
                else:
                    logger.info("Alpha Vantage Direct API not available or limit reached")
            except Exception as e:
                logger.info(f"Alpha Vantage not available: {e}")

    def _get_alpaca_client(self):
        key_id = os.getenv('ALPACA_KEY_ID')
        secret_key = os.getenv('ALPACA_SECRET_KEY')

        if key_id and secret_key:
            return StockHistoricalDataClient(
                api_key=key_id,
                secret_key=secret_key
            )
        return None

    def _load_universe(self) -> List[str]:
        """Load stock universe from database watchlist or build from news"""

        # Try to load from database watchlist first
        try:
            from libs.database import get_session, Watchlist
            session = get_session()

            # Get watchlist entries ordered by score (highest first)
            watchlist_entries = session.query(Watchlist).order_by(Watchlist.score.desc()).all()

            if watchlist_entries:
                symbols = [entry.symbol for entry in watchlist_entries]
                session.close()
                logger.info(f"Loaded {len(symbols)} symbols from database watchlist")
                return symbols

            session.close()
        except Exception as e:
            logger.warning(f"Could not load from database watchlist: {e}")

        # Always build new universe from news
        logger.info("Building universe from news sources...")
        try:
            from libs.news_watchlist import NewsWatchlistProvider
            provider = NewsWatchlistProvider()
            symbols = provider.build_watchlist(max_symbols=200)

            if symbols:
                logger.info(f"Built universe with {len(symbols)} symbols from news")
                return symbols
        except Exception as e:
            logger.error(f"Failed to build news-driven universe: {e}")

        # Return empty list if we can't get news - no hardcoded symbols
        logger.warning("No symbols available - will need to fetch news first")
        return []

    def fetch_daily_bars(self, symbols: List[str] = None, days: int = 5):
        """Fetch daily OHLCV data using multiple data sources with fallback"""
        if not symbols:
            symbols = self.universe

        end_date = datetime.now(timezone.utc)
        start_date = end_date - timedelta(days=days)

        logger.info(f"Fetching {days} days of data for {len(symbols)} symbols")

        session = get_session()
        try:
            # First try Alpaca if available
            alpaca_success = False
            if self.alpaca_client:
                try:
                    # Process in small batches to avoid rate limits
                    for i in range(0, len(symbols), 5):
                        batch = symbols[i:i+5]
                        try:
                            request = StockBarsRequest(
                                symbol_or_symbols=batch,
                                timeframe=TimeFrame.Day,
                                start=start_date,
                                end=end_date,
                                feed='iex'  # Use IEX feed which is available for free accounts
                            )

                            bars = self.alpaca_client.get_stock_bars(request)

                            for symbol in batch:
                                if symbol in bars.data:
                                    for bar in bars.data[symbol]:
                                        self._save_price_bar(session, symbol, bar)

                            # Small delay to avoid rate limits
                            import time
                            time.sleep(0.2)

                        except Exception as e:
                            logger.warning(f"Alpaca failed for batch {batch}: {e}")
                            # Continue with other batches

                    alpaca_success = True
                    logger.info(f"Fetched Alpaca data for symbols")

                except Exception as e:
                    logger.warning(f"Alpaca API failed: {e}, will use fallback providers")
                    alpaca_success = False

            # If Alpaca failed or unavailable, use the fallback chain
            if not alpaca_success:
                logger.info("Using fallback data provider chain")
                results = self.data_provider_chain.fetch_daily_bars(symbols, days)

                for symbol, df in results.items():
                    if df is not None and not df.empty:
                        for _, row in df.iterrows():
                            # Convert date to datetime if needed
                            if hasattr(row['date'], 'to_pydatetime'):
                                date_val = row['date'].to_pydatetime()
                            else:
                                date_val = row['date']

                            price_data = PriceData(
                                symbol=symbol,
                                date=date_val,
                                open=row['open'],
                                high=row['high'],
                                low=row['low'],
                                close=row['close'],
                                volume=int(row['volume'])
                            )

                            # Check if exists
                            existing = session.query(PriceData).filter_by(
                                symbol=symbol,
                                date=date_val
                            ).first()

                            if not existing:
                                session.add(price_data)
                            else:
                                # Update existing
                                existing.open = price_data.open
                                existing.high = price_data.high
                                existing.low = price_data.low
                                existing.close = price_data.close
                                existing.volume = price_data.volume

            session.commit()
            logger.info(f"Successfully ingested price data for symbols")

        except Exception as e:
            session.rollback()
            logger.error(f"Error in data ingestion: {e}")
            raise
        finally:
            session.close()

    def _save_price_bar(self, session, symbol: str, bar):
        """Save a single price bar to database"""
        price_data = PriceData(
            symbol=symbol,
            date=bar.timestamp,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            vwap=bar.vwap if hasattr(bar, 'vwap') else None
        )

        # Check if exists
        existing = session.query(PriceData).filter_by(
            symbol=symbol,
            date=bar.timestamp
        ).first()

        if not existing:
            session.add(price_data)
        else:
            # Update existing
            existing.open = price_data.open
            existing.high = price_data.high
            existing.low = price_data.low
            existing.close = price_data.close
            existing.volume = price_data.volume
            existing.vwap = price_data.vwap

    def fetch_corporate_events(self, symbols: List[str] = None):
        """Fetch earnings, dividends, and splits"""
        if not symbols:
            symbols = self.universe

        session = get_session()
        try:
            for symbol in symbols:
                try:
                    ticker = yf.Ticker(symbol)

                    # Fetch calendar events
                    calendar = ticker.calendar
                    if calendar and 'Earnings Date' in calendar:
                        earnings_dates = calendar['Earnings Date']
                        if isinstance(earnings_dates, pd.Timestamp):
                            earnings_dates = [earnings_dates]

                        for date in earnings_dates:
                            if pd.notna(date):
                                # Handle both datetime.date and pd.Timestamp objects
                                if hasattr(date, 'to_pydatetime'):
                                    event_datetime = date.to_pydatetime()
                                elif isinstance(date, datetime):
                                    event_datetime = date
                                else:
                                    # It's a date object, convert to datetime
                                    event_datetime = datetime.combine(date, datetime.min.time())

                                event = CorporateEvent(
                                    symbol=symbol,
                                    event_date=event_datetime,
                                    event_type='earnings',
                                    data={'source': 'yfinance'}
                                )

                                # Check if exists
                                existing = session.query(CorporateEvent).filter_by(
                                    symbol=symbol,
                                    event_date=event_datetime,
                                    event_type='earnings'
                                ).first()

                                if not existing:
                                    session.add(event)

                except Exception as e:
                    logger.warning(f"Error fetching events for {symbol}: {e}")

            session.commit()
            logger.info(f"Successfully ingested corporate events")

        except Exception as e:
            session.rollback()
            logger.error(f"Error in event ingestion: {e}")
        finally:
            session.close()

    def run_daily_ingest(self):
        """Run the complete daily ingestion pipeline"""
        logger.info("Starting daily market data ingestion")
        logger.info("="*60)

        # Step 1: Build universe from RSS/SEC news FIRST
        logger.info("Step 1: Building universe from RSS feeds and SEC filings...")
        self.universe = self._load_universe()

        if not self.universe:
            logger.error("No symbols in universe - news discovery may have failed")
            return

        logger.info(f"Universe built with {len(self.universe)} symbols")
        logger.info(f"Top 20 symbols: {self.universe[:20]}")

        # Step 2: Fetch price data using regular providers (YFinance, Finnhub)
        # Get 60 days for better technical analysis
        logger.info("="*60)
        logger.info(f"Step 2: Fetching price data for {len(self.universe)} symbols using YFinance/Finnhub...")
        self.fetch_daily_bars(days=60)

        # Step 3: Check what data we have
        session = get_session()
        try:
            from datetime import datetime, timedelta, timezone
            recent_date = datetime.now(timezone.utc) - timedelta(days=7)
            count = session.query(PriceData).filter(
                PriceData.date >= recent_date
            ).count()
            logger.info(f"Price data in DB: {count} records from last 7 days")
        finally:
            session.close()

        # Step 4: Get top symbols based on volume/mentions for Alpha Vantage enhancement
        logger.info("="*60)
        logger.info("Step 3: Identifying top symbols for Alpha Vantage enhancement...")
        top_symbols = self._get_top_symbols()

        # Step 5: Use Alpha Vantage MCP for top 25 symbols only (AFTER basic data is fetched)
        if self.use_alpha and top_symbols:
            try:
                logger.info(f"Step 4: Using Alpha Vantage MCP for top {min(25, len(top_symbols))} symbols...")
                enhanced_data = self.alpha.fetch_top_symbols_data(top_symbols, max_symbols=25)

                # Save enhanced data to database
                session = get_session()
                try:
                    for symbol, df in enhanced_data.items():
                        for _, row in df.iterrows():
                            self._save_price_bar_from_dict(session, symbol, {
                                'date': row['date'],
                                'open': row['open'],
                                'high': row['high'],
                                'low': row['low'],
                                'close': row['close'],
                                'volume': row['volume']
                            })
                    session.commit()
                    logger.info(f"Saved Alpha Vantage MCP data for {len(enhanced_data)} symbols")
                except Exception as e:
                    session.rollback()
                    logger.error(f"Failed to save enhanced data: {e}")
                finally:
                    session.close()

            except Exception as e:
                logger.warning(f"Alpha Vantage MCP failed: {e}")

        logger.info("Daily ingestion completed")

    def _get_top_symbols(self) -> List[str]:
        """Get top symbols based on recent signals or volume"""
        session = get_session()
        try:
            # For now, just return the universe sorted by typical importance
            # In production, this would query signals or volume data
            return self.universe[:30]  # Return top 30 for Alpha Vantage to pick top 25
        finally:
            session.close()

    def _save_price_bar_from_dict(self, session, symbol: str, bar_data: Dict):
        """Save a price bar from dictionary data"""
        try:
            price_data = PriceData(
                symbol=symbol,
                date=bar_data['date'],
                open=float(bar_data['open']),
                high=float(bar_data['high']),
                low=float(bar_data['low']),
                close=float(bar_data['close']),
                volume=int(bar_data['volume'])
            )
            session.merge(price_data)
        except Exception as e:
            logger.error(f"Error saving bar for {symbol}: {e}")

if __name__ == "__main__":
    try:
        ingestor = MarketDataIngestor()
        ingestor.run_daily_ingest()
        logger.info("Ingest completed successfully")
    except Exception as e:
        logger.error(f"Ingest service failed: {e}")

    # Keep the service running but idle
    logger.info("Ingest service ready and waiting...")
    import time
    while True:
        time.sleep(3600)  # Sleep for an hour