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

        for symbol in symbols:
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
                if response.status_code == 200:
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

        for symbol in symbols:
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
                        df = df[df['date'] >= cutoff_date]

                        results[symbol] = df
                        logger.info(f"Alpha Vantage: Fetched {len(df)} bars for {symbol}")
                    elif 'Note' in data:
                        logger.warning(f"Alpha Vantage: Rate limit hit - {data['Note']}")
                        break  # Stop to avoid further rate limiting
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

        for symbol in symbols:
            try:
                # Polygon aggregates endpoint
                url = f"{self.base_url}/v2/aggs/ticker/{symbol}/range/1/day/{start_date}/{end_date}"
                params = {
                    'adjusted': 'true',
                    'sort': 'asc',
                    'apiKey': self.api_key
                }

                response = requests.get(url, params=params)
                if response.status_code == 200:
                    data = response.json()

                    if data.get('status') == 'OK' and 'results' in data:
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
        self.providers = [
            ('Finnhub', FinnhubProvider()),
            ('Alpha Vantage', AlphaVantageProvider()),
            ('Polygon', PolygonProvider()),
            ('Yahoo Finance', YFinanceProvider())
        ]

        # Log available providers
        available = [name for name, provider in self.providers if provider.is_available()]
        logger.info(f"Available data providers: {available}")

    def fetch_daily_bars(self, symbols: List[str], days: int = 5) -> Dict[str, pd.DataFrame]:
        """Fetch daily bars using fallback chain"""
        all_results = {}
        remaining_symbols = set(symbols)

        for provider_name, provider in self.providers:
            if not remaining_symbols:
                break  # All symbols fetched

            if not provider.is_available():
                logger.debug(f"Skipping {provider_name} (not configured)")
                continue

            try:
                logger.info(f"Trying {provider_name} for {len(remaining_symbols)} symbols")
                results = provider.fetch_daily_bars(list(remaining_symbols), days)

                # Add successful results
                for symbol, df in results.items():
                    if not df.empty:
                        all_results[symbol] = df
                        remaining_symbols.discard(symbol)

                logger.info(f"{provider_name} fetched {len(results)} symbols, {len(remaining_symbols)} remaining")

                # Rate limit protection - don't hammer multiple providers
                if results:
                    import time
                    time.sleep(0.5)

            except Exception as e:
                logger.warning(f"{provider_name} failed: {e}")
                continue

        if remaining_symbols:
            logger.warning(f"Could not fetch data for: {remaining_symbols}")

        return all_results