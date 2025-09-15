"""
Article Storage and Summarization Service
Stores full articles in MinIO/S3 and generates LLM summaries
"""

import os
import sys
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
import httpx
from minio import Minio
from minio.error import S3Error
import io

sys.path.append('/app')

logger = logging.getLogger(__name__)

class ArticleStorage:
    """
    Manages article storage in MinIO and LLM summarization
    """

    def __init__(self):
        # MinIO configuration
        self.minio_endpoint = os.getenv('MINIO_ENDPOINT', 'minio:9000')
        self.minio_access_key = os.getenv('MINIO_ACCESS_KEY', 'minioadmin')
        self.minio_secret_key = os.getenv('MINIO_SECRET_KEY', 'minioadmin')
        self.bucket_name = os.getenv('MINIO_BUCKET', 'articles')

        # LLM configuration
        self.llm_base_url = os.getenv('LLM_BASE_URL', 'http://ollama:11434')
        self.llm_model = os.getenv('LLM_SUMMARY_MODEL', 'deepseek-v2:16b')  # Use fast model for summarization

        # Initialize MinIO client
        self.minio_client = None
        try:
            self.minio_client = Minio(
                self.minio_endpoint,
                access_key=self.minio_access_key,
                secret_key=self.minio_secret_key,
                secure=False  # Set to True for HTTPS
            )

            # Create bucket if it doesn't exist
            if not self.minio_client.bucket_exists(self.bucket_name):
                self.minio_client.make_bucket(self.bucket_name)
                logger.info(f"Created MinIO bucket: {self.bucket_name}")
            else:
                logger.info(f"Using existing MinIO bucket: {self.bucket_name}")

        except Exception as e:
            logger.warning(f"MinIO not available: {e}")

    def generate_article_id(self, url: str) -> str:
        """Generate unique ID for article"""
        return hashlib.sha256(url.encode()).hexdigest()

    def store_article(self, article: Dict) -> Optional[str]:
        """
        Store article in MinIO
        Returns the object path if successful
        """
        if not self.minio_client:
            return None

        try:
            # Generate unique ID
            article_id = self.generate_article_id(article.get('url', ''))

            # Prepare article data with metadata
            article_data = {
                'id': article_id,
                'url': article.get('url', ''),
                'title': article.get('title', ''),
                'author': article.get('author', ''),
                'published': article.get('published', ''),
                'source': article.get('source', ''),
                'content': article.get('content', ''),
                'summary': article.get('summary', ''),
                'tickers': article.get('tickers', []),
                'stored_at': datetime.now(timezone.utc).isoformat()
            }

            # Convert to JSON
            json_data = json.dumps(article_data, indent=2)
            json_bytes = json_data.encode('utf-8')

            # Create object path: YYYY/MM/DD/article_id.json
            now = datetime.now(timezone.utc)
            object_path = f"{now.year:04d}/{now.month:02d}/{now.day:02d}/{article_id}.json"

            # Upload to MinIO
            self.minio_client.put_object(
                self.bucket_name,
                object_path,
                io.BytesIO(json_bytes),
                len(json_bytes),
                content_type='application/json'
            )

            logger.debug(f"Stored article {article_id} to MinIO: {object_path}")
            return object_path

        except S3Error as e:
            logger.error(f"MinIO S3 error: {e}")
        except Exception as e:
            logger.error(f"Failed to store article: {e}")

        return None

    def retrieve_article(self, article_id: str, date: Optional[datetime] = None) -> Optional[Dict]:
        """
        Retrieve article from MinIO
        """
        if not self.minio_client:
            return None

        try:
            # Build object path
            if date:
                object_path = f"{date.year:04d}/{date.month:02d}/{date.day:02d}/{article_id}.json"
            else:
                # Search for article in recent days
                from datetime import timedelta
                for days_ago in range(7):  # Look back 7 days
                    check_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
                    object_path = f"{check_date.year:04d}/{check_date.month:02d}/{check_date.day:02d}/{article_id}.json"

                    try:
                        response = self.minio_client.get_object(self.bucket_name, object_path)
                        data = json.loads(response.read().decode('utf-8'))
                        response.close()
                        return data
                    except:
                        continue

                return None

            # Get object
            response = self.minio_client.get_object(self.bucket_name, object_path)
            data = json.loads(response.read().decode('utf-8'))
            response.close()

            return data

        except Exception as e:
            logger.error(f"Failed to retrieve article {article_id}: {e}")
            return None

    async def summarize_article(self, content: str, title: str = "", max_length: int = 500) -> Optional[str]:
        """
        Use LLM to summarize article content

        Args:
            content: Full article text
            title: Article title for context
            max_length: Maximum summary length

        Returns:
            Summary text or None
        """
        if not content or len(content) < 100:
            return content  # Too short to summarize

        try:
            prompt = f"""Summarize this financial news article in 2-3 sentences. Focus on:
1. What happened (main event/news)
2. Impact on stock price or market
3. Any forward-looking statements or guidance

Title: {title}

Article:
{content[:3000]}  # Limit to first 3000 chars to fit in context

Summary:"""

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.llm_base_url}/api/generate",
                    json={
                        "model": self.llm_model,
                        "prompt": prompt,
                        "temperature": 0.3,  # Lower temp for factual summarization
                        "max_tokens": max_length,
                        "stream": False
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    result = response.json()
                    summary = result.get('response', '').strip()

                    # Clean up the summary
                    if summary:
                        # Remove any prompt leakage
                        if "Summary:" in summary:
                            summary = summary.split("Summary:")[-1].strip()

                        return summary[:max_length]

        except Exception as e:
            logger.error(f"LLM summarization failed: {e}")

        return None

    async def extract_key_facts(self, content: str) -> Dict[str, any]:
        """
        Extract structured information from article
        """
        try:
            prompt = f"""Extract key facts from this article as JSON:
{{
    "tickers": ["SYMBOL1", "SYMBOL2"],
    "sentiment": "positive|negative|neutral",
    "price_impact": "up|down|neutral",
    "event_type": "earnings|merger|product|regulatory|other",
    "key_numbers": ["revenue: $1.2B", "EPS: $0.45"]
}}

Article: {content[:2000]}

JSON:"""

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.llm_base_url}/api/generate",
                    json={
                        "model": self.llm_model,
                        "prompt": prompt,
                        "temperature": 0.1,  # Very low for structured extraction
                        "stream": False,
                        "format": "json"
                    },
                    timeout=30.0
                )

                if response.status_code == 200:
                    result = response.json()
                    response_text = result.get('response', '{}')

                    # Parse JSON
                    try:
                        facts = json.loads(response_text)
                        return facts
                    except json.JSONDecodeError:
                        # Try to extract JSON from response
                        import re
                        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                        if json_match:
                            facts = json.loads(json_match.group())
                            return facts

        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")

        return {}

    def list_articles(self, date: Optional[datetime] = None) -> List[str]:
        """
        List all article IDs for a given date
        """
        if not self.minio_client:
            return []

        articles = []
        try:
            if not date:
                date = datetime.now(timezone.utc)

            prefix = f"{date.year:04d}/{date.month:02d}/{date.day:02d}/"

            objects = self.minio_client.list_objects(
                self.bucket_name,
                prefix=prefix,
                recursive=True
            )

            for obj in objects:
                # Extract article ID from path
                article_id = obj.object_name.split('/')[-1].replace('.json', '')
                articles.append(article_id)

        except Exception as e:
            logger.error(f"Failed to list articles: {e}")

        return articles

    async def process_and_store(self, article: Dict) -> Dict[str, Any]:
        """
        Process article: store full text and generate summary

        Returns:
            {
                'article_id': str,
                'storage_path': str,
                'summary': str,
                'key_facts': dict
            }
        """
        article_id = self.generate_article_id(article.get('url', ''))

        # Get full article content if available
        content = article.get('content') or article.get('summary', '')
        title = article.get('title', '')

        # Generate summary if we have content
        summary = None
        key_facts = {}

        if content and len(content) > 200:
            summary = await self.summarize_article(content, title)
            key_facts = await self.extract_key_facts(content)

            # Update article with summary and facts
            article['llm_summary'] = summary
            article['key_facts'] = key_facts

        # Store in MinIO
        storage_path = self.store_article(article)

        return {
            'article_id': article_id,
            'storage_path': storage_path,
            'summary': summary or article.get('summary', ''),
            'key_facts': key_facts,
            'tickers': key_facts.get('tickers', article.get('tickers', []))
        }


# Synchronous wrapper
class ArticleStorageSync:
    """Synchronous wrapper for article storage"""

    def __init__(self):
        self.storage = ArticleStorage()

    def process_articles(self, articles: List[Dict]) -> List[Dict]:
        """Process multiple articles synchronously"""
        import asyncio

        async def process_all():
            results = []
            for article in articles:
                result = await self.storage.process_and_store(article)
                results.append(result)
            return results

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(process_all())
        finally:
            loop.close()


if __name__ == "__main__":
    # Test the storage system
    import asyncio

    async def test():
        storage = ArticleStorage()

        # Test article
        article = {
            'url': 'https://example.com/article1',
            'title': 'Apple Reports Record Q4 Earnings',
            'content': 'Apple Inc. reported record fourth-quarter earnings...',
            'tickers': ['AAPL'],
            'source': 'Reuters'
        }

        result = await storage.process_and_store(article)
        print(f"Processed article: {result}")

    asyncio.run(test())