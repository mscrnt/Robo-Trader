"""
RSS Feed Manager with ETag/Last-Modified Support
Config-driven, idempotent, and rate-limited
"""

import os
import sys
import yaml
import json
import logging
import hashlib
import requests
import feedparser
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

sys.path.append('/app')

logger = logging.getLogger(__name__)


class RSSFeedManager:
    """
    Manages RSS feeds with caching, deduplication, and rate limiting
    """

    def __init__(self, config_path: str = '/app/configs/rss_feeds.yaml'):
        self.config = self._load_config(config_path)
        self.last_request_time = 0

        # Use database for state instead of JSON file
        self._load_state_from_db()

        # Initialize LLM for ticker extraction if available
        self.llm_client = None
        self.llm_model = os.getenv('LLM_SUMMARY_MODEL', 'deepseek-v2:16b')
        try:
            import httpx
            base_url = os.getenv('LLM_BASE_URL', 'http://ollama:11434')
            self.llm_client = httpx.Client(base_url=base_url, timeout=30.0)
            # Check if LLM is available
            response = self.llm_client.get('/api/tags', timeout=2.0)
            if response.status_code == 200:
                logger.info("LLM available for ticker extraction")
            else:
                self.llm_client = None
        except Exception as e:
            logger.info(f"LLM not available, using regex extraction: {e}")
            self.llm_client = None

        logger.info(f"RSS Manager initialized with {len(self.config['feeds'])} feeds")

    def _load_config(self, path: str) -> Dict:
        """Load RSS feed configuration"""
        try:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load RSS config: {e}")

        # Return default config if file doesn't exist
        return {
            'feeds': [],
            'ticker_feeds': [],
            'polling': {
                'interval_minutes': 15,
                'timeout_seconds': 20,
                'max_retries': 3,
                'backoff_factor': 2.0
            },
            'rate_limits': {
                'requests_per_minute': 30,
                'cooldown_seconds': 2
            },
            'deduplication': {
                'hash_fields': ['title', 'link'],
                'ttl_hours': 48
            }
        }

    def _load_state_from_db(self):
        """Load feed state from database"""
        try:
            from libs.database import get_session, RSSFeedState, SeenArticle
            session = get_session()

            # Load feed states
            self.feed_state = {}
            feed_states = session.query(RSSFeedState).all()
            for state in feed_states:
                self.feed_state[state.feed_name] = {
                    'etag': state.etag,
                    'last_modified': state.last_modified,
                    'last_fetch': state.last_fetch
                }

            # Load seen articles
            self.seen_articles = set()
            seen = session.query(SeenArticle).all()
            for article in seen:
                self.seen_articles.add(article.article_hash)

            session.close()
            logger.debug(f"Loaded RSS state from database: {len(self.feed_state)} feeds, {len(self.seen_articles)} seen articles")
        except Exception as e:
            logger.debug(f"Could not load RSS state from database: {e}")
            self.feed_state = {}
            self.seen_articles = set()

    def _save_state_to_db(self):
        """Save feed state to database"""
        try:
            from libs.database import get_session, RSSFeedState, SeenArticle
            session = get_session()

            # Save feed states
            for feed_name, state in self.feed_state.items():
                feed_state = session.query(RSSFeedState).filter_by(feed_name=feed_name).first()
                if not feed_state:
                    feed_state = RSSFeedState(feed_name=feed_name)
                    session.add(feed_state)

                feed_state.etag = state.get('etag')
                feed_state.last_modified = state.get('last_modified')
                feed_state.last_fetch = datetime.now(timezone.utc)

            # Save new seen articles (only add new ones)
            for article_hash in self.seen_articles:
                if not session.query(SeenArticle).filter_by(article_hash=article_hash).first():
                    session.add(SeenArticle(article_hash=article_hash))

            session.commit()
            session.close()
            logger.debug("Saved RSS state to database")
        except Exception as e:
            logger.error(f"Failed to save RSS state to database: {e}")

    def _clean_old_articles(self):
        """Remove old article hashes beyond TTL"""
        # For simplicity, just keep the last 10k articles
        if len(self.seen_articles) > 10000:
            self.seen_articles = set(list(self.seen_articles)[-10000:])

    def _rate_limit(self):
        """Enforce rate limiting"""
        cooldown = self.config['rate_limits']['cooldown_seconds']
        elapsed = time.time() - self.last_request_time
        if elapsed < cooldown:
            time.sleep(cooldown - elapsed)
        self.last_request_time = time.time()

    def _get_article_hash(self, article: Dict) -> str:
        """Generate unique hash for article deduplication"""
        hash_fields = self.config['deduplication']['hash_fields']
        hash_str = ""
        for field in hash_fields:
            hash_str += str(article.get(field, ''))
        return hashlib.md5(hash_str.encode()).hexdigest()

    def fetch_feed(self, feed_config: Dict) -> List[Dict]:
        """
        Fetch a single feed with conditional GET support
        Returns list of new articles
        """
        url = feed_config['url']
        name = feed_config['name']

        # Rate limiting
        self._rate_limit()

        # Get stored state for this feed
        state = self.feed_state.get(name, {})
        etag = state.get('etag')
        last_modified = state.get('last_modified')

        # Prepare headers for conditional GET
        headers = {
            'User-Agent': 'RoboTrader/1.0 (Financial News Aggregator)'
        }
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified

        try:
            # Make request with timeout
            timeout = self.config['polling']['timeout_seconds']
            response = requests.get(url, headers=headers, timeout=timeout)

            # Handle 304 Not Modified
            if response.status_code == 304:
                logger.debug(f"Feed {name} not modified since last fetch")
                return []

            # Handle errors
            if response.status_code != 200:
                logger.warning(f"Feed {name} returned status {response.status_code}")
                return []

            # Update state
            new_state = {
                'etag': response.headers.get('ETag'),
                'last_modified': response.headers.get('Last-Modified'),
                'last_fetch': datetime.now(timezone.utc).isoformat()
            }
            self.feed_state[name] = new_state

            # Parse feed
            feed = feedparser.parse(response.content)

            # Process entries
            articles = []
            for entry in feed.entries[:50]:  # Limit entries per feed
                # Get the best available content - many RSS feeds don't have summaries
                summary = entry.get('summary', '')
                if not summary:
                    # Try other fields that might have content
                    summary = entry.get('description', '')
                if not summary and hasattr(entry, 'content'):
                    # Some feeds put content in 'content' field
                    try:
                        summary = entry.content[0].value if entry.content else ''
                    except:
                        summary = ''

                article = {
                    'title': entry.get('title', ''),
                    'link': entry.get('link', ''),
                    'summary': summary,
                    'published': entry.get('published_parsed'),
                    'guid': entry.get('id', entry.get('link', '')),
                    'categories': [tag.term for tag in entry.get('tags', [])],
                    'source': feed_config['name'],
                    'weight': feed_config['weight'],
                    'category': feed_config.get('category', 'news')
                }

                # Check deduplication
                article_hash = self._get_article_hash(article)
                if article_hash not in self.seen_articles:
                    self.seen_articles.add(article_hash)
                    articles.append(article)

            logger.info(f"Fetched {len(articles)} new articles from {name}")
            return articles

        except requests.Timeout:
            logger.warning(f"Feed {name} timed out")
            return []
        except Exception as e:
            logger.error(f"Error fetching feed {name}: {e}")
            return []

    def fetch_ticker_feed(self, template: str, symbol: str, weight: float) -> List[Dict]:
        """
        Fetch feed for a specific ticker
        """
        url = template.replace('{symbol}', symbol.upper())
        feed_config = {
            'name': f'ticker_{symbol}',
            'url': url,
            'weight': weight,
            'category': 'ticker_specific'
        }

        articles = self.fetch_feed(feed_config)

        # Tag all articles with the ticker
        for article in articles:
            article['tickers'] = [symbol.upper()]

        return articles

    def fetch_all_feeds(self, symbols: List[str] = None) -> Dict[str, Any]:
        """
        Fetch all configured feeds including ticker-specific
        Returns aggregated results
        """
        all_articles = []
        symbol_mentions = defaultdict(int)
        fetch_stats = {
            'total_feeds': 0,
            'successful_feeds': 0,
            'new_articles': 0,
            'errors': []
        }

        # Fetch general feeds
        for feed_config in self.config['feeds']:
            try:
                articles = self.fetch_feed(feed_config)
                all_articles.extend(articles)
                fetch_stats['successful_feeds'] += 1

            except Exception as e:
                fetch_stats['errors'].append(f"{feed_config['name']}: {e}")

            fetch_stats['total_feeds'] += 1

        # Fetch ticker-specific feeds
        if symbols and self.config.get('ticker_feeds'):
            for ticker_feed_config in self.config['ticker_feeds']:
                template = ticker_feed_config['url_template']
                weight = ticker_feed_config['weight']
                max_symbols = ticker_feed_config.get('max_symbols', 30)

                # Limit symbols to avoid rate limits
                for symbol in symbols[:max_symbols]:
                    try:
                        articles = self.fetch_ticker_feed(template, symbol, weight)
                        all_articles.extend(articles)
                        fetch_stats['successful_feeds'] += 1

                        # Count mentions
                        for article in articles:
                            symbol_mentions[symbol] += article['weight']

                    except Exception as e:
                        fetch_stats['errors'].append(f"ticker_{symbol}: {e}")

                    fetch_stats['total_feeds'] += 1

        # Save state after all fetches
        self._save_state_to_db()

        fetch_stats['new_articles'] = len(all_articles)

        # Process articles for ticker extraction with proper batching
        logger.info(f"Processing {len(all_articles)} articles for ticker extraction...")

        # Use regex extraction for first pass
        for article in all_articles:
            text = f"{article['title']} {article['summary']}"
            article['tickers'] = self._extract_tickers_regex(text)
            for ticker in article['tickers']:
                symbol_mentions[ticker] += article['weight']

        # Optionally enhance top articles with LLM (limited to avoid overload)
        if self.llm_client and len(all_articles) > 0:
            # Only process top 5 articles with LLM
            for i, article in enumerate(all_articles[:5]):
                if i > 0:
                    time.sleep(2)  # 2 second delay between LLM calls
                text = f"{article['title']} {article['summary']}"
                llm_tickers = self._extract_tickers_llm(text)
                if llm_tickers:
                    # Merge with regex results
                    existing = set(article.get('tickers', []))
                    article['tickers'] = list(existing.union(set(llm_tickers)))
                    # Update mentions
                    for ticker in llm_tickers:
                        if ticker not in existing:
                            symbol_mentions[ticker] += article['weight']

        # Sort symbols by weighted mentions
        top_symbols = sorted(symbol_mentions.items(), key=lambda x: x[1], reverse=True)

        logger.info(f"RSS fetch complete: {fetch_stats['new_articles']} new articles from {fetch_stats['successful_feeds']}/{fetch_stats['total_feeds']} feeds")

        return {
            'articles': all_articles,
            'symbol_mentions': dict(symbol_mentions),
            'top_symbols': [s[0] for s in top_symbols],
            'stats': fetch_stats
        }

    def _extract_tickers(self, text: str) -> List[str]:
        """
        Extract stock tickers from text using LLM if available, fallback to regex
        """
        # Disabled LLM extraction to avoid overloading - using regex only for now
        # TODO: Implement proper batching and rate limiting for LLM extraction
        return self._extract_tickers_regex(text)

    def _extract_tickers_regex(self, text: str) -> List[str]:
        """
        Extract tickers using regex patterns only
        """
        import re
        tickers = set()

        # Look for $TICKER patterns
        cashtag_pattern = re.compile(r'\$([A-Z]{1,5})\b')
        for match in cashtag_pattern.findall(text):
            tickers.add(match)

        # Look for common patterns like "Apple (AAPL)" or "AAPL:"
        paren_pattern = re.compile(r'\(([A-Z]{1,5})\)')
        for match in paren_pattern.findall(text):
            tickers.add(match)

        # Look for "Ticker: XYZ" patterns
        ticker_pattern = re.compile(r'(?:ticker|symbol):\s*([A-Z]{1,5})\b', re.I)
        for match in ticker_pattern.findall(text):
            tickers.add(match.upper())

        # Filter out common false positives
        false_positives = {'I', 'A', 'US', 'UK', 'EU', 'CEO', 'CFO', 'IPO', 'ETF', 'AI', 'IT'}
        tickers = {t for t in tickers if t not in false_positives}

        return list(tickers)

    def _extract_tickers_llm(self, text: str) -> List[str]:
        """
        Extract tickers using LLM for better accuracy
        """
        if not text or len(text) < 10:
            return []

        # Truncate very long text
        if len(text) > 1500:
            text = text[:1500] + "..."

        # Log the text we're processing
        logger.info(f"Extracting tickers from text ({len(text)} chars): {text[:200]}...")

        prompt = f"""Extract stock ticker symbols from this news text.
Return ONLY the ticker symbols as a JSON array, nothing else.
Include only valid US stock symbols (1-5 uppercase letters).
Do not include exchange names, just the symbols.
If no tickers are found, return an empty array [].

Text: {text}

Response (JSON array only):"""

        logger.info(f"Sending prompt to LLM model {self.llm_model}")

        try:
            response = self.llm_client.post(
                '/api/generate',
                json={
                    'model': self.llm_model,
                    'prompt': prompt,
                    'stream': False,
                    'temperature': 0.1,
                    'max_tokens': 100
                },
                timeout=10.0  # Increased timeout
            )

            if response.status_code == 200:
                result = response.json()
                llm_output = result.get('response', '').strip()

                # Log the full LLM output for debugging
                logger.info(f"LLM raw response length: {len(llm_output)} chars")
                logger.info(f"LLM response preview: {llm_output[:300]}")

                # Try to parse as JSON array
                import json
                if '[' in llm_output and ']' in llm_output:
                    start = llm_output.index('[')
                    end = llm_output.rindex(']') + 1
                    json_str = llm_output[start:end]
                    logger.info(f"Extracted JSON: {json_str}")
                    try:
                        tickers = json.loads(json_str)
                        logger.info(f"Parsed tickers: {tickers}")

                        # Validate tickers
                        valid_tickers = []
                        for ticker in tickers[:10]:  # Max 10 tickers per article
                            if isinstance(ticker, str) and 1 <= len(ticker) <= 5 and ticker.isalpha() and ticker.isupper():
                                valid_tickers.append(ticker)

                        if valid_tickers:
                            logger.info(f"LLM extracted valid tickers: {valid_tickers}")
                        else:
                            logger.warning(f"No valid tickers from: {tickers}")
                        return valid_tickers
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse LLM JSON: {e}, json_str was: {json_str[:100]}")
                        return []
                else:
                    logger.warning(f"No JSON array found in LLM response: {llm_output[:100]}")
                    return []
            else:
                logger.error(f"LLM request failed with status {response.status_code}")

        except Exception as e:
            logger.debug(f"LLM extraction failed, using regex: {e}")

        return []

    def _warm_up_model(self, model_name: str):
        """Warm up an LLM model by loading it into memory"""
        if not self.llm_client:
            return

        try:
            logger.info(f"Warming up model: {model_name}...")
            # Send a simple prompt to load it into memory
            response = self.llm_client.post(
                '/api/generate',
                json={
                    'model': model_name,
                    'prompt': 'Extract tickers: Apple reports earnings. Response:',
                    'stream': False,
                    'temperature': 0.1,
                    'max_tokens': 10
                },
                timeout=60.0  # Allow time for model loading
            )

            if response.status_code == 200:
                logger.info(f"Model {model_name} warmed up successfully")
            else:
                logger.warning(f"Failed to warm up {model_name}: {response.status_code}")

        except Exception as e:
            logger.warning(f"Error warming up {model_name}: {e}")


def test_rss_manager():
    """Test RSS feed manager"""
    manager = RSSFeedManager()

    # Test with a few symbols
    test_symbols = ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA']

    print("Fetching all RSS feeds...")
    results = manager.fetch_all_feeds(symbols=test_symbols)

    print(f"\nFetch Statistics:")
    print(f"  Total feeds: {results['stats']['total_feeds']}")
    print(f"  Successful: {results['stats']['successful_feeds']}")
    print(f"  New articles: {results['stats']['new_articles']}")

    print(f"\nTop mentioned symbols:")
    for symbol, score in list(results['symbol_mentions'].items())[:10]:
        print(f"  {symbol}: {score:.2f}")

    print(f"\nSample articles:")
    for article in results['articles'][:5]:
        print(f"  [{article['source']}] {article['title'][:80]}")
        if article.get('tickers'):
            print(f"    Tickers: {', '.join(article['tickers'])}")


if __name__ == "__main__":
    test_rss_manager()