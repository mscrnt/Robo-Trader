"""
Alpha Vantage Enhanced Data Provider
Reserved for top 25 symbols only due to 25/day API limit
Direct API usage for high-priority symbols
"""

import os
import sys
import json
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
import pandas as pd
import time

sys.path.append('/app')
from libs.database import get_session, PriceData

logger = logging.getLogger(__name__)

class AlphaVantageProvider:
    """
    Alpha Vantage provider for top symbols only
    Uses direct API calls - reserves precious 25/day limit
    """

    def __init__(self):
        self.api_key = os.getenv('ALPHA_VANTAGE_API_KEY', '')
        self.base_url = 'https://www.alphavantage.co/query'

        # Strict rate limiting - only 25 calls per day!
        self.daily_calls_made = self._load_daily_usage()
        self.max_daily_calls = 25
        self.last_call_time = 0
        self.min_call_interval = 15.0  # 15 seconds between calls to be safe

    def _load_daily_usage(self) -> int:
        """Load today's API usage count from file"""
        usage_file = '/app/storage/alpha_vantage_usage.json'
        today = datetime.now(timezone.utc).date().isoformat()

        try:
            if os.path.exists(usage_file):
                with open(usage_file, 'r') as f:
                    usage = json.load(f)
                    if usage.get('date') == today:
                        return usage.get('calls', 0)
        except:
            pass

        return 0

    def _save_daily_usage(self):
        """Save today's API usage count to file"""
        usage_file = '/app/storage/alpha_vantage_usage.json'
        today = datetime.now(timezone.utc).date().isoformat()

        try:
            os.makedirs('/app/storage', exist_ok=True)
            with open(usage_file, 'w') as f:
                json.dump({'date': today, 'calls': self.daily_calls_made}, f)
        except Exception as e:
            logger.error(f"Failed to save usage: {e}")

    def is_available(self) -> bool:
        """Check if provider is available and has calls remaining"""
        return bool(self.api_key) and self.daily_calls_made < self.max_daily_calls

    def can_make_call(self) -> bool:
        """Check if we can make another API call"""
        if self.daily_calls_made >= self.max_daily_calls:
            logger.warning(f"Alpha Vantage daily limit reached: {self.daily_calls_made}/{self.max_daily_calls}")
            return False
        return True

    def fetch_enhanced_data(self, symbol: str, function: str = "TIME_SERIES_DAILY") -> Optional[Dict]:
        """
        Fetch enhanced data for a single symbol

        Args:
            symbol: Stock symbol
            function: Alpha Vantage function to call

        Returns:
            Market data or None
        """
        if not self.can_make_call():
            return None

        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_call_time
        if time_since_last < self.min_call_interval:
            time.sleep(self.min_call_interval - time_since_last)

        self.last_call_time = time.time()

        try:
            params = {
                'function': function,
                'symbol': symbol,
                'apikey': self.api_key,
                'outputsize': 'compact'  # Last 100 data points
            }

            response = requests.get(self.base_url, params=params, timeout=10)
            self.daily_calls_made += 1
            self._save_daily_usage()

            if response.status_code == 200:
                data = response.json()

                # Check for rate limit messages
                if 'Note' in data:
                    # Standard rate limit message
                    note = data['Note']
                    if 'rate limit' in note.lower():
                        logger.warning(f"Alpha Vantage daily limit reached: {note[:100]}")
                        # Mark as maxed out for today
                        self.daily_calls_made = self.max_daily_calls
                        self._save_daily_usage()
                    else:
                        logger.warning(f"Alpha Vantage note: {note[:100]}")
                    return None
                elif 'Information' in data:
                    # Another rate limit format
                    logger.warning(f"Alpha Vantage API limit: {data['Information'][:100]}")
                    return None
                elif 'Error Message' in data:
                    logger.error(f"Alpha Vantage error: {data['Error Message']}")
                    return None

                logger.info(f"Alpha Vantage: Fetched data for {symbol} (call {self.daily_calls_made}/{self.max_daily_calls})")
                return data
            elif response.status_code == 429:
                logger.warning(f"Alpha Vantage: Rate limit HTTP 429")
                self.daily_calls_made = self.max_daily_calls
                self._save_daily_usage()
                return None
            else:
                logger.error(f"Alpha Vantage HTTP {response.status_code} for {symbol}")
                return None

        except Exception as e:
            logger.error(f"Alpha Vantage error for {symbol}: {e}")
            return None

    def fetch_top_symbols_data(self, symbols: List[str], max_symbols: int = 25) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for top symbols only

        Args:
            symbols: List of symbols sorted by priority (highest score first)
            max_symbols: Maximum symbols to fetch (default 25)

        Returns:
            Dictionary of symbol -> DataFrame with OHLCV data
        """
        results = {}

        # Only fetch top symbols up to our daily limit
        remaining_calls = self.max_daily_calls - self.daily_calls_made
        symbols_to_fetch = symbols[:min(max_symbols, remaining_calls)]

        if not symbols_to_fetch:
            logger.warning(f"No Alpha Vantage calls remaining for today ({self.daily_calls_made}/{self.max_daily_calls} used)")
            return results

        logger.info(f"Alpha Vantage Enhanced: Fetching {len(symbols_to_fetch)} top symbols (precious API calls!)")
        logger.info(f"Symbols: {', '.join(symbols_to_fetch)}")

        for symbol in symbols_to_fetch:
            if not self.can_make_call():
                break

            data = self.fetch_enhanced_data(symbol)

            if data and 'Time Series (Daily)' in data:
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

                if df_data:
                    df = pd.DataFrame(df_data).sort_values('date')
                    results[symbol] = df
                    logger.info(f"Alpha Vantage: Got {len(df)} bars for {symbol}")

        logger.info(f"Alpha Vantage Enhanced: Fetched {len(results)} symbols. Daily usage: {self.daily_calls_made}/{self.max_daily_calls}")
        return results

    def get_company_overview(self, symbol: str) -> Optional[Dict]:
        """
        Get company fundamentals (uses 1 API call)

        Args:
            symbol: Stock symbol

        Returns:
            Company overview data or None
        """
        return self.fetch_enhanced_data(symbol, function="OVERVIEW")

    def get_earnings(self, symbol: str) -> Optional[Dict]:
        """
        Get earnings data (uses 1 API call)

        Args:
            symbol: Stock symbol

        Returns:
            Earnings data or None
        """
        return self.fetch_enhanced_data(symbol, function="EARNINGS")

    def get_news_sentiment(self, symbol: str) -> Optional[Dict]:
        """
        Get news and sentiment data (uses 1 API call)

        Args:
            symbol: Stock symbol

        Returns:
            News sentiment data or None
        """
        if not self.can_make_call():
            return None

        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self.last_call_time
        if time_since_last < self.min_call_interval:
            time.sleep(self.min_call_interval - time_since_last)

        self.last_call_time = time.time()

        try:
            params = {
                'function': 'NEWS_SENTIMENT',
                'tickers': symbol,
                'apikey': self.api_key,
                'limit': 10
            }

            response = requests.get(self.base_url, params=params, timeout=10)
            self.daily_calls_made += 1
            self._save_daily_usage()

            if response.status_code == 200:
                data = response.json()
                logger.info(f"Alpha Vantage: Got news for {symbol} (call {self.daily_calls_made}/{self.max_daily_calls})")
                return data
            else:
                logger.error(f"Alpha Vantage HTTP {response.status_code} for {symbol} news")
                return None

        except Exception as e:
            logger.error(f"Alpha Vantage news error for {symbol}: {e}")
            return None


# For backward compatibility
AlphaVantageEnhancedProvider = AlphaVantageProvider  # Alias for old name
MCPDataProvider = AlphaVantageProvider  # Old alias