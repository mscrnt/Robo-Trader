import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_broker_mode_selection():
    """Test that broker correctly selects paper/live mode"""
    # Test paper mode (default)
    os.environ['TRADING_MODE'] = 'paper'
    os.environ['LIVE_TRADING_ENABLED'] = 'false'
    os.environ['LIVE_CONFIRM_PHRASE'] = ''

    from services.broker.alpaca_broker import AlpacaBroker

    # Mock credentials
    os.environ['ALPACA_KEY_ID'] = 'test_key'
    os.environ['ALPACA_SECRET_KEY'] = 'test_secret'

    broker = AlpacaBroker()
    assert broker.mode == 'paper'

def test_risk_position_sizing():
    """Test position sizing calculation"""
    from services.risk.risk import RiskManager

    risk_mgr = RiskManager()

    # Test position sizing
    size = risk_mgr.calculate_position_size(
        symbol='TEST',
        signal_strength=0.8,
        account_equity=100000,
        current_price=100
    )

    # Should be limited by max position size (2% = $2000 = 20 shares at $100)
    assert size > 0
    assert size <= 20

def test_circuit_breaker():
    """Test circuit breaker triggers"""
    from services.risk.risk import RiskManager

    risk_mgr = RiskManager()

    # Test normal conditions
    account_data = {
        'equity': 100000,
        'last_equity': 100000
    }
    ok, msg = risk_mgr.check_circuit_breakers(account_data)
    assert ok == True

    # Test drawdown trigger
    account_data = {
        'equity': 97000,  # 3% loss
        'last_equity': 100000
    }
    ok, msg = risk_mgr.check_circuit_breakers(account_data)
    assert ok == False  # Should trigger at 2% loss
    assert 'drawdown' in msg.lower()

if __name__ == "__main__":
    # Run basic tests
    test_broker_mode_selection()
    print("✓ Broker mode selection test passed")

    test_risk_position_sizing()
    print("✓ Risk position sizing test passed")

    test_circuit_breaker()
    print("✓ Circuit breaker test passed")

    print("\nAll tests passed!")