import os
import sys
import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any
import ta
import yaml
import httpx
import json

sys.path.append('/app')
from libs.database import get_session, PriceData, Signal

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class SignalGenerator:
    def __init__(self):
        self.strategy_config = self._load_strategy_config()
        self.llm_client = self._setup_llm_client()
        self.rate_limits = self._load_rate_limits()

    def _load_rate_limits(self) -> Dict:
        """Load rate limiting configuration"""
        try:
            rate_limits_file = '/app/configs/rate_limits.yaml'
            if os.path.exists(rate_limits_file):
                with open(rate_limits_file, 'r') as f:
                    return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Could not load rate limits: {e}")

        return {
            'signals': {'use_llm_rationale': False}
        }

    def _load_strategy_config(self) -> Dict:
        """Load strategy configuration"""
        config_file = '/app/configs/strategy.yaml'
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                return yaml.safe_load(f)

        # Default config
        return {
            'factors': {
                'momentum_12_1': {'weight': 0.25, 'enabled': True},
                'rsi_14': {'weight': 0.15, 'enabled': True},
                'macd': {'weight': 0.20, 'enabled': True},
                'volume_surge': {'weight': 0.15, 'enabled': True},
                'earnings_drift': {'weight': 0.15, 'enabled': True},
                'news_sentiment': {'weight': 0.10, 'enabled': True}
            }
        }

    def _setup_llm_client(self):
        """Setup LLM client for Ollama"""
        base_url = os.getenv('LLM_BASE_URL', 'http://192.168.69.197:11434')
        return httpx.Client(base_url=base_url, timeout=30.0)

    def calculate_momentum(self, prices: pd.DataFrame) -> float:
        """Calculate 12-month momentum excluding last month"""
        if len(prices) < 252:  # ~12 months of trading days
            return 0.0

        price_12m_ago = prices.iloc[-252]['close']
        price_1m_ago = prices.iloc[-21]['close']

        if price_12m_ago > 0:
            return (price_1m_ago - price_12m_ago) / price_12m_ago
        return 0.0

    def calculate_rsi(self, prices: pd.DataFrame, period: int = 14) -> float:
        """Calculate RSI"""
        if len(prices) < period:
            return 50.0  # Neutral

        rsi = ta.momentum.RSIIndicator(prices['close'], window=period)
        return rsi.rsi().iloc[-1]

    def calculate_macd(self, prices: pd.DataFrame) -> Dict[str, float]:
        """Calculate MACD signal"""
        if len(prices) < 26:
            return {'signal': 0.0, 'histogram': 0.0}

        macd = ta.trend.MACD(prices['close'])
        return {
            'signal': macd.macd_signal().iloc[-1],
            'histogram': macd.macd_diff().iloc[-1]
        }

    def calculate_volume_surge(self, prices: pd.DataFrame, lookback: int = 20) -> float:
        """Detect unusual volume"""
        if len(prices) < lookback:
            return 0.0

        avg_volume = prices['volume'].rolling(lookback).mean().iloc[-1]
        current_volume = prices['volume'].iloc[-1]

        if avg_volume > 0:
            return current_volume / avg_volume
        return 1.0

    def generate_llm_rationale(self, symbol: str, factors: Dict[str, float]) -> str:
        """Generate trading rationale - SIMPLIFIED to reduce API calls"""
        # Skip individual LLM calls per symbol to respect rate limits
        # Use rule-based rationale instead
        momentum = factors.get('momentum', 0)
        rsi = factors.get('rsi', 50)
        macd = factors.get('macd_histogram', 0)
        volume = factors.get('volume_surge', 1)

        signals = []
        if momentum > 0.5:
            signals.append("strong momentum")
        elif momentum < -0.5:
            signals.append("weak momentum")

        if rsi > 70:
            signals.append("overbought RSI")
        elif rsi < 30:
            signals.append("oversold RSI")

        if volume > 1.5:
            signals.append("high volume")

        if signals:
            return f"{symbol}: {', '.join(signals)}"
        else:
            return f"{symbol}: neutral signals"

    def calculate_composite_score(self, factors: Dict[str, float]) -> float:
        """Calculate weighted composite score"""
        weights = self.strategy_config.get('factors', {})
        total_weight = 0
        weighted_sum = 0

        for factor_name, factor_value in factors.items():
            if factor_name in weights and weights[factor_name].get('enabled', True):
                weight = weights[factor_name].get('weight', 0)
                normalized_value = self.normalize_factor(factor_name, factor_value)
                weighted_sum += normalized_value * weight
                total_weight += weight

        if total_weight > 0:
            return weighted_sum / total_weight
        return 0.5  # Neutral

    def normalize_factor(self, factor_name: str, value: float) -> float:
        """Normalize factor values to 0-1 range"""
        if factor_name == 'momentum':
            # Momentum: -50% to +50% mapped to 0-1
            return max(0, min(1, (value + 0.5) / 1.0))

        elif factor_name == 'rsi':
            # RSI: 0-100 mapped inversely (low RSI = buy signal)
            if value < 30:
                return 0.8 + (30 - value) / 150  # Oversold
            elif value > 70:
                return 0.2 - (value - 70) / 150  # Overbought
            else:
                return 0.5  # Neutral

        elif factor_name == 'macd_histogram':
            # MACD: positive = bullish
            return 1 / (1 + np.exp(-value * 10))  # Sigmoid

        elif factor_name == 'volume_surge':
            # Volume: >2x average is bullish
            if value > 2:
                return min(1, 0.5 + (value - 1) * 0.25)
            return 0.5

        return 0.5  # Default neutral

    def generate_signals(self, symbols: List[str] = None, use_llm: bool = True) -> List[Dict[str, Any]]:
        """Generate signals for all symbols with optional LLM enhancement"""
        session = get_session()
        signals = []
        universe_scores = {}  # For LLM copilot

        try:
            # Get symbols from universe if not provided
            if not symbols:
                from services.ingest.ingest import MarketDataIngestor
                ingestor = MarketDataIngestor()
                symbols = ingestor.universe

            # First pass: Generate base quant signals
            for symbol in symbols:
                try:
                    # Fetch price history
                    end_date = datetime.now(timezone.utc)
                    start_date = end_date - timedelta(days=365)

                    prices = session.query(PriceData).filter(
                        PriceData.symbol == symbol,
                        PriceData.date >= start_date
                    ).order_by(PriceData.date).all()

                    if len(prices) < 20:
                        logger.warning(f"Insufficient data for {symbol}")
                        continue

                    # Convert to DataFrame
                    df = pd.DataFrame([{
                        'date': p.date,
                        'open': p.open,
                        'high': p.high,
                        'low': p.low,
                        'close': p.close,
                        'volume': p.volume
                    } for p in prices])

                    # Calculate factors
                    factors = {
                        'momentum': self.calculate_momentum(df),
                        'rsi': self.calculate_rsi(df),
                        'macd_histogram': self.calculate_macd(df)['histogram'],
                        'volume_surge': self.calculate_volume_surge(df)
                    }

                    # Calculate composite score
                    score = self.calculate_composite_score(factors)

                    # Generate LLM rationale (only if enabled in rate limits)
                    if self.rate_limits.get('signals', {}).get('use_llm_rationale', False):
                        rationale = self.generate_llm_rationale(symbol, factors)
                    else:
                        # Use simple rule-based rationale
                        rationale = self.generate_llm_rationale(symbol, factors)

                    # Save signals to database
                    for factor_name, factor_value in factors.items():
                        # Convert numpy types to Python native types
                        if hasattr(factor_value, 'item'):
                            factor_value = factor_value.item()  # Convert numpy scalar to Python scalar

                        normalized = self.normalize_factor(factor_name, factor_value)
                        if hasattr(normalized, 'item'):
                            normalized = normalized.item()

                        signal = Signal(
                            symbol=symbol,
                            signal_date=datetime.now(timezone.utc),
                            factor_name=factor_name,
                            raw_value=float(factor_value) if factor_value is not None else 0.0,
                            normalized_score=float(normalized) if normalized is not None else 0.5,
                            weight=self.strategy_config['factors'].get(factor_name, {}).get('weight', 0),
                            rationale=rationale if factor_name == 'momentum' else None
                        )
                        session.add(signal)

                    # Store for LLM copilot
                    universe_scores[symbol] = {
                        'composite_score': score,
                        'momentum': factors.get('momentum', 0),
                        'rsi': factors.get('rsi', 50),
                        'macd_histogram': factors.get('macd_histogram', 0),
                        'volume_ratio': factors.get('volume_surge', 1.0),
                        'earnings_surprise': False  # Would come from earnings data
                    }

                    signals.append({
                        'symbol': symbol,
                        'score': score,
                        'factors': factors,
                        'rationale': rationale,
                        'action': 'buy' if score > 0.6 else ('sell' if score < 0.4 else 'hold')
                    })

                    logger.info(f"Generated signals for {symbol}: score={score:.2f}")

                except Exception as e:
                    logger.error(f"Error generating signals for {symbol}: {e}")

            session.commit()

            # Second pass: Enhance with LLM if enabled
            # DISABLED to reduce API calls and avoid import errors
            if False and use_llm and universe_scores:
                try:
                    from llm_copilot import LLMCopilot
                    from libs.database import NewsArticle
                    from services.broker.alpaca_broker import AlpacaBroker

                    # Get account data
                    broker = AlpacaBroker()
                    account = broker.get_account()
                    account_data = {
                        'equity': float(account.equity) if account else 100000,
                        'cash': float(account.cash) if account else 100000,
                        'net_exposure': 0.0  # Calculate from positions
                    }

                    # Get recent news
                    recent_news = session.query(NewsArticle).filter(
                        NewsArticle.published_at >= datetime.now(timezone.utc) - timedelta(hours=24)
                    ).all()

                    # Organize news by ticker
                    news_data = {}
                    for article in recent_news:
                        for ticker in article.tickers or []:
                            if ticker not in news_data:
                                news_data[ticker] = []
                            news_data[ticker].append({
                                'published_at': article.published_at.isoformat() if article.published_at else '',
                                'source': article.source,
                                'title': article.title,
                                'sentiment': article.sentiment or 0,
                                'novelty': article.novelty or 0.5,
                                'event_type': article.event_type
                            })

                    # Run LLM copilot
                    copilot = LLMCopilot()
                    llm_signals = copilot.run(account_data, universe_scores, news_data)

                    # Replace signals with LLM-enhanced versions
                    if llm_signals:
                        # Create a map of existing signals
                        signal_map = {s['symbol']: s for s in signals}

                        # Update with LLM signals
                        for llm_signal in llm_signals:
                            symbol = llm_signal['symbol']
                            if symbol in signal_map:
                                # Merge LLM data into existing signal
                                signal_map[symbol]['score'] = llm_signal['final_score']
                                signal_map[symbol]['llm_score'] = llm_signal['llm_news_score']
                                signal_map[symbol]['rationale'] = llm_signal['rationale']
                                signal_map[symbol]['confidence'] = llm_signal.get('confidence', 0.5)
                            else:
                                # Add new LLM-only signal
                                signals.append(llm_signal)

                        logger.info(f"Enhanced {len(llm_signals)} signals with LLM copilot")

                except Exception as e:
                    logger.warning(f"LLM enhancement failed, using pure quant signals: {e}")

            return signals

        except Exception as e:
            session.rollback()
            logger.error(f"Error in signal generation: {e}")
            raise
        finally:
            session.close()

def wait_for_data(max_wait_minutes: int = 10):
    """Wait for market data to be available before generating signals"""
    import time
    from datetime import datetime, timedelta, timezone

    session = get_session()
    start_time = datetime.now()
    timeout = timedelta(minutes=max_wait_minutes)

    logger.info("Waiting for market data to be available...")

    while datetime.now() - start_time < timeout:
        try:
            # Check if we have recent price data
            recent_date = datetime.now(timezone.utc) - timedelta(days=7)
            count = session.query(PriceData).filter(
                PriceData.date >= recent_date
            ).count()

            if count > 0:
                logger.info(f"Found {count} recent price records, data is ready")
                session.close()
                return True

            logger.info(f"No recent data yet, waiting... ({int((datetime.now() - start_time).total_seconds())}s elapsed)")
            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            logger.warning(f"Error checking for data: {e}")
            time.sleep(30)

    session.close()
    logger.warning(f"Timeout waiting for data after {max_wait_minutes} minutes")
    return False

if __name__ == "__main__":
    # Wait for ingest service to populate data first
    if wait_for_data():
        try:
            generator = SignalGenerator()
            signals = generator.generate_signals()
            logger.info(f"Generated {len(signals)} signals")
            for signal in signals[:5]:
                print(f"  {signal['symbol']}: {signal['action']} (score: {signal['score']:.2f})")
            logger.info("Signals service completed successfully")
        except Exception as e:
            logger.error(f"Signals service failed: {e}")
    else:
        logger.error("Could not generate signals - no market data available")

    # Keep the service running but idle
    logger.info("Signals service ready and waiting...")
    import time
    while True:
        time.sleep(3600)  # Sleep for an hour