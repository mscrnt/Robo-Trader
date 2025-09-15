import os
import sys
import logging
import numpy as np
from typing import Dict, List, Any, Tuple
from datetime import datetime, timezone
import yaml

sys.path.append('/app')
from libs.database import get_session, Position, PriceData

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self):
        self.config = self._load_risk_config()
        self.max_position_size = float(os.getenv('RISK_MAX_SINGLE_NAME', '0.02'))
        self.max_gross_exposure = float(os.getenv('RISK_GROSS_MAX', '0.60'))
        self.max_net_exposure = float(os.getenv('RISK_NET_MAX', '0.40'))
        self.daily_drawdown_halt = float(os.getenv('DAILY_DRAWDOWN_HALT', '0.02'))

    def _load_risk_config(self) -> Dict:
        """Load risk configuration"""
        config_file = '/app/configs/strategy.yaml'
        if os.path.exists(config_file):
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
                return config.get('risk', {})

        # Default config
        return {
            'max_position_size': 0.02,
            'max_sector_exposure': 0.25,
            'max_gross_exposure': 0.60,
            'max_net_exposure': 0.40,
            'stop_loss_pct': 0.05,
            'take_profit_pct': 0.15,
            'position_sizing_method': 'equal_weight'
        }

    def calculate_position_size(self, symbol: str, signal_strength: float,
                               account_equity: float, current_price: float) -> int:
        """Calculate position size based on risk parameters"""

        # Maximum position value
        max_position_value = account_equity * self.max_position_size

        # Adjust for signal strength
        adjusted_value = max_position_value * min(1.0, signal_strength)

        # Calculate shares
        shares = int(adjusted_value / current_price)

        # Apply minimum and maximum constraints
        min_shares = max(1, int(100 / current_price))  # At least $100
        max_shares = int(50000 / current_price)  # Max $50k per position

        return max(min_shares, min(shares, max_shares))

    def check_exposure_limits(self, proposed_orders: List[Dict],
                             current_positions: List[Dict],
                             account_equity: float) -> Tuple[List[Dict], List[str]]:
        """Check if proposed orders violate exposure limits"""
        violations = []
        approved_orders = []

        # Calculate current exposure
        current_gross = sum(abs(p.get('market_value', 0)) for p in current_positions)
        current_long = sum(p.get('market_value', 0) for p in current_positions if p.get('qty', 0) > 0)
        current_short = sum(abs(p.get('market_value', 0)) for p in current_positions if p.get('qty', 0) < 0)

        for order in proposed_orders:
            # Estimate order value
            order_value = order['qty'] * order.get('price', 100)  # Use default if price not set

            # Check single name limit
            if order_value > account_equity * self.max_position_size:
                violations.append(f"{order['symbol']}: Position size ${order_value:.0f} exceeds limit")
                continue

            # Check gross exposure
            new_gross = current_gross + abs(order_value)
            if new_gross > account_equity * self.max_gross_exposure:
                violations.append(f"{order['symbol']}: Would exceed gross exposure limit")
                continue

            # Check net exposure
            if order['side'] == 'buy':
                new_net = (current_long - current_short + order_value) / account_equity
            else:
                new_net = (current_long - current_short - order_value) / account_equity

            if abs(new_net) > self.max_net_exposure:
                violations.append(f"{order['symbol']}: Would exceed net exposure limit")
                continue

            # Order approved
            approved_orders.append(order)

        if violations:
            logger.warning(f"Risk violations: {violations}")

        return approved_orders, violations

    def calculate_stop_loss(self, entry_price: float, side: str) -> float:
        """Calculate stop loss price"""
        stop_loss_pct = self.config.get('stop_loss_pct', 0.05)

        if side == 'buy':
            return entry_price * (1 - stop_loss_pct)
        else:
            return entry_price * (1 + stop_loss_pct)

    def calculate_take_profit(self, entry_price: float, side: str) -> float:
        """Calculate take profit price"""
        take_profit_pct = self.config.get('take_profit_pct', 0.15)

        if side == 'buy':
            return entry_price * (1 + take_profit_pct)
        else:
            return entry_price * (1 - take_profit_pct)

    def check_circuit_breakers(self, account_data: Dict) -> Tuple[bool, str]:
        """Check if any circuit breakers are triggered"""

        # Check daily drawdown
        equity = account_data.get('equity', 0)
        last_equity = account_data.get('last_equity', equity)

        if last_equity > 0:
            daily_return = (equity - last_equity) / last_equity
            if daily_return < -self.daily_drawdown_halt:
                return False, f"Daily drawdown limit hit: {daily_return:.2%}"

        # Check other circuit breakers from config
        circuit_breakers = self.config.get('circuit_breakers', {})

        # Could add more checks here (consecutive losses, volatility, etc.)

        return True, "All circuit breakers passed"

    def optimize_portfolio(self, signals: List[Dict], account_data: Dict) -> List[Dict]:
        """Optimize portfolio allocation based on signals and constraints"""
        equity = account_data.get('equity', 100000)
        orders = []

        # Sort signals by score
        sorted_signals = sorted(signals, key=lambda x: x.get('score', 0), reverse=True)

        # Select top signals within risk limits
        total_allocated = 0
        max_positions = 20  # Maximum number of positions

        session = get_session()
        try:
            for signal in sorted_signals[:max_positions]:
                if signal.get('action') != 'buy':
                    continue

                # Get current price
                latest_price = session.query(PriceData).filter_by(
                    symbol=signal['symbol']
                ).order_by(PriceData.date.desc()).first()

                if not latest_price:
                    continue

                current_price = latest_price.close

                # Calculate position size
                position_size = self.calculate_position_size(
                    signal['symbol'],
                    signal.get('score', 0.5),
                    equity - total_allocated,
                    current_price
                )

                if position_size > 0:
                    order_value = position_size * current_price

                    # Check if we have budget
                    if total_allocated + order_value > equity * self.max_gross_exposure:
                        break

                    order = {
                        'symbol': signal['symbol'],
                        'side': 'buy',
                        'qty': position_size,
                        'order_type': 'market',
                        'price': current_price,
                        'stop_loss': self.calculate_stop_loss(current_price, 'buy'),
                        'take_profit': self.calculate_take_profit(current_price, 'buy'),
                        'confidence': signal.get('score', 0.5)
                    }

                    orders.append(order)
                    total_allocated += order_value

            logger.info(f"Optimized portfolio: {len(orders)} orders, ${total_allocated:.0f} allocated")
            return orders

        finally:
            session.close()

    def calculate_risk_metrics(self, orders: List[Dict], positions: List[Dict],
                              account_equity: float) -> Dict[str, float]:
        """Calculate portfolio risk metrics"""

        # Current position metrics
        current_gross = sum(abs(p.get('market_value', 0)) for p in positions)
        current_long = sum(p.get('market_value', 0) for p in positions if p.get('qty', 0) > 0)
        current_short = sum(abs(p.get('market_value', 0)) for p in positions if p.get('qty', 0) < 0)

        # Proposed order metrics
        order_value = sum(o.get('qty', 0) * o.get('price', 100) for o in orders)

        # Combined metrics
        total_gross = current_gross + abs(order_value)
        total_net = current_long - current_short + order_value

        metrics = {
            'gross_exposure': total_gross / account_equity if account_equity > 0 else 0,
            'net_exposure': total_net / account_equity if account_equity > 0 else 0,
            'position_count': len(positions) + len(orders),
            'max_position_pct': self.max_position_size,
            'current_gross': current_gross,
            'current_net': current_long - current_short,
            'proposed_value': order_value
        }

        return metrics

if __name__ == "__main__":
    try:
        risk_mgr = RiskManager()

        # Test position sizing
        size = risk_mgr.calculate_position_size(
            symbol='AAPL',
            signal_strength=0.75,
            account_equity=100000,
            current_price=180
        )
        print(f"Position size for AAPL: {size} shares")

        # Test stop/take profit
        stop = risk_mgr.calculate_stop_loss(180, 'buy')
        take = risk_mgr.calculate_take_profit(180, 'buy')
        print(f"Stop loss: ${stop:.2f}, Take profit: ${take:.2f}")
        logger.info("Risk service test completed")
    except Exception as e:
        logger.error(f"Risk service failed: {e}")

    # Keep the service running but idle
    logger.info("Risk service ready and waiting...")
    import time
    while True:
        time.sleep(3600)  # Sleep for an hour