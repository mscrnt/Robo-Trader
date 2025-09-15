import os
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import pandas as pd
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

class DataProvider(ABC):
    """Abstract base class for data providers"""

    @abstractmethod
    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily OHLCV data for symbols"""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the provider is configured and available"""
        pass

class FinnhubProvider(DataProvider):
    """Finnhub data provider - https://finnhub.io/"""

    def __init__(self):
        self.api_key = os.getenv('FINNHUB_API_KEY')
        self.base_url = 'https://finnhub.io/api/v1'

    def is_available(self) -> bool:
        return bool(self.api_key)

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars from Finnhub"""
        if not self.is_available():
            raise ValueError("Finnhub API key not configured")

        results = {}
        end_time = int(datetime.now(timezone.utc).timestamp())
        start_time = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

        # Rate limit: 30 calls/second - we can go fast!
        import time
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(0.1)  # Only 100ms delay for safety
            try:
                # Finnhub candles endpoint
                url = f"{self.base_url}/stock/candle"
                params = {
                    'symbol': symbol,
                    'resolution': 'D',  # Daily
                    'from': start_time,
                    'to': end_time,
                    'token': self.api_key
                }

                response = requests.get(url, params=params)

                # Check for rate limit (429)
                if response.status_code == 429:
                    logger.warning(f"Finnhub: Rate limit hit (429), stopping batch")
                    break  # Stop this batch to avoid more 429s

                elif response.status_code == 403:
                    logger.warning(f"Finnhub: Forbidden (403) - API key may be invalid or limit reached")
                    break  # Stop completely if forbidden

                elif response.status_code == 200:
                    data = response.json()

                    if data.get('s') == 'ok' and 'c' in data:
                        df = pd.DataFrame({
                            'date': pd.to_datetime(data['t'], unit='s'),
                            'open': data['o'],
                            'high': data['h'],
                            'low': data['l'],
                            'close': data['c'],
                            'volume': data['v']
                        })
                        results[symbol] = df
                        logger.info(f"Finnhub: Fetched {len(df)} bars for {symbol}")
                    else:
                        logger.warning(f"Finnhub: No data for {symbol}")
                else:
                    logger.warning(f"Finnhub: HTTP {response.status_code} for {symbol}")

            except Exception as e:
                logger.error(f"Finnhub error for {symbol}: {e}")

        return results

class AlphaVantageProvider(DataProvider):
    """Alpha Vantage data provider - https://www.alphavantage.co/"""

    def __init__(self):
        self.api_key = os.getenv('ALPHA_VANTAGE_API_KEY')
        self.base_url = 'https://www.alphavantage.co/query'

    def is_available(self) -> bool:
        return bool(self.api_key)

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars from Alpha Vantage"""
        if not self.is_available():
            raise ValueError("Alpha Vantage API key not configured")

        results = {}

        # Rate limit: Only 25 calls PER DAY! Skip if we've hit the limit
        import time
        max_daily_calls = 25

        # Only fetch top symbols to preserve daily quota
        symbols_to_fetch = symbols[:min(5, len(symbols))]  # Max 5 symbols
        logger.warning(f"Alpha Vantage: Limited to {len(symbols_to_fetch)} symbols due to 25/day limit")

        for i, symbol in enumerate(symbols_to_fetch):
            if i >= 5:  # Hard limit to preserve quota
                logger.warning(f"Alpha Vantage: Skipping remaining symbols to preserve daily quota")
                break
            if i > 0:
                time.sleep(2.0)  # Small delay between calls
            try:
                # Alpha Vantage TIME_SERIES_DAILY endpoint
                params = {
                    'function': 'TIME_SERIES_DAILY',
                    'symbol': symbol,
                    'apikey': self.api_key,
                    'outputsize': 'compact'  # Last 100 data points
                }

                response = requests.get(self.base_url, params=params)
                if response.status_code == 200:
                    data = response.json()

                    if 'Time Series (Daily)' in data:
                        time_series = data['Time Series (Daily)']

                        # Convert to DataFrame
                        df_data = []
                        for date_str, values in time_series.items():
                            df_data.append({
                                'date': pd.to_datetime(date_str),
                                'open': float(values['1. open']),
                                'high': float(values['2. high']),
                                'low': float(values['3. low']),
                                'close': float(values['4. close']),
                                'volume': int(values['5. volume'])
                            })

                        df = pd.DataFrame(df_data).sort_values('date')
                        # Filter to requested days
                        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
                        # Convert to naive datetime for comparison
                        df = df[pd.to_datetime(df['date']).dt.tz_localize(None) >= pd.Timestamp(cutoff_date).tz_localize(None)]

                        results[symbol] = df
                        logger.info(f"Alpha Vantage: Fetched {len(df)} bars for {symbol}")
                    elif 'Note' in data:
                        # Alpha Vantage rate limit message
                        logger.warning(f"Alpha Vantage: Rate limit hit - {data['Note'][:100]}")
                        break  # Stop to avoid wasting precious calls
                    elif 'Information' in data:
                        # Another type of rate limit message
                        logger.warning(f"Alpha Vantage: API limit - {data['Information'][:100]}")
                        break
                    else:
                        logger.warning(f"Alpha Vantage: No data for {symbol}")
                else:
                    logger.warning(f"Alpha Vantage: HTTP {response.status_code} for {symbol}")

            except Exception as e:
                logger.error(f"Alpha Vantage error for {symbol}: {e}")

        return results

class PolygonProvider(DataProvider):
    """Polygon.io data provider - https://polygon.io/"""

    def __init__(self):
        self.api_key = os.getenv('POLYGON_API_KEY')
        self.base_url = 'https://api.polygon.io'

    def is_available(self) -> bool:
        return bool(self.api_key)

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars from Polygon.io"""
        if not self.is_available():
            raise ValueError("Polygon API key not configured")

        results = {}
        end_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%d')

        # Rate limit: 5 calls/minute = 12 seconds between calls
        import time
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(12.0)  # Wait 12 seconds between calls for free tier
            try:
                # Polygon aggregates endpoint
                url = f"{self.base_url}/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
                params = {
                    'adjusted': 'true',
                    'sort': 'asc',
                    'apiKey': self.api_key
                }

                response = requests.get(url, params=params)

                # Check for rate limit
                if response.status_code == 429:
                    logger.warning(f"Polygon: Rate limit hit (429), stopping batch")
                    break

                elif response.status_code == 200:
                    data = response.json()

                    # Check for Polygon-specific rate limit response
                    if data.get('status') == '0' and data.get('message') == 'NOTOK':
                        logger.warning(f"Polygon: Rate limit - {data.get('result', 'Max rate limit reached')}")
                        break
                    elif data.get('status') == 'OK' and 'results' in data:
                        bars = data['results']

                        df_data = []
                        for bar in bars:
                            df_data.append({
                                'date': pd.to_datetime(bar['t'], unit='ms'),
                                'open': bar['o'],
                                'high': bar['h'],
                                'low': bar['l'],
                                'close': bar['c'],
                                'volume': bar['v']
                            })

                        df = pd.DataFrame(df_data)
                        results[symbol] = df
                        logger.info(f"Polygon: Fetched {len(df)} bars for {symbol}")
                    else:
                        logger.warning(f"Polygon: No data for {symbol}")
                else:
                    logger.warning(f"Polygon: HTTP {response.status_code} for {symbol}")

            except Exception as e:
                logger.error(f"Polygon error for {symbol}: {e}")

        return results

class YFinanceProvider(DataProvider):
    """Yahoo Finance fallback provider"""

    def __init__(self):
        try:
            import yfinance as yf
            self.yf = yf
            self.available = True
        except ImportError:
            self.available = False
            logger.warning("yfinance not installed")

    def is_available(self) -> bool:
        return self.available

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars from Yahoo Finance"""
        if not self.is_available():
            raise ValueError("yfinance not available")

        results = {}

        for symbol in symbols:
            try:
                ticker = self.yf.Ticker(symbol)
                hist = ticker.history(period=f"{days}d")

                if not hist.empty:
                    df = pd.DataFrame({
                        'date': hist.index,
                        'open': hist['Open'],
                        'high': hist['High'],
                        'low': hist['Low'],
                        'close': hist['Close'],
                        'volume': hist['Volume']
                    }).reset_index(drop=True)

                    results[symbol] = df
                    logger.info(f"YFinance: Fetched {len(df)} bars for {symbol}")
                else:
                    logger.warning(f"YFinance: No data for {symbol}")

            except Exception as e:
                logger.error(f"YFinance error for {symbol}: {e}")

        return results

class DataProviderChain:
    """Chain of data providers with fallback support"""

    def __init__(self):
        # Order providers by rate limits (best to worst)
        # YFinance: 2000/hr (best), Finnhub: 30/sec
        # REMOVED: Alpha Vantage - Reserved for top 25 symbols only (25/day limit)
        # REMOVED: Polygon - Reserved for news ingestion only (5/min limit)
        self.providers = [
            ('Yahoo Finance', YFinanceProvider()),  # 2000/hour - USE FIRST!
            ('Finnhub', FinnhubProvider()),        # 30/second - good backup
            # Polygon removed - reserved for news ingestion (5/min limit)
            # Alpha Vantage removed - reserved for top 25 symbols via enhanced provider
        ]

        # Track rate limit status for each provider
        self.provider_cooldowns = {}  # provider_name -> cooldown_until_timestamp

        # Log available providers
        available = [name for name, provider in self.providers if provider.is_available()]
        logger.info(f"Available data providers: {available}")

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars using fallback chain with rate limit handling"""
        all_results = {}
        remaining_symbols = set(symbols)
        import time

        for provider_name, provider in self.providers:
            if not remaining_symbols:
                break  # All symbols fetched

            # Check if provider is in cooldown
            if provider_name in self.provider_cooldowns:
                cooldown_until = self.provider_cooldowns[provider_name]
                if time.time() < cooldown_until:
                    logger.info(f"Skipping {provider_name} (in cooldown for {int(cooldown_until - time.time())}s)")
                    continue
                else:
                    # Cooldown expired, remove it
                    del self.provider_cooldowns[provider_name]
                    logger.info(f"{provider_name} cooldown expired, retrying")

            if not provider.is_available():
                logger.debug(f"Skipping {provider_name} (not configured)")
                continue

            try:
                # Skip Alpha Vantage unless absolutely necessary (only 25/day!)
                if provider_name == 'Alpha Vantage' and len(remaining_symbols) > 10:
                    logger.info(f"Skipping Alpha Vantage to preserve 25/day quota")
                    continue

                # Limit symbols for rate-limited providers
                symbols_to_fetch = list(remaining_symbols)
                if provider_name == 'Alpha Vantage':
                    # Only use for critical symbols we couldn't get elsewhere
                    symbols_to_fetch = symbols_to_fetch[:3]
                    logger.warning(f"Using precious Alpha Vantage quota for {len(symbols_to_fetch)} symbols")
                elif provider_name == 'Polygon':
                    # Polygon: 5/min, so limit batch
                    symbols_to_fetch = symbols_to_fetch[:10]
                    logger.info(f"Trying {provider_name} for {len(symbols_to_fetch)} symbols (5/min limit)")
                else:
                    logger.info(f"Trying {provider_name} for {len(symbols_to_fetch)} symbols")

                # Track if we hit rate limits
                hit_rate_limit = False
                results = provider.fetch_daily_bars(symbols_to_fetch, days)

                # Check if we got less results than expected (might indicate rate limit)
                if len(results) < len(symbols_to_fetch) / 2:
                    # If we got very few results, might be rate limited
                    if provider_name == 'Finnhub':
                        # Finnhub: cooldown for 60 seconds after rate limit
                        logger.warning(f"{provider_name} may be rate limited, setting 60s cooldown")
                        self.provider_cooldowns[provider_name] = time.time() + 60
                        hit_rate_limit = True

                # Add successful results
                for symbol, df in results.items():
                    if not df.empty:
                        all_results[symbol] = df
                        remaining_symbols.discard(symbol)

                logger.info(f"{provider_name} fetched {len(results)} symbols, {len(remaining_symbols)} remaining")

                # Rate limit protection - don't hammer multiple providers
                if results and not hit_rate_limit:
                    time.sleep(0.5)
                elif hit_rate_limit:
                    # Longer wait if we hit rate limit
                    time.sleep(2.0)

            except Exception as e:
                logger.warning(f"{provider_name} failed: {e}")
                # Set cooldown on error
                if '429' in str(e) or 'rate limit' in str(e).lower():
                    self.provider_cooldowns[provider_name] = time.time() + 60
                    logger.info(f"Set {provider_name} cooldown for 60s due to rate limit error")
                continue

        if remaining_symbols:
            logger.warning(f"Could not fetch data for: {remaining_symbols}")

        return all_results