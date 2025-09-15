"""
Multi-Source News Aggregator
Discovers trending stocks from FREE news sources (RSS, APIs)
Saves Alpha Vantage MCP for actual data fetching
"""

import os
import sys
import logging
import hashlib
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Set, Optional, Any
from collections import defaultdict
import re
import json
from bs4 import BeautifulSoup

sys.path.append('/app')
from libs.database import get_session, NewsArticle
from libs.article_storage import ArticleStorage

logger = logging.getLogger(__name__)

class NewsAggregator:
    """
    Aggregates news from multiple FREE sources
    No API limits - just RSS feeds and public endpoints
    """

    def __init__(self):
        # Initialize article storage for MinIO and LLM summarization
        self.article_storage = ArticleStorage()

        # Initialize RSS feed manager with config-driven feeds
        try:
            from libs.rss_manager import RSSFeedManager
            self.rss_manager = RSSFeedManager()
            logger.info("RSS Manager initialized for config-driven feeds")
        except Exception as e:
            logger.warning(f"RSS Manager initialization failed: {e}")
            self.rss_manager = None

        # Keep minimal fallback feeds if RSS manager fails
        self.fallback_feeds = {
            'marketwatch': [
                'http://feeds.marketwatch.com/marketwatch/topstories/'
            ],
            'yahoo': [
                'https://finance.yahoo.com/rss/topstories'
            ]
        }

        # Common ticker patterns
        self.ticker_pattern = re.compile(r'\b([A-Z]{1,5})\b(?:\s|,|;|\.|\)|:)')

        # Exchange prefixes to remove
        self.exchange_prefixes = ['NYSE:', 'NASDAQ:', 'AMEX:', 'CRYPTO:', 'FOREX:']

    def fetch_rss_feed(self, url: str) -> List[Dict]:
        """
        Fetch and parse RSS feed
        """
        articles = []
        try:
            # Fetch with timeout to prevent hanging
            response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if response.status_code != 200:
                logger.warning(f"RSS feed {url} returned status {response.status_code}")
                return articles

            feed = feedparser.parse(response.text)

            for entry in feed.entries[:20]:  # Limit to recent 20 per feed
                article = {
                    'title': entry.get('title', ''),
                    'url': entry.get('link', ''),
                    'summary': entry.get('summary', ''),
                    'published': entry.get('published_parsed', None),
                    'source': feed.feed.get('title', url.split('/')[2])
                }

                # Extract tickers from title and summary
                text = f"{article['title']} {article['summary']}"
                tickers = self.extract_tickers(text)
                article['tickers'] = list(tickers)

                articles.append(article)

        except Exception as e:
            logger.warning(f"Failed to fetch RSS {url}: {e}")

        return articles

    def extract_tickers(self, text: str) -> Set[str]:
        """
        Extract stock tickers from text
        """
        tickers = set()

        # Find all potential tickers
        matches = self.ticker_pattern.findall(text)

        for match in matches:
            # Clean up ticker
            ticker = match.strip()

            # Skip common words and acronyms that match pattern but aren't stocks
            skip_words = {
                'I', 'A', 'THE', 'NYSE', 'NASDAQ', 'IPO', 'CEO', 'CFO', 'SEC', 'FDA',
                'AI', 'EV', 'US', 'USA', 'UK', 'EU', 'UN', 'NATO', 'WHO', 'CDC',
                'FBI', 'CIA', 'NSA', 'IRS', 'IRA', 'NYC', 'LA', 'SF', 'DC',
                'GDP', 'CPI', 'PMI', 'ETF', 'VIP', 'VE', 'BOE', 'BOJ', 'ECB',
                'OPEC', 'FOMC', 'ISI', 'NAV', 'PE', 'SA', 'NOT', 'FIFA', 'UFC',
                'NFL', 'NBA', 'MLB', 'NHL', 'NCAA', 'GOP', 'DNC', 'CNN', 'BBC',
                'NBC', 'ABC', 'CBS', 'FOX', 'CNBC', 'MSNBC', 'NPR', 'WSJ', 'NYT',
                'FT', 'WP', 'AP', 'AFP', 'REIT', 'REG', 'ULTY', 'GAC', 'ACA',
                'VC', 'PC', 'IT', 'HR', 'PR', 'VP', 'EVP', 'SVP', 'COO', 'CTO',
                'CIO', 'CMO', 'CSO', 'P', 'S', 'U', 'X', 'E', 'K', 'O'
            }
            if ticker in skip_words:
                continue

            # Must be 1-5 uppercase letters
            if len(ticker) >= 1 and len(ticker) <= 5 and ticker.isalpha() and ticker.isupper():
                tickers.add(ticker)

        return tickers

    def fetch_sec_filings(self) -> List[Dict]:
        """
        Fetch recent SEC filings using official EDGAR API
        """
        filings = []

        try:
            from libs.sec_edgar import SECEdgarClient
            sec_client = SECEdgarClient()

            # Get top symbols from current mentions
            top_symbols = []
            if hasattr(self, 'symbol_mentions'):
                # Get symbols we've already seen in news
                sorted_symbols = sorted(self.symbol_mentions.items(),
                                      key=lambda x: x[1], reverse=True)
                top_symbols = [s[0] for s in sorted_symbols[:30]
                             if len(s[0]) >= 2 and len(s[0]) <= 5]

            # If no symbols yet, use some major companies
            if not top_symbols:
                top_symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA', 'META',
                             'NVDA', 'JPM', 'BAC', 'WMT']

            # Get recent filings for these symbols (last 48 hours)
            recent_filings = sec_client.get_recent_filings_for_watchlist(
                top_symbols, hours_back=48
            )

            # Convert to article format
            for ticker, ticker_filings in recent_filings.items():
                for f in ticker_filings[:3]:  # Limit to 3 most recent per company
                    title = f"{ticker}: {f['form']} Filed {f['filing_date']}"

                    # Add 8-K items to title if available
                    if f['form'] == '8-K' and f.get('items'):
                        items = sec_client.extract_8k_items(f)
                        if items:
                            title += f" - {items[0]}"  # Show first item

                    filing = {
                        'title': title,
                        'url': f.get('url', ''),
                        'summary': f"SEC {f['form']} filing",
                        'published': None,  # Will use current time
                        'source': 'SEC EDGAR',
                        'tickers': [ticker]
                    }

                    filings.append(filing)

            logger.info(f"Found {len(filings)} SEC filings via EDGAR API")

        except Exception as e:
            logger.warning(f"Failed to fetch SEC filings via API: {e}")

        return filings

    def fetch_yahoo_trending(self) -> List[str]:
        """
        Fetch trending tickers from Yahoo Finance
        """
        trending = []

        try:
            # Yahoo trending tickers endpoint (no auth required)
            url = 'https://finance.yahoo.com/trending-tickers'
            headers = {'User-Agent': 'Mozilla/5.0'}

            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                # Parse HTML to find trending tickers
                soup = BeautifulSoup(response.text, 'html.parser')

                # Look for ticker symbols in the page
                for link in soup.find_all('a', href=True):
                    if '/quote/' in link['href']:
                        ticker = link['href'].split('/quote/')[-1].split('?')[0]
                        if ticker and ticker.isalpha() and ticker.isupper():
                            trending.append(ticker)

        except Exception as e:
            logger.warning(f"Failed to fetch Yahoo trending: {e}")

        return trending[:20]  # Top 20 trending

    def fetch_finviz_news(self) -> List[Dict]:
        """
        Fetch news from FinViz (no API needed)
        """
        articles = []

        try:
            url = 'https://finviz.com/news.ashx'
            headers = {'User-Agent': 'Mozilla/5.0'}

            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')

                # Find news table
                news_table = soup.find('table', {'id': 'news-table'})
                if news_table:
                    for row in news_table.find_all('tr')[:30]:
                        link = row.find('a')
                        if link:
                            title = link.text
                            article = {
                                'title': title,
                                'url': link.get('href', ''),
                                'source': 'FinViz',
                                'tickers': list(self.extract_tickers(title))
                            }
                            articles.append(article)

        except Exception as e:
            logger.warning(f"Failed to fetch FinViz news: {e}")

        return articles

    def aggregate_all_news(self) -> Dict[str, Any]:
        """
        Aggregate news from all sources
        Returns symbol counts and articles
        """
        all_articles = []
        symbol_mentions = defaultdict(int)
        symbol_sentiment = defaultdict(list)

        # Use RSS Manager if available for config-driven feeds
        if self.rss_manager:
            logger.info("Fetching news from RSS Manager (config-driven)...")

            # Get initial symbols from any previous sources
            initial_symbols = list(symbol_mentions.keys())[:30] if symbol_mentions else None

            # Fetch all feeds including ticker-specific
            rss_results = self.rss_manager.fetch_all_feeds(symbols=initial_symbols)

            # Process RSS Manager results
            for article in rss_results['articles']:
                all_articles.append(article)

                # Count ticker mentions with weights
                for ticker in article.get('tickers', []):
                    weight = article.get('weight', 1.0)
                    symbol_mentions[ticker] += weight

            # Add symbol mentions from RSS Manager
            for ticker, score in rss_results['symbol_mentions'].items():
                symbol_mentions[ticker] += score

            logger.info(f"RSS Manager: {rss_results['stats']['new_articles']} articles from {rss_results['stats']['successful_feeds']} feeds")
        else:
            # Fallback to simple RSS fetching
            logger.info("Using fallback RSS feeds...")
            for source, urls in self.fallback_feeds.items():
                for url in urls:
                    articles = self.fetch_rss_feed(url)
                    all_articles.extend(articles)

                    for article in articles:
                        for ticker in article.get('tickers', []):
                            symbol_mentions[ticker] += 1

        # Store symbol_mentions as instance variable for SEC fetching
        # (do this AFTER RSS feeds so we have symbols to lookup)
        self.symbol_mentions = symbol_mentions

        # Fetch SEC filings using discovered symbols
        logger.info("Fetching SEC filings...")
        sec_filings = self.fetch_sec_filings()
        all_articles.extend(sec_filings)

        for filing in sec_filings:
            for ticker in filing.get('tickers', []):
                symbol_mentions[ticker] += 2  # Weight SEC filings higher

        # Fetch Yahoo trending
        logger.info("Fetching Yahoo trending...")
        trending = self.fetch_yahoo_trending()
        for ticker in trending:
            symbol_mentions[ticker] += 3  # Weight trending higher

        # Fetch FinViz news
        logger.info("Fetching FinViz news...")
        finviz_articles = self.fetch_finviz_news()
        all_articles.extend(finviz_articles)

        for article in finviz_articles:
            for ticker in article.get('tickers', []):
                symbol_mentions[ticker] += 1

        # Sort symbols by mention count
        sorted_symbols = sorted(symbol_mentions.items(), key=lambda x: x[1], reverse=True)

        # Filter out likely non-stock symbols
        valid_symbols = []
        for symbol, count in sorted_symbols:
            # More strict filtering - at least 2 chars and not in skip list
            if len(symbol) >= 2 and len(symbol) <= 5:
                valid_symbols.append((symbol, count))

        logger.info(f"Aggregated {len(all_articles)} articles, found {len(valid_symbols)} valid symbols")
        logger.info(f"Top symbols: {valid_symbols[:10]}")

        return {
            'articles': all_articles,
            'symbol_mentions': dict(symbol_mentions),
            'top_symbols': [s[0] for s in valid_symbols[:200]],  # Top 200 valid symbols
            'article_count': len(all_articles)
        }

    def save_to_database_sync(self, articles: List[Dict]):
        """
        Save articles to database (synchronous version)
        """
        session = get_session()
        saved_db = 0

        try:
            for article in articles[:50]:  # Limit to 50 articles for now
                try:
                    # Generate unique ID from URL
                    article_id = hashlib.md5(article.get('url', '').encode()).hexdigest()

                    # Parse published date
                    published_at = datetime.now(timezone.utc)
                    if article.get('published'):
                        try:
                            published_at = datetime(*article['published'][:6], tzinfo=timezone.utc)
                        except:
                            pass

                    # Get primary ticker
                    tickers = article.get('tickers', [])
                    symbol = tickers[0] if tickers else 'MARKET'

                    news_item = NewsArticle(
                        article_id=article_id,
                        symbol=symbol,
                        published_at=published_at,
                        title=article.get('title', '')[:500],
                        author=article.get('author', ''),
                        url=article.get('url', '')[:500],
                        summary=article.get('summary', '')[:1000],
                        source=article.get('source', 'RSS'),
                        tickers=tickers,
                        sentiment_score=None,  # Skip LLM for now
                        keywords=[]
                    )

                    # Check if exists
                    existing = session.query(NewsArticle).filter_by(
                        article_id=article_id
                    ).first()

                    if not existing:
                        session.add(news_item)
                        saved_db += 1

                except Exception as e:
                    logger.debug(f"Skipped article: {e}")

            session.commit()
            logger.info(f"Saved {saved_db} articles to DB")

        except Exception as e:
            session.rollback()
            logger.error(f"Database error: {e}")
        finally:
            session.close()

    def build_watchlist(self) -> List[str]:
        """
        Build watchlist from aggregated news
        """
        # Aggregate all news
        news_data = self.aggregate_all_news()

        # Save articles to database and MinIO with LLM processing
        self.save_to_database_sync(news_data['articles'])

        # Return top symbols
        return news_data['top_symbols']


if __name__ == "__main__":
    aggregator = NewsAggregator()
    watchlist = aggregator.build_watchlist()
    print(f"Generated watchlist with {len(watchlist)} symbols")
    print(f"Top 20: {watchlist[:20]}")