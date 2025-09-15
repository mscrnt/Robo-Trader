#!/usr/bin/env python3
"""
Cancel all pending orders
"""

import sys
sys.path.append('/app')

from services.broker.alpaca_broker import AlpacaBroker
from libs.database import get_session, Order
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def cancel_all_pending_orders():
    """Cancel all pending orders in the system"""

    broker = AlpacaBroker()
    session = get_session()

    try:
        # Get all pending orders from database
        pending_orders = session.query(Order).filter(
            Order.status.in_(['new', 'accepted', 'pending', 'partially_filled'])
        ).all()

        if not pending_orders:
            print("No pending orders to cancel")
            return

        print(f"Found {len(pending_orders)} pending orders to cancel:")

        cancelled_count = 0
        for order in pending_orders:
            print(f"  - {order.symbol}: {order.qty} shares (ID: {order.order_id})")
            try:
                # Cancel via Alpaca
                broker.cancel_order(order.order_id)
                cancelled_count += 1
                print(f"    ✓ Cancelled")
            except Exception as e:
                print(f"    ✗ Failed to cancel: {e}")

        print(f"\nSuccessfully cancelled {cancelled_count}/{len(pending_orders)} orders")

    finally:
        session.close()

if __name__ == "__main__":
    cancel_all_pending_orders()