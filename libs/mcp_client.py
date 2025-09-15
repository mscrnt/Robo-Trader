"""
MCP Client for Alpha Vantage with Ollama
Bridges between Ollama and Alpha Vantage MCP server
Based on: https://github.com/jonigl/mcp-client-for-ollama
"""

import os
import sys
import json
import logging
import asyncio
import httpx
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import pandas as pd

sys.path.append('/app')

logger = logging.getLogger(__name__)

class AlphaVantageMCPClient:
    """
    MCP Client for Alpha Vantage that works with Ollama
    Uses the Alpha Vantage MCP server to fetch market data
    """

    def __init__(self):
        self.api_key = os.getenv('ALPHA_VANTAGE_API_KEY', '')
        self.ollama_base_url = os.getenv('LLM_BASE_URL', 'http://192.168.69.197:11434')
        self.ollama_model = os.getenv('LLM_MODEL', 'deepseek-v2:16b')

        # MCP server endpoint
        self.mcp_url = f"https://mcp.alphavantage.co/mcp?apikey={self.api_key}"

        # Track daily usage
        self.daily_calls_made = 0
        self.max_daily_calls = 25

        logger.info(f"Alpha Vantage MCP Client initialized")

    async def list_tools(self) -> List[Dict]:
        """
        List available tools from Alpha Vantage MCP server
        """
        try:
            async with httpx.AsyncClient() as client:
                # MCP list tools request
                response = await client.post(
                    self.mcp_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/list",
                        "id": 1
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    data = response.json()
                    tools = data.get('result', {}).get('tools', [])
                    logger.info(f"Available MCP tools: {[t.get('name') for t in tools]}")
                    return tools
                else:
                    logger.error(f"Failed to list tools: {response.status_code}")
                    return []

        except Exception as e:
            logger.error(f"Error listing MCP tools: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict) -> Optional[Dict]:
        """
        Call a specific tool on the MCP server

        Args:
            tool_name: Name of the tool (e.g., 'get_quote', 'get_time_series_daily')
            arguments: Tool arguments

        Returns:
            Tool response or None
        """
        if self.daily_calls_made >= self.max_daily_calls:
            logger.warning(f"Daily limit reached: {self.daily_calls_made}/{self.max_daily_calls}")
            return None

        try:
            async with httpx.AsyncClient() as client:
                # MCP tool call request
                response = await client.post(
                    self.mcp_url,
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": arguments
                        },
                        "id": self.daily_calls_made + 1
                    },
                    timeout=30.0
                )

                self.daily_calls_made += 1

                if response.status_code == 200:
                    data = response.json()
                    result = data.get('result', {})

                    # Debug log the result structure
                    if result:
                        logger.debug(f"MCP result keys: {list(result.keys())}")
                        if 'content' in result:
                            content = result.get('content', [])
                            logger.debug(f"MCP content type: {type(content)}, length: {len(content) if isinstance(content, list) else 'N/A'}")
                            if isinstance(content, list) and len(content) > 0:
                                logger.debug(f"First content item keys: {list(content[0].keys()) if isinstance(content[0], dict) else 'not a dict'}")

                    logger.info(f"MCP tool {tool_name} called successfully (call {self.daily_calls_made}/{self.max_daily_calls})")
                    return result
                else:
                    logger.error(f"MCP tool call failed: {response.status_code}")
                    return None

        except Exception as e:
            logger.error(f"Error calling MCP tool {tool_name}: {e}")
            return None

    async def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Get real-time quote for a symbol
        """
        return await self.call_tool("GLOBAL_QUOTE", {"symbol": symbol})

    async def get_time_series_daily(self, symbol: str, outputsize: str = "compact") -> Optional[pd.DataFrame]:
        """
        Get daily time series data for a symbol

        Args:
            symbol: Stock symbol
            outputsize: 'compact' (last 100 days) or 'full' (all available)

        Returns:
            DataFrame with OHLCV data or None
        """
        result = await self.call_tool("TIME_SERIES_DAILY", {
            "symbol": symbol,
            "outputsize": outputsize
        })

        if result and 'content' in result:
            try:
                # Parse the response content
                content = result.get('content', [])
                if isinstance(content, list) and len(content) > 0:
                    data_str = content[0].get('text', '')

                    # Debug log the raw response
                    logger.debug(f"Raw MCP response text (first 500 chars): {str(data_str)[:500]}")

                    # Parse JSON from text - handle potential format issues
                    if data_str:
                        # Sometimes MCP returns malformed JSON, try to clean it
                        if isinstance(data_str, str):
                            # Remove any BOM or invisible characters
                            data_str = data_str.strip().lstrip('\ufeff')
                            # If it starts with invalid chars, find the first {
                            if not data_str.startswith('{'):
                                json_start = data_str.find('{')
                                if json_start > 0:
                                    data_str = data_str[json_start:]
                            data = json.loads(data_str)
                        else:
                            data = data_str

                        # Check for rate limit message
                        if 'Information' in data and 'rate limit' in data.get('Information', '').lower():
                            logger.warning(f"Alpha Vantage rate limit reached: {data.get('Information')}")
                            self.daily_calls_made = self.max_daily_calls  # Mark as exhausted
                            return None

                        # Convert to DataFrame
                        if 'Time Series (Daily)' in data:
                            time_series = data['Time Series (Daily)']
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
                            return df

            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                logger.error(f"Failed to parse: {data_str[:200] if isinstance(data_str, str) else str(data_str)[:200]}")
            except Exception as e:
                logger.error(f"Error parsing time series data: {e}")
                import traceback
                logger.error(traceback.format_exc())

        return None

    async def get_company_overview(self, symbol: str) -> Optional[Dict]:
        """
        Get company fundamental data
        """
        result = await self.call_tool("OVERVIEW", {"symbol": symbol})

        if result and 'content' in result:
            try:
                content = result.get('content', [])
                if isinstance(content, list) and len(content) > 0:
                    data_str = content[0].get('text', '')
                    return json.loads(data_str) if isinstance(data_str, str) else data_str
            except Exception as e:
                logger.error(f"Error parsing company overview: {e}")

        return None

    async def get_news_sentiment(self, tickers: str, limit: int = 10) -> Optional[List[Dict]]:
        """
        Get news and sentiment for tickers

        Args:
            tickers: Comma-separated ticker symbols
            limit: Number of articles to return

        Returns:
            List of news articles with sentiment or None
        """
        result = await self.call_tool("NEWS_SENTIMENT", {
            "tickers": tickers,
            "limit": str(limit)
        })

        if result and 'content' in result:
            try:
                content = result.get('content', [])
                if isinstance(content, list) and len(content) > 0:
                    data_str = content[0].get('text', '')
                    data = json.loads(data_str) if isinstance(data_str, str) else data_str
                    return data.get('feed', [])
            except Exception as e:
                logger.error(f"Error parsing news sentiment: {e}")

        return None

    async def fetch_top_symbols_data(self, symbols: List[str], max_symbols: int = 25) -> Dict[str, pd.DataFrame]:
        """
        Fetch data for top symbols using MCP

        Args:
            symbols: List of symbols sorted by priority
            max_symbols: Maximum symbols to fetch (default 25)

        Returns:
            Dictionary of symbol -> DataFrame
        """
        results = {}

        # Only fetch top symbols up to our daily limit
        remaining_calls = self.max_daily_calls - self.daily_calls_made
        symbols_to_fetch = symbols[:min(max_symbols, remaining_calls)]

        if not symbols_to_fetch:
            logger.warning(f"No Alpha Vantage MCP calls remaining ({self.daily_calls_made}/{self.max_daily_calls})")
            return results

        logger.info(f"Alpha Vantage MCP: Fetching {len(symbols_to_fetch)} top symbols")

        for symbol in symbols_to_fetch:
            if self.daily_calls_made >= self.max_daily_calls:
                break

            try:
                # Get daily time series via MCP
                df = await self.get_time_series_daily(symbol)

                if df is not None and not df.empty:
                    results[symbol] = df
                    logger.info(f"MCP: Got {len(df)} bars for {symbol}")
                else:
                    logger.warning(f"MCP: No data for {symbol}")

            except Exception as e:
                logger.error(f"MCP error for {symbol}: {e}")

        logger.info(f"Alpha Vantage MCP: Fetched {len(results)} symbols. Daily usage: {self.daily_calls_made}/{self.max_daily_calls}")
        return results

    def create_ollama_prompt_with_mcp(self, symbols: List[str]) -> str:
        """
        Create a prompt for Ollama that includes MCP context

        Args:
            symbols: List of symbols to analyze

        Returns:
            Formatted prompt for Ollama
        """
        prompt = f"""You have access to Alpha Vantage MCP server with these tools:
- get_quote: Get real-time stock quotes
- get_time_series_daily: Get historical daily prices
- get_company_overview: Get company fundamentals
- get_news_sentiment: Get news and sentiment analysis

Analyze these symbols and provide trading signals: {', '.join(symbols[:10])}

For each symbol, consider:
1. Recent price momentum
2. Volume patterns
3. Technical indicators
4. News sentiment if available

Return a JSON response with this structure:
{{
    "symbols": [
        {{
            "ticker": "SYMBOL",
            "signal": "BUY|HOLD|SELL",
            "score": 0.0 to 1.0,
            "rationale": "Brief explanation"
        }}
    ]
}}"""

        return prompt

    async def analyze_with_ollama(self, symbols: List[str], market_data: Dict[str, pd.DataFrame]) -> Optional[Dict]:
        """
        Use Ollama to analyze market data fetched via MCP

        Args:
            symbols: List of symbols
            market_data: Dictionary of symbol -> DataFrame from MCP

        Returns:
            Ollama's analysis or None
        """
        try:
            # Prepare context with actual data
            context = {
                "symbols": {},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            for symbol, df in market_data.items():
                if not df.empty:
                    latest = df.iloc[-1]
                    context["symbols"][symbol] = {
                        "last_close": float(latest['close']),
                        "last_volume": int(latest['volume']),
                        "5d_return": float((df.iloc[-1]['close'] - df.iloc[-5]['close']) / df.iloc[-5]['close']) if len(df) >= 5 else 0,
                        "20d_avg_volume": float(df['volume'].tail(20).mean()) if len(df) >= 20 else float(latest['volume'])
                    }

            # Query Ollama with context
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_base_url}/api/generate",
                    json={
                        "model": self.ollama_model,
                        "prompt": f"""Analyze this market data and provide trading signals:

{json.dumps(context, indent=2)}

Return a JSON response with buy/hold/sell signals and scores for each symbol.""",
                        "temperature": 0.2,
                        "stream": False,
                        "format": "json"
                    },
                    timeout=60.0
                )

                if response.status_code == 200:
                    result = response.json()
                    return json.loads(result.get('response', '{}'))

        except Exception as e:
            logger.error(f"Ollama analysis error: {e}")

        return None


# Synchronous wrapper for backward compatibility
class AlphaVantageMCPProvider:
    """
    Synchronous wrapper for the async MCP client
    """

    def __init__(self):
        self.client = AlphaVantageMCPClient()

    def fetch_top_symbols_data(self, symbols: List[str], max_symbols: int = 25) -> Dict[str, pd.DataFrame]:
        """
        Synchronous wrapper for fetching top symbols data
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.client.fetch_top_symbols_data(symbols, max_symbols)
            )
        finally:
            loop.close()

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Synchronous wrapper for getting quotes
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.client.get_quote(symbol)
            )
        finally:
            loop.close()

    def is_available(self) -> bool:
        """Check if MCP client is available"""
        return bool(self.client.api_key) and self.client.daily_calls_made < self.client.max_daily_calls

    def get_daily_bars(self, symbols: List[str]) -> Dict[str, pd.DataFrame]:
        """
        Get daily bars for symbols, falling back to regular API if MCP is rate limited
        """
        results = {}

        # First try MCP for all symbols
        if self.is_available():
            results = self.fetch_top_symbols_data(symbols, len(symbols))

        # If MCP failed or was rate limited, try regular API for missing symbols
        missing_symbols = [s for s in symbols if s not in results]
        if missing_symbols and self.client.api_key:
            logger.info(f"Falling back to regular Alpha Vantage API for {len(missing_symbols)} symbols")
            for symbol in missing_symbols[:5]:  # Limit to 5 to avoid rate limits
                try:
                    import requests
                    url = f"https://www.alphavantage.co/query"
                    params = {
                        'function': 'TIME_SERIES_DAILY',
                        'symbol': symbol,
                        'outputsize': 'compact',
                        'apikey': self.client.api_key
                    }
                    response = requests.get(url, params=params, timeout=10)
                    if response.status_code == 200:
                        data = response.json()

                        # Check for rate limit
                        if 'Information' in data and 'rate limit' in data.get('Information', '').lower():
                            logger.warning(f"Regular API also rate limited")
                            break

                        # Parse time series data
                        if 'Time Series (Daily)' in data:
                            time_series = data['Time Series (Daily)']
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
                            results[symbol] = df
                            logger.info(f"Regular API: Got {len(df)} bars for {symbol}")
                except Exception as e:
                    logger.error(f"Regular API error for {symbol}: {e}")

        return results