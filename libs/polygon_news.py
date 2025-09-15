"""
Polygon News Ingestion Service
Reserved for news data only (5/min rate limit)
"""

import os
import sys
import logging
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import time
import json

sys.path.append('/app')
from libs.database import get_session, NewsArticle

logger = logging.getLogger(__name__)

class PolygonNewsProvider:
    """
    Polygon.io provider for news data only
    Respects 5/min rate limit
    """

    def __init__(self):
        self.api_key = os.getenv('POLYGON_API_KEY')
        self.base_url = 'https://api.polygon.io'
        self.last_call_time = 0
        self.min_call_interval = 12.0  # 5 calls/minute = 12 seconds between calls

    def is_available(self) -> bool:
        return bool(self.api_key)

    def _rate_limit(self):
        """Enforce rate limiting"""
        current_time = time.time()
        time_since_last = current_time - self.last_call_time
        if time_since_last < self.min_call_interval:
            time.sleep(self.min_call_interval - time_since_last)
        self.last_call_time = time.time()

    def fetch_ticker_news(self, symbol: str, limit: int = 10) -> List[Dict]:
        """
        Fetch news for a specific ticker
        """
        if not self.is_available():
            logger.warning("Polygon API key not configured")
            return []

        self._rate_limit()

        try:
            url = f"{self.base_url}/v2/reference/news"
            params = {
                'ticker': symbol,
                'limit': limit,
                'order': 'desc',
                'sort': 'published_utc',
                'apiKey': self.api_key
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 429:
                logger.warning(f"Polygon: Rate limit hit (429)")
                return []
            elif response.status_code == 200:
                data = response.json()
                if data.get('status') == 'OK' and 'results' in data:
                    articles = data['results']
                    logger.info(f"Polygon News: Fetched {len(articles)} articles for {symbol}")
                    return articles
                else:
                    logger.warning(f"Polygon News: No articles for {symbol}")
            else:
                logger.warning(f"Polygon News: HTTP {response.status_code} for {symbol}")

        except Exception as e:
            logger.error(f"Polygon News error for {symbol}: {e}")

        return []

    def fetch_market_news(self, limit: int = 50) -> List[Dict]:
        """
        Fetch general market news
        """
        if not self.is_available():
            logger.warning("Polygon API key not configured")
            return []

        self._rate_limit()

        try:
            url = f"{self.base_url}/v2/reference/news"
            params = {
                'limit': limit,
                'order': 'desc',
                'sort': 'published_utc',
                'apiKey': self.api_key
            }

            response = requests.get(url, params=params, timeout=10)

            if response.status_code == 429:
                logger.warning(f"Polygon: Rate limit hit (429)")
                return []
            elif response.status_code == 200:
                data = response.json()
                if data.get('status') == 'OK' and 'results' in data:
                    articles = data['results']
                    logger.info(f"Polygon News: Fetched {len(articles)} market articles")
                    return articles
                else:
                    logger.warning(f"Polygon News: No market articles")
            else:
                logger.warning(f"Polygon News: HTTP {response.status_code}")

        except Exception as e:
            logger.error(f"Polygon News error: {e}")

        return []

    def ingest_news_to_db(self, symbols: List[str] = None, market_news: bool = True):
        """
        Ingest news to database

        Args:
            symbols: List of specific symbols to fetch news for
            market_news: Whether to fetch general market news
        """
        session = get_session()
        articles_saved = 0

        try:
            # Fetch market news first
            if market_news:
                logger.info("Fetching market news...")
                articles = self.fetch_market_news(limit=50)

                for article in articles:
                    try:
                        # Extract tickers from article
                        tickers = article.get('tickers', [])
                        symbol = tickers[0] if tickers else 'MARKET'

                        news_item = NewsArticle(
                            article_id=article.get('id', ''),
                            symbol=symbol,
                            published_at=datetime.fromisoformat(
                                article.get('published_utc', '').replace('Z', '+00:00')
                            ),
                            title=article.get('title', ''),
                            author=article.get('author', ''),
                            url=article.get('article_url', ''),
                            summary=article.get('description', ''),
                            source='polygon',
                            tickers=tickers,
                            sentiment_score=None,  # Polygon doesn't provide sentiment
                            keywords=article.get('keywords', [])
                        )

                        # Check if exists
                        existing = session.query(NewsArticle).filter_by(
                            article_id=news_item.article_id
                        ).first()

                        if not existing:
                            session.add(news_item)
                            articles_saved += 1

                    except Exception as e:
                        logger.error(f"Error saving article: {e}")

            # Fetch news for specific symbols
            if symbols:
                # Limit to 10 symbols due to rate limit
                symbols_to_fetch = symbols[:10]
                logger.info(f"Fetching news for {len(symbols_to_fetch)} symbols...")

                for symbol in symbols_to_fetch:
                    articles = self.fetch_ticker_news(symbol, limit=5)

                    for article in articles:
                        try:
                            news_item = NewsArticle(
                                article_id=article.get('id', ''),
                                symbol=symbol,
                                published_at=datetime.fromisoformat(
                                    article.get('published_utc', '').replace('Z', '+00:00')
                                ),
                                title=article.get('title', ''),
                                author=article.get('author', ''),
                                url=article.get('article_url', ''),
                                summary=article.get('description', ''),
                                source='polygon',
                                tickers=article.get('tickers', []),
                                sentiment_score=None,
                                keywords=article.get('keywords', [])
                            )

                            # Check if exists
                            existing = session.query(NewsArticle).filter_by(
                                article_id=news_item.article_id
                            ).first()

                            if not existing:
                                session.add(news_item)
                                articles_saved += 1

                        except Exception as e:
                            logger.error(f"Error saving article for {symbol}: {e}")

            session.commit()
            logger.info(f"Saved {articles_saved} new articles to database")

        except Exception as e:
            session.rollback()
            logger.error(f"Error ingesting news: {e}")
        finally:
            session.close()

    def get_trending_from_news(self) -> List[str]:
        """
        Extract trending symbols from recent news
        """
        articles = self.fetch_market_news(limit=100)

        ticker_counts = {}
        for article in articles:
            for ticker in article.get('tickers', []):
                if ticker and not ticker.startswith('CRYPTO:') and not ticker.startswith('FOREX:'):
                    ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

        # Sort by mention count
        sorted_tickers = sorted(ticker_counts.items(), key=lambda x: x[1], reverse=True)

        # Return top 50 mentioned tickers
        trending = [ticker for ticker, count in sorted_tickers[:50]]
        logger.info(f"Top trending from Polygon news: {trending[:10]}")

        return trending


if __name__ == "__main__":
    # Test the news provider
    provider = PolygonNewsProvider()

    if provider.is_available():
        # Get trending symbols
        trending = provider.get_trending_from_news()
        print(f"Trending symbols: {trending[:20]}")

        # Ingest to database
        provider.ingest_news_to_db(symbols=trending[:5], market_news=True)
    else:
        print("Polygon API key not configured")