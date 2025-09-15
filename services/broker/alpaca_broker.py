import os
import sys
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce, OrderType
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

sys.path.append('/app')
from libs.database import get_session, Order, Position

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class AlpacaBroker:
    def __init__(self):
        self.mode = self._determine_mode()
        self.client = self._get_client()
        self.data_client = self._get_data_client()
        logger.info(f"AlpacaBroker initialized in {self.mode} mode")

    def _determine_mode(self) -> str:
        mode = os.getenv('TRADING_MODE', 'paper')

        if mode == 'live':
            live_enabled = os.getenv('LIVE_TRADING_ENABLED', 'false') == 'true'
            live_phrase = os.getenv('LIVE_CONFIRM_PHRASE', '')

            if not (live_enabled and live_phrase == 'I_UNDERSTAND_THE_RISKS'):
                logger.warning("Live trading requested but not properly configured, falling back to paper")
                return 'paper'

        return mode

    def _get_client(self) -> TradingClient:
        key_id = os.getenv('ALPACA_KEY_ID')
        secret_key = os.getenv('ALPACA_SECRET_KEY')

        if not key_id or not secret_key:
            raise ValueError("Alpaca credentials not configured")

        # The Python SDK uses the paper flag to determine the endpoint
        # paper=True uses https://paper-api.alpaca.markets
        # paper=False uses https://api.alpaca.markets
        if self.mode == 'live':
            paper = False
            logger.info("Using Alpaca LIVE trading endpoint")
        else:
            paper = True
            logger.info("Using Alpaca PAPER trading endpoint")

        return TradingClient(
            api_key=key_id,
            secret_key=secret_key,
            paper=paper
        )

    def _get_data_client(self) -> StockHistoricalDataClient:
        key_id = os.getenv('ALPACA_KEY_ID')
        secret_key = os.getenv('ALPACA_SECRET_KEY')

        return StockHistoricalDataClient(
            api_key=key_id,
            secret_key=secret_key
        )

    def get_account(self) -> Dict[str, Any]:
        account = self.client.get_account()
        return {
            'buying_power': float(account.buying_power),
            'cash': float(account.cash),
            'portfolio_value': float(account.portfolio_value),
            'equity': float(account.equity),
            'last_equity': float(account.last_equity),
            'pattern_day_trader': account.pattern_day_trader,
            'trading_blocked': account.trading_blocked,
            'account_blocked': account.account_blocked,
            'daytrade_count': account.daytrade_count,
            'daytrading_buying_power': float(account.daytrading_buying_power) if account.daytrading_buying_power else None
        }

    def get_positions(self) -> List[Dict[str, Any]]:
        positions = self.client.get_all_positions()

        # Update database
        session = get_session()
        try:
            # Clear existing positions (handle case where table might not exist yet)
            try:
                session.query(Position).delete()
            except Exception as e:
                logger.debug(f"Could not clear positions table: {e}")

            position_list = []
            for pos in positions:
                db_pos = Position(
                    symbol=pos.symbol,
                    qty=int(pos.qty),
                    avg_entry_price=float(pos.avg_entry_price),
                    market_value=float(pos.market_value),
                    cost_basis=float(pos.cost_basis),
                    unrealized_pl=float(pos.unrealized_pl),
                    unrealized_plpc=float(pos.unrealized_plpc),
                    current_price=float(pos.current_price) if pos.current_price else None,
                    lastday_price=float(pos.lastday_price) if pos.lastday_price else None,
                    change_today=float(pos.change_today) if pos.change_today else None,
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(db_pos)

                position_list.append({
                    'symbol': pos.symbol,
                    'qty': int(pos.qty),
                    'avg_entry_price': float(pos.avg_entry_price),
                    'market_value': float(pos.market_value),
                    'cost_basis': float(pos.cost_basis),
                    'unrealized_pl': float(pos.unrealized_pl),
                    'unrealized_plpc': float(pos.unrealized_plpc),
                    'current_price': float(pos.current_price) if pos.current_price else None,
                    'change_today': float(pos.change_today) if pos.change_today else None
                })

            session.commit()
            return position_list

        except Exception as e:
            session.rollback()
            logger.error(f"Error updating positions: {e}")
            raise
        finally:
            session.close()

    def place_order(self, symbol: str, side: str, qty: int, order_type: str = 'market',
                   limit_price: Optional[float] = None, stop_price: Optional[float] = None,
                   time_in_force: str = 'day', plan_id: Optional[int] = None) -> Dict[str, Any]:

        # Check kill switch
        import redis
        redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://redis:6379/0'))
        kill_switch = redis_client.get("GLOBAL_KILL_SWITCH")
        if kill_switch and kill_switch.decode() == "true":
            raise Exception("Trading is paused (GLOBAL_KILL_SWITCH is active)")

        # Prepare order request
        order_side = OrderSide.BUY if side.lower() == 'buy' else OrderSide.SELL
        tif = getattr(TimeInForce, time_in_force.upper(), TimeInForce.DAY)

        if order_type.lower() == 'market':
            request = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif
            )
        elif order_type.lower() == 'limit':
            if not limit_price:
                raise ValueError("Limit price required for limit orders")
            request = LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                limit_price=limit_price
            )
        elif order_type.lower() == 'stop':
            if not stop_price:
                raise ValueError("Stop price required for stop orders")
            request = StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=order_side,
                time_in_force=tif,
                stop_price=stop_price
            )
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        # Submit order
        try:
            order = self.client.submit_order(request)

            # Save to database
            session = get_session()
            try:
                db_order = Order(
                    order_id=order.id,
                    plan_id=plan_id,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    order_type=order_type,
                    limit_price=limit_price,
                    stop_price=stop_price,
                    time_in_force=time_in_force,
                    status=order.status,
                    submitted_at=order.submitted_at,
                    created_at=datetime.now(timezone.utc)
                )
                session.add(db_order)
                session.commit()

                logger.info(f"Order placed: {symbol} {side} {qty} @ {order_type}")

                return {
                    'order_id': order.id,
                    'symbol': symbol,
                    'side': side,
                    'qty': qty,
                    'order_type': order_type,
                    'status': order.status,
                    'submitted_at': order.submitted_at.isoformat() if order.submitted_at else None
                }

            except Exception as e:
                session.rollback()
                logger.error(f"Error saving order to database: {e}")
                raise
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            raise

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.cancel_order_by_id(order_id)

            # Update database
            session = get_session()
            try:
                db_order = session.query(Order).filter_by(order_id=order_id).first()
                if db_order:
                    db_order.status = 'cancelled'
                    db_order.cancelled_at = datetime.now(timezone.utc)
                    session.commit()

                logger.info(f"Order cancelled: {order_id}")
                return True

            except Exception as e:
                session.rollback()
                logger.error(f"Error updating cancelled order: {e}")
                raise
            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False

    def get_orders(self, status: str = 'all', limit: int = 100) -> List[Dict[str, Any]]:
        orders = self.client.get_orders(status=status, limit=limit)

        return [
            {
                'order_id': order.id,
                'symbol': order.symbol,
                'side': order.side,
                'qty': order.qty,
                'order_type': order.order_type,
                'status': order.status,
                'filled_qty': order.filled_qty,
                'filled_avg_price': float(order.filled_avg_price) if order.filled_avg_price else None,
                'submitted_at': order.submitted_at.isoformat() if order.submitted_at else None,
                'filled_at': order.filled_at.isoformat() if order.filled_at else None
            }
            for order in orders
        ]

    def sync_orders(self):
        """Sync order status from Alpaca to database"""
        orders = self.client.get_orders(status='all', limit=500)

        session = get_session()
        try:
            for order in orders:
                db_order = session.query(Order).filter_by(order_id=order.id).first()
                if db_order:
                    db_order.status = order.status
                    db_order.filled_qty = order.filled_qty
                    db_order.filled_avg_price = float(order.filled_avg_price) if order.filled_avg_price else None
                    db_order.filled_at = order.filled_at
                    db_order.updated_at = datetime.now(timezone.utc)

            session.commit()
            logger.info(f"Synced {len(orders)} orders from Alpaca")

        except Exception as e:
            session.rollback()
            logger.error(f"Error syncing orders: {e}")
            raise
        finally:
            session.close()

    def get_market_hours(self, date: Optional[datetime] = None) -> Dict[str, Any]:
        """Get market hours for a given date"""
        if not date:
            date = datetime.now(timezone.utc)

        calendar = self.client.get_calendar(start=date.date(), end=date.date())

        if calendar:
            cal = calendar[0]
            return {
                'date': cal.date,
                'open': cal.open,
                'close': cal.close,
                'is_open': True
            }
        else:
            return {
                'date': date.date(),
                'is_open': False
            }

if __name__ == "__main__":
    # Initialize database first
    try:
        from libs.init_db import init_database
        init_database()
    except Exception as e:
        logger.warning(f"Database initialization: {e}")

    # Test the broker
    try:
        broker = AlpacaBroker()
        print(f"Broker mode: {broker.mode}")

        account = broker.get_account()
        print(f"Account equity: ${account['equity']:,.2f}")
        print(f"Buying power: ${account['buying_power']:,.2f}")

        try:
            positions = broker.get_positions()
            print(f"Positions: {len(positions)}")

            for pos in positions[:5]:
                print(f"  {pos['symbol']}: {pos['qty']} shares @ ${pos['avg_entry_price']:.2f}")
        except Exception as e:
            logger.warning(f"Could not get positions (might be empty): {e}")
            print("Positions: 0 (no positions or initialization in progress)")

        logger.info("Broker service initialized successfully")
    except Exception as e:
        logger.error(f"Broker initialization failed: {e}")

    # Keep the service running but idle
    logger.info("Broker service ready and waiting...")
    import time
    while True:
        time.sleep(3600)  # Sleep for an hour