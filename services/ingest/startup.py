#!/usr/bin/env python3
"""
Startup script for ingest service
Ensures proper initialization order
"""

import os
import sys
import time
import logging

sys.path.append('/app')

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

def wait_for_dependencies():
    """Wait for database and other dependencies to be ready"""
    from libs.database import get_session
    from sqlalchemy import text

    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            # Try to connect to database
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

def main():
    """Main startup sequence"""
    logger.info("="*60)
    logger.info("INGEST SERVICE STARTING")
    logger.info("="*60)

    # Wait for dependencies
    if not wait_for_dependencies():
        sys.exit(1)

    # Small delay to ensure other services are starting
    logger.info("Waiting 10 seconds for system initialization...")
    time.sleep(10)

    # Run the ingest service
    logger.info("Starting ingest service main process...")
    from services.ingest.ingest import MarketDataIngestor

    try:
        ingestor = MarketDataIngestor()
        ingestor.run_daily_ingest()
        logger.info("Initial ingestion completed successfully")
    except Exception as e:
        logger.error(f"Ingest service failed: {e}")
        # Don't exit - keep running for scheduled jobs

    # Keep the service running
    logger.info("Ingest service ready and waiting for scheduled jobs...")
    while True:
        time.sleep(3600)  # Sleep for an hour

if __name__ == "__main__":
    main()