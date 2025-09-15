"""
News-Driven Watchlist Builder
Discovers trending stocks from news sources and builds dynamic watchlist
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Set, Optional, Any
import asyncio

sys.path.append('/app')

logger = logging.getLogger(__name__)

class NewsWatchlistBuilder:
    """
    Builds a dynamic watchlist based on news mentions and sentiment
    """

    def __init__(self):
        self.watchlist = set()
        self.news_scores = {}  # symbol -> news_score

        # Use free news aggregator instead of MCP for discovery
        # Save MCP calls for actual data fetching
        from libs.news_aggregator import NewsAggregator
        self.news_aggregator = NewsAggregator()
        logger.info("News aggregator initialized for symbol discovery")

    async def discover_trending_stocks(self) -> List[str]:
        """
        Discover trending stocks from FREE news sources
        Returns list of symbols sorted by relevance/mentions
        """
        # Use news aggregator to get trending symbols
        news_data = self.news_aggregator.aggregate_all_news()

        # Build scores from mention counts
        trending = {}
        for symbol, mentions in news_data['symbol_mentions'].items():
            # Skip crypto and forex
            if 'CRYPTO' in symbol or 'FOREX' in symbol or 'USD' in symbol:
                continue

            trending[symbol] = {
                'score': mentions,  # Use mention count as score
                'mentions': mentions,
                'avg_sentiment': 0  # We don't have sentiment from RSS
            }

        # Store scores for later use
        self.news_scores = trending

        # Return top symbols from aggregator
        top_symbols = news_data['top_symbols']

        logger.info(f"Discovered {len(top_symbols)} symbols from news aggregation")
        logger.info(f"Top trending symbols: {top_symbols[:10]}")

        return top_symbols

    async def get_market_leaders(self) -> List[str]:
        """
        Get market leaders dynamically from news or return empty
        """
        # Don't hardcode - let news discovery find the active symbols
        # The news will naturally surface the most traded/mentioned symbols
        return []

    async def build_watchlist(self, max_symbols: int = 200) -> List[str]:
        """
        Build comprehensive watchlist from news sources only

        Returns:
            List of symbols sorted by priority (highest news score first)
        """
        logger.info("Building dynamic watchlist from news...")

        # Get trending stocks from news - this is our only source
        trending = await self.discover_trending_stocks()

        # Build watchlist from trending stocks only
        watchlist = []
        seen = set()

        # Add all trending stocks up to max
        for symbol in trending:
            if symbol not in seen and len(watchlist) < max_symbols:
                watchlist.append(symbol)
                seen.add(symbol)

        logger.info(f"Built watchlist with {len(watchlist)} symbols")
        logger.info(f"Watchlist preview: {watchlist[:20]}")

        # Save watchlist to file
        self.save_watchlist(watchlist)

        return watchlist

    def save_watchlist(self, symbols: List[str]):
        """
        Save watchlist to database
        """
        try:
            from libs.database import get_session, Watchlist
            session = get_session()

            # Clear existing watchlist
            session.query(Watchlist).delete()

            # Categorize symbols based on news metrics
            categories_map = {}

            for symbol in symbols:
                categories = []
                score = 0.0
                mentions = 0

                if symbol in self.news_scores:
                    score_data = self.news_scores[symbol]
                    score = score_data['score']
                    mentions = score_data.get('mentions', 0)

                    # Categorize by metrics
                    if score > 1.0:
                        categories.append('high_score')
                    if score_data.get('avg_sentiment', 0) > 0.5:
                        categories.append('high_sentiment')
                    if mentions > 2:
                        categories.append('high_mentions')
                    if not categories:
                        categories.append('trending')
                else:
                    categories.append('trending')

                # Add to database
                watchlist_entry = Watchlist(
                    symbol=symbol,
                    source='news_aggregator',
                    score=score,
                    mention_count=mentions,
                    categories=categories,
                    last_seen=datetime.now(timezone.utc)
                )
                session.add(watchlist_entry)

            # Commit all changes
            session.commit()
            session.close()

            logger.info(f"Saved {len(symbols)} symbols to database watchlist")

        except Exception as e:
            logger.error(f"Failed to save watchlist: {e}")

    def get_symbol_priority(self, symbol: str) -> float:
        """
        Get priority score for a symbol based on news metrics
        """
        if symbol in self.news_scores:
            return self.news_scores[symbol]['score']
        return 0.0


# Synchronous wrapper
class NewsWatchlistProvider:
    """
    Synchronous wrapper for the async watchlist builder
    """

    def __init__(self):
        self.builder = NewsWatchlistBuilder()

    def build_watchlist(self, max_symbols: int = 200) -> List[str]:
        """
        Build watchlist synchronously
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.builder.build_watchlist(max_symbols)
            )
        finally:
            loop.close()

    def get_trending_stocks(self) -> List[str]:
        """
        Get trending stocks synchronously
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                self.builder.discover_trending_stocks()
            )
        finally:
            loop.close()


if __name__ == "__main__":
    # Test the news watchlist builder
    import asyncio

    async def test():
        builder = NewsWatchlistBuilder()
        watchlist = await builder.build_watchlist()
        print(f"Generated watchlist with {len(watchlist)} symbols")
        print(f"Top 20: {watchlist[:20]}")

    asyncio.run(test())