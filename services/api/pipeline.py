import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List
import redis

sys.path.append('/app')
from libs.database import get_session, TradePlan, Order, Signal
from services.broker.alpaca_broker import AlpacaBroker

logger = logging.getLogger(__name__)
redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://redis:6379/0'))

class TradingPipeline:
    def __init__(self):
        self.broker = AlpacaBroker()
        self.mode = os.getenv('TRADING_MODE', 'paper')
        self.auto_execute = os.getenv('AUTO_EXECUTE', 'true') == 'true'

    def run(self, force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """Execute the complete trading pipeline"""
        logger.info(f"Starting pipeline - Mode: {self.mode}, Auto-execute: {self.auto_execute}, Dry-run: {dry_run}")

        try:
            # Step 1: Check circuit breakers
            if not self._check_circuit_breakers():
                return {"status": "halted", "reason": "Circuit breaker triggered"}

            # Step 2: Ingest news and generate dynamic watchlist
            news_data = self._ingest_news()

            # Step 3: Fetch market data
            market_data = self._fetch_market_data()

            # Step 4: Generate signals (including news signals)
            signals = self._generate_signals(market_data, news_data)

            # Step 4: Create trade plan
            trade_plan = self._create_trade_plan(signals)

            # Step 5: Apply risk management
            trade_plan = self._apply_risk_management(trade_plan)

            # Step 6: Save trade plan
            plan_id = self._save_trade_plan(trade_plan)

            # Step 7: Execute trades if configured
            if self.auto_execute and not dry_run:
                execution_results = self._execute_trades(trade_plan, plan_id)
                trade_plan['execution'] = execution_results

            # Step 8: Generate reports
            self._generate_reports(trade_plan)

            return {
                "status": "completed",
                "plan_id": plan_id,
                "mode": self.mode,
                "orders": len(trade_plan.get('orders', [])),
                "executed": self.auto_execute and not dry_run
            }

        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    def _check_circuit_breakers(self) -> bool:
        """Check if any circuit breakers are triggered"""
        # Check kill switch
        kill_switch = redis_client.get("GLOBAL_KILL_SWITCH")
        if kill_switch and kill_switch.decode() == "true":
            logger.warning("GLOBAL_KILL_SWITCH is active")
            return False

        # Check daily drawdown
        account = self.broker.get_account()
        equity = account['equity']
        last_equity = account['last_equity']

        if last_equity > 0:
            daily_return = (equity - last_equity) / last_equity
            max_drawdown = float(os.getenv('DAILY_DRAWDOWN_HALT', '0.02'))

            if daily_return < -max_drawdown:
                logger.warning(f"Daily drawdown limit hit: {daily_return:.2%}")
                redis_client.set("GLOBAL_KILL_SWITCH", "true")
                return False

        return True

    def _ingest_news(self) -> Dict[str, Any]:
        """Ingest news and generate dynamic watchlist"""
        try:
            # Use the news watchlist builder which fetches RSS feeds and extracts tickers
            from libs.news_watchlist import NewsWatchlistBuilder

            builder = NewsWatchlistBuilder()
            watchlist = builder.build_watchlist()

            logger.info(f"News ingestion completed: {len(watchlist)} symbols in watchlist")
            return {
                "status": "completed",
                "watchlist_size": len(watchlist),
                "symbols": watchlist
            }

        except Exception as e:
            logger.error(f"News ingestion failed: {e}")
            return {
                "status": "failed",
                "error": str(e),
                "watchlist_size": 0,
                "symbols": []
            }

    def _fetch_market_data(self) -> Dict[str, Any]:
        """Fetch current market data"""
        try:
            sys.path.append('/app/services/ingest')
            from services.ingest.ingest import MarketDataIngestor
            ingestor = MarketDataIngestor()
            ingestor.run_daily_ingest()

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "completed"
            }
        except Exception as e:
            logger.error(f"Market data fetch failed: {e}")
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "error": str(e)
            }

    def _generate_signals(self, market_data: Dict[str, Any], news_data: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Generate trading signals from market data and news"""
        try:
            from services.signals.signals import SignalGenerator
            generator = SignalGenerator()

            # Get technical signals
            signals = generator.generate_signals()

            # The signals already incorporate news-based watchlist through the database
            # No need to merge news signals separately as the watchlist is already news-driven

            logger.info(f"Generated {len(signals)} signals from news-driven watchlist")
            return signals
        except Exception as e:
            logger.error(f"Signal generation failed: {e}")
            return []

    def _create_trade_plan(self, signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Create trade plan from signals"""
        orders = []

        for signal in signals:
            if signal['action'] == 'buy' and signal.get('score', 0) > 0.6:
                order = {
                    "symbol": signal['symbol'],
                    "side": "buy",
                    "qty": 10,  # This would be calculated by risk management
                    "order_type": "market",
                    "confidence": signal.get('score', 0)
                }
                orders.append(order)

        return {
            "date": datetime.now(timezone.utc).isoformat(),
            "mode": self.mode,
            "orders": orders,
            "signals": signals
        }

    def _apply_risk_management(self, trade_plan: Dict[str, Any]) -> Dict[str, Any]:
        """Apply risk management rules to trade plan"""
        try:
            from services.risk.risk import RiskManager
            risk_mgr = RiskManager()

            account = self.broker.get_account()
            positions = self.broker.get_positions()

            # Check circuit breakers
            breaker_ok, breaker_msg = risk_mgr.check_circuit_breakers(account)
            if not breaker_ok:
                logger.warning(f"Circuit breaker triggered: {breaker_msg}")
                trade_plan['orders'] = []
                trade_plan['notes'] = breaker_msg
                return trade_plan

            # Optimize portfolio based on signals
            if trade_plan.get('signals'):
                optimized_orders = risk_mgr.optimize_portfolio(
                    trade_plan['signals'],
                    account
                )
                trade_plan['orders'] = optimized_orders

            # Check exposure limits
            approved_orders, violations = risk_mgr.check_exposure_limits(
                trade_plan.get('orders', []),
                positions,
                account['equity']
            )

            trade_plan['orders'] = approved_orders
            if violations:
                trade_plan['risk_violations'] = violations

            # Calculate risk metrics
            trade_plan['risk_metrics'] = risk_mgr.calculate_risk_metrics(
                approved_orders,
                positions,
                account['equity']
            )

            return trade_plan

        except Exception as e:
            logger.error(f"Risk management failed: {e}")
            # Clear orders on error
            trade_plan['orders'] = []
            trade_plan['error'] = str(e)
            return trade_plan

    def _save_trade_plan(self, trade_plan: Dict[str, Any]) -> int:
        """Save trade plan to database"""
        session = get_session()
        try:
            db_plan = TradePlan(
                plan_date=datetime.now(timezone.utc),
                mode=self.mode,
                universe=[o['symbol'] for o in trade_plan.get('orders', [])],
                orders=trade_plan.get('orders', []),
                risk_metrics=trade_plan.get('risk_metrics', {}),
                performance_metrics={},
                notes=f"Generated {len(trade_plan.get('orders', []))} orders",
                approved=False,
                executed=False
            )
            session.add(db_plan)
            session.commit()

            return db_plan.id

        finally:
            session.close()

    def _execute_trades(self, trade_plan: Dict[str, Any], plan_id: int) -> Dict[str, Any]:
        """Execute trades through broker"""
        results = {
            "submitted": [],
            "failed": [],
            "total": len(trade_plan.get('orders', []))
        }

        for order in trade_plan.get('orders', []):
            try:
                result = self.broker.place_order(
                    symbol=order['symbol'],
                    side=order['side'],
                    qty=order['qty'],
                    order_type=order.get('order_type', 'market'),
                    plan_id=plan_id
                )
                results['submitted'].append(result)
                logger.info(f"Order submitted: {result}")

            except Exception as e:
                logger.error(f"Failed to place order for {order['symbol']}: {e}")
                results['failed'].append({
                    "symbol": order['symbol'],
                    "error": str(e)
                })

        # Update plan as executed
        session = get_session()
        try:
            plan = session.query(TradePlan).filter_by(id=plan_id).first()
            if plan:
                plan.executed = len(results['submitted']) > 0
                session.commit()
        finally:
            session.close()

        return results

    def _generate_reports(self, trade_plan: Dict[str, Any]):
        """Generate and save reports"""
        try:
            from services.reporter.reporter import Reporter
            reporter = Reporter()

            execution_results = trade_plan.get('execution')
            reporter.process_daily_reports(trade_plan, execution_results)

            logger.info("Reports generated successfully")

        except Exception as e:
            logger.error(f"Report generation failed: {e}")