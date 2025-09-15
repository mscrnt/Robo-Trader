"""
LLM News Signal Copilot
Prepares compact JSON packets and gets ranked trading signals from LLM
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field, validator
import httpx

sys.path.append('/app')
from libs.database import get_session, NewsArticle, Signal, PriceData

logger = logging.getLogger(__name__)

# Pydantic models for strict validation
class NewsItem(BaseModel):
    ts: str
    src: str
    head: str = Field(max_length=120)
    sent: float = Field(ge=-1, le=1)
    novelty: float = Field(ge=0, le=1)
    etype: Optional[str] = None

class TickerPacket(BaseModel):
    ticker: str
    pre_score: float = Field(ge=-1, le=1)
    features: Dict[str, float]
    news: List[NewsItem]

class LLMRequest(BaseModel):
    as_of: str
    account: Dict[str, float]
    risk_limits: Dict[str, float]
    universe: List[TickerPacket]
    constraints: Optional[Dict[str, Any]] = None

class LLMSignal(BaseModel):
    ticker: str
    action: str = Field(pattern="^(BUY|AVOID|WATCH)$")
    llm_news_score: float = Field(ge=-1, le=1)
    confidence: float = Field(ge=0, le=1)
    catalyst_window_days: Optional[int] = Field(ge=0, le=30)
    rationale: str = Field(max_length=200)

class LLMResponse(BaseModel):
    as_of: str
    ranked: List[LLMSignal]

    @validator('ranked')
    def validate_ranked(cls, v):
        if not v:
            raise ValueError("ranked list cannot be empty")
        return v

class LLMCopilot:
    """LLM-powered news signal copilot"""

    def __init__(self):
        self.llm_base_url = os.getenv('LLM_BASE_URL', 'http://ollama:11434')
        self.summary_model = os.getenv('LLM_SUMMARY_MODEL', 'deepseek-v2:16b')
        self.selector_model = os.getenv('LLM_SELECTOR_MODEL', 'deepseek-r1:32b')
        self.llm_api_key = os.getenv('LLM_API_KEY', '')
        self.client = httpx.Client(base_url=self.llm_base_url, timeout=90.0)
        self.models_warmed = {'summary': False, 'selector': False}

        # Weights for final score
        self.weight_quant = float(os.getenv('WEIGHT_QUANT', '0.7'))
        self.weight_llm = float(os.getenv('WEIGHT_LLM', '0.3'))

        # Warm up ONLY the summary model on startup
        logger.info("Warming up summary model on startup...")
        self._warm_up_model('summary')

        # Token budget management
        self.max_tickers = 30
        self.max_news_per_ticker = 3  # Reduced for efficiency

        self.system_prompt = """You are the News Signal Selector. Read structured market/news context and rank tickers to BUY/AVOID/WATCH for the next session. You do not size positions or place orders; sizing and risk are handled elsewhere. Prefer catalysts (guidance changes, 8-K, earnings) and consistent news/factor alignment.

IMPORTANT: Return ONLY valid JSON matching the exact schema provided. No explanations or text outside the JSON."""

    def prepare_packet(self,
                      account_data: Dict[str, float],
                      universe_scores: Dict[str, Dict[str, Any]],
                      news_data: Dict[str, List[Dict]],
                      top_n: int = 30) -> LLMRequest:
        """
        Prepare compact JSON packet for LLM

        Args:
            account_data: Account equity, cash, exposure
            universe_scores: Pre-calculated factor scores per ticker
            news_data: Summarized news per ticker
            top_n: Number of top tickers to include
        """

        # Filter and rank by pre_score
        ranked_tickers = sorted(
            universe_scores.items(),
            key=lambda x: abs(x[1].get('composite_score', 0)),
            reverse=True
        )[:top_n]

        universe = []
        for ticker, scores in ranked_tickers:
            # Prepare features
            features = {
                'mom_12_1': scores.get('momentum', 0),
                'rsi_14': scores.get('rsi', 50),
                'macd_hist': scores.get('macd_histogram', 0),
                'gap_vol': scores.get('volume_ratio', 1.0),
                'earn_win': scores.get('earnings_surprise', False)
            }

            # Prepare news (max 5 items, truncate headlines)
            news_items = []
            ticker_news = news_data.get(ticker, [])[:self.max_news_per_ticker]

            for article in ticker_news:
                news_items.append(NewsItem(
                    ts=article.get('published_at', datetime.now(timezone.utc).isoformat()),
                    src=article.get('source', 'unknown')[:20],
                    head=article.get('title', '')[:120],
                    sent=article.get('sentiment', 0),
                    novelty=article.get('novelty', 0.5),
                    etype=article.get('event_type')
                ))

            if news_items:  # Only include tickers with news
                universe.append(TickerPacket(
                    ticker=ticker,
                    pre_score=scores.get('composite_score', 0),
                    features=features,
                    news=news_items
                ))

        # Build request
        request = LLMRequest(
            as_of=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            account=account_data,
            risk_limits={
                'max_single': float(os.getenv('RISK_MAX_SINGLE_NAME', '0.02')),
                'gross_max': float(os.getenv('RISK_GROSS_MAX', '0.60')),
                'net_max': float(os.getenv('RISK_NET_MAX', '0.40'))
            },
            universe=universe,
            constraints={
                'deny': ['OTC'],
                'sector_caps': {'XLK': 0.30}  # Tech sector cap
            }
        )

        return request

    def warm_up_models(self):
        """Warm up both models - summary on startup, selector before use"""
        # Warm up summary model immediately
        if not self.models_warmed['summary']:
            self._warm_up_model(self.summary_model, 'summary')

    def _warm_up_model(self, model: str, model_type: str):
        """Warm up a specific model"""
        try:
            logger.info(f"Warming up {model_type} model: {model}...")
            response = self.client.post(
                '/api/generate',
                json={
                    'model': model,
                    'prompt': 'Test: Extract ticker from Apple news. Response: ["AAPL"]',
                    'stream': False,
                    'temperature': 0.1,
                    'max_tokens': 20
                }
            )
            if response.status_code == 200:
                self.models_warmed[model_type] = True
                logger.info(f"{model_type} model {model} warmed up successfully")
        except Exception as e:
            logger.warning(f"Failed to warm up {model_type} model: {e}")

    def summarize_news_batch(self, news_data: Dict[str, List[Dict]]) -> Dict[str, Dict]:
        """
        Step 1: Use fast model (v2:16b) to summarize news for all tickers
        """
        summaries = {}

        for ticker, articles in news_data.items():
            if not articles:
                continue

            # Take top 3 most recent
            recent = sorted(articles,
                          key=lambda x: x.get('published_at', ''),
                          reverse=True)[:3]

            headlines = "\n".join([f"- {a.get('title', '')[:120]}" for a in recent])

            prompt = f"""Analyze these headlines for {ticker}:
{headlines}

Provide JSON only:
{{"sentiment": -1 to 1, "event": "earnings|product|guidance|regulatory|other", "novelty": 0 to 1, "summary": "one line under 120 chars"}}"""

            try:
                response = self.client.post(
                    '/api/generate',
                    json={
                        'model': self.summary_model,
                        'prompt': prompt,
                        'stream': False,
                        'temperature': 0.3,
                        'max_tokens': 300,
                        'format': 'json'
                    }
                )

                if response.status_code == 200:
                    result = response.json()
                    output = result.get('response', '')
                    if output:
                        summary = json.loads(output)
                        summaries[ticker] = summary

            except Exception as e:
                logger.debug(f"Failed to summarize {ticker}: {e}")

        return summaries

    def query_llm(self, packet: LLMRequest) -> Optional[LLMResponse]:
        """
        Query LLM with packet and get ranked signals

        Args:
            packet: Prepared LLM request packet

        Returns:
            Validated LLM response or None
        """
        try:
            # Prepare the prompt
            user_prompt = f"""Given this market context, rank tickers for trading:

{packet.model_dump_json(indent=2)}

Return a JSON response with this EXACT schema:
{{
  "as_of": "YYYY-MM-DD",
  "ranked": [
    {{
      "ticker": "SYMBOL",
      "action": "BUY|AVOID|WATCH",
      "llm_news_score": -1.0 to 1.0,
      "confidence": 0.0 to 1.0,
      "catalyst_window_days": 0 to 30,
      "rationale": "One sentence using headline evidence"
    }}
  ]
}}

Score each ticker's llm_news_score from -1 (strong avoid) to 1 (strong buy).
Include ALL tickers from the universe in your response.
"""

            # Warm up selector model ONLY when needed for selection
            if not self.models_warmed['selector']:
                logger.info(f"Switching to selector model: {self.selector_model}")
                self._warm_up_model('selector')

            # Query the selector model (r1:32b)
            response = self.client.post(
                '/api/generate',
                json={
                    "model": self.selector_model,
                    "prompt": f"{self.system_prompt}\n\n{user_prompt}",
                    "temperature": 0.2,
                    "top_p": 0.9,
                    "stream": False,
                    "format": "json",
                    "max_tokens": 1200
                }

                if response.status_code == 200:
                    result = response.json()
                    llm_output = result.get('response', '')
            else:
                logger.error(f"Selector query failed: {response.status_code}")
                return None

            # Parse and validate response
            llm_data = json.loads(llm_output)
            validated_response = LLMResponse(**llm_data)

            logger.info(f"LLM ranked {len(validated_response.ranked)} tickers")
            return validated_response

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM query error: {e}")
            return None

    def merge_signals(self,
                     pre_scores: Dict[str, float],
                     llm_response: LLMResponse,
                     threshold: float = 0.5) -> List[Dict[str, Any]]:
        """
        Merge quant pre-scores with LLM news scores

        Args:
            pre_scores: Quantitative pre-scores per ticker
            llm_response: LLM response with news scores
            threshold: Minimum final score for BUY signals

        Returns:
            List of merged signals ready for execution
        """
        merged_signals = []

        for signal in llm_response.ranked:
            ticker = signal.ticker
            pre_score = pre_scores.get(ticker, 0)

            # Calculate weighted final score
            final_score = (
                self.weight_quant * pre_score +
                self.weight_llm * signal.llm_news_score
            )

            # Only include BUY signals above threshold
            if signal.action == 'BUY' and final_score >= threshold:
                merged_signals.append({
                    'symbol': ticker,
                    'action': 'buy',
                    'final_score': final_score,
                    'pre_score': pre_score,
                    'llm_news_score': signal.llm_news_score,
                    'confidence': signal.confidence,
                    'catalyst_window_days': signal.catalyst_window_days,
                    'rationale': signal.rationale,
                    'signal_type': 'llm_enhanced',
                    'weights': {
                        'quant': self.weight_quant,
                        'llm': self.weight_llm
                    }
                })

        # Sort by final score
        merged_signals.sort(key=lambda x: x['final_score'], reverse=True)

        return merged_signals

    def track_performance(self, signals: List[Dict[str, Any]]):
        """
        Track A/B performance of LLM vs pure quant signals

        Args:
            signals: List of signals with both pre_score and llm_news_score
        """
        session = get_session()
        try:
            for signal in signals:
                # Store signal with both scores for A/B analysis
                db_signal = Signal(
                    symbol=signal['symbol'],
                    signal_date=datetime.now(timezone.utc),
                    factor_name='llm_copilot',
                    raw_value=signal['llm_news_score'],
                    normalized_score=signal['final_score'],
                    weight=self.weight_llm,
                    rationale=json.dumps({
                        'pre_score': signal['pre_score'],
                        'llm_news_score': signal['llm_news_score'],
                        'final_score': signal['final_score'],
                        'confidence': signal.get('confidence'),
                        'rationale': signal['rationale']
                    })
                )
                session.add(db_signal)

            session.commit()
            logger.info(f"Tracked {len(signals)} LLM-enhanced signals for A/B analysis")

        except Exception as e:
            session.rollback()
            logger.error(f"Error tracking performance: {e}")
        finally:
            session.close()

    def run(self,
           account_data: Dict[str, float],
           universe_scores: Dict[str, Dict[str, Any]],
           news_data: Dict[str, List[Dict]]) -> List[Dict[str, Any]]:
        """
        Complete LLM copilot pipeline

        Args:
            account_data: Current account state
            universe_scores: Pre-calculated factor scores
            news_data: Summarized news per ticker

        Returns:
            List of actionable trading signals
        """
        logger.info("Starting LLM copilot signal generation")

        # 0. Warm up summary model if needed
        if not self.models_warmed['summary']:
            self._warm_up_model(self.summary_model, 'summary')

        # 1. News pass with fast model (v2:16b) - summarize all news first
        logger.info(f"Summarizing news with {self.summary_model}...")
        news_summaries = self.summarize_news_batch(news_data)

        # 2. Enhance news data with summaries
        for ticker in news_data:
            if ticker in news_summaries:
                summary = news_summaries[ticker]
                # Add summary to first article for each ticker
                if news_data[ticker]:
                    news_data[ticker][0]['sentiment'] = summary.get('sentiment', 0)
                    news_data[ticker][0]['novelty'] = summary.get('novelty', 0.5)
                    news_data[ticker][0]['event_type'] = summary.get('event', 'other')
                    news_data[ticker][0]['summary'] = summary.get('summary', '')

        # 3. Prepare compact packet with enhanced news
        packet = self.prepare_packet(account_data, universe_scores, news_data)
        logger.info(f"Prepared packet with {len(packet.universe)} tickers")

        # 4. Decision pass with strong model (r1:32b)
        logger.info(f"Running decision pass with {self.selector_model}...")
        llm_response = self.query_llm(packet)
        if not llm_response:
            logger.warning("No valid selector response, falling back to pure quant")
            return []

        # 3. Merge signals
        pre_scores = {t.ticker: t.pre_score for t in packet.universe}
        signals = self.merge_signals(pre_scores, llm_response)

        # 4. Track performance for A/B testing
        self.track_performance(signals)

        logger.info(f"Generated {len(signals)} actionable signals from LLM copilot")
        return signals


if __name__ == "__main__":
    # Test the LLM copilot
    copilot = LLMCopilot()

    # Mock data for testing
    account_data = {
        "equity": 125000,
        "cash": 40000,
        "net_exposure": 0.22
    }

    universe_scores = {
        "AAPL": {
            "composite_score": 0.63,
            "momentum": 0.8,
            "rsi": 58,
            "macd_histogram": 0.12,
            "volume_ratio": 1.4,
            "earnings_surprise": True
        },
        "NVDA": {
            "composite_score": 0.75,
            "momentum": 0.9,
            "rsi": 65,
            "macd_histogram": 0.20,
            "volume_ratio": 2.1,
            "earnings_surprise": True
        }
    }

    news_data = {
        "AAPL": [
            {
                "published_at": "2025-09-12T14:10:00Z",
                "source": "Alpaca",
                "title": "Apple raises FY guidance on strong iPhone demand",
                "sentiment": 0.82,
                "novelty": 0.9,
                "event_type": "guidance"
            }
        ],
        "NVDA": [
            {
                "published_at": "2025-09-12T15:30:00Z",
                "source": "Reuters",
                "title": "NVIDIA announces new AI chip exceeding performance targets",
                "sentiment": 0.90,
                "novelty": 0.95,
                "event_type": "product"
            }
        ]
    }

    signals = copilot.run(account_data, universe_scores, news_data)

    print(f"\nGenerated {len(signals)} signals:")
    for signal in signals[:5]:
        print(f"  {signal['symbol']}: {signal['action']} (score: {signal['final_score']:.3f})")
        print(f"    Rationale: {signal['rationale']}")