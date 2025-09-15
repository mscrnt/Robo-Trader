#!/usr/bin/env python3
"""
Startup script for signals service
Ensures data is available before generating signals
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone

sys.path.append('/app')

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

def wait_for_database():
    """Wait for database to be ready"""
    from libs.database import get_session
    from sqlalchemy import text

    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            session = get_session()
            result = session.execute(text('SELECT 1'))
            result.fetchone()
            session.close()
            logger.info("Database is ready")
            return True
        except Exception as e:
            retry_count += 1
            logger.info(f"Waiting for database... ({retry_count}/{max_retries})")
            time.sleep(2)

    logger.error("Database not ready after maximum retries")
    return False

def wait_for_market_data(max_wait_minutes: int = 15):
    """Wait for market data to be available"""
    from libs.database import get_session, PriceData

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

            if count >= 50:  # Need at least 50 recent price records
                logger.info(f"Found {count} recent price records, data is ready")
                session.close()
                return True

            logger.info(f"Found {count} records, waiting for more data... ({int((datetime.now() - start_time).total_seconds())}s elapsed)")
            time.sleep(30)  # Check every 30 seconds

        except Exception as e:
            logger.warning(f"Error checking for data: {e}")
            time.sleep(30)

    session.close()
    logger.warning(f"Timeout waiting for data after {max_wait_minutes} minutes")
    return False

def main():
    """Main startup sequence"""
    logger.info("="*60)
    logger.info("SIGNALS SERVICE STARTING")
    logger.info("="*60)

    # Wait for database
    if not wait_for_database():
        sys.exit(1)

    # Wait for market data from ingest service
    if not wait_for_market_data():
        logger.error("No market data available - signals cannot be generated")
        # Continue anyway - will retry periodically
    else:
        # Generate initial signals
        logger.info("Starting signal generation...")
        from services.signals.signals import SignalGenerator

        try:
            generator = SignalGenerator()
            signals = generator.generate_signals()
            logger.info(f"Generated {len(signals)} signals successfully")

            # Show top signals
            if signals:
                logger.info("Top 5 signals:")
                for signal in signals[:5]:
                    logger.info(f"  {signal['symbol']}: {signal['action']} (score: {signal['score']:.2f})")
        except Exception as e:
            logger.error(f"Signal generation failed: {e}")

    # Keep the service running
    logger.info("Signals service ready and waiting for scheduled jobs...")
    while True:
        time.sleep(3600)  # Sleep for an hour

if __name__ == "__main__":
    main()