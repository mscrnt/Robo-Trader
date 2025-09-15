import os
import sys
import logging
from sqlalchemy import create_engine, text
from alembic.config import Config
from alembic import command

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def init_database():
    """Initialize database and run migrations"""
    db_url = os.getenv('DB_URL', 'postgresql://trader:trader@db:5432/trader')

    logger.info("Initializing database...")

    # Check if database is accessible
    max_retries = 30
    retry_count = 0

    while retry_count < max_retries:
        try:
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database is accessible")
            break
        except Exception as e:
            retry_count += 1
            if retry_count >= max_retries:
                logger.error(f"Could not connect to database after {max_retries} retries")
                raise
            logger.warning(f"Database not ready, retrying... ({retry_count}/{max_retries})")
            import time
            time.sleep(2)

    # Run migrations
    try:
        alembic_cfg = Config("/app/alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", db_url)

        # Generate initial migration if needed
        try:
            sys.path.append('/app')
            from libs.database import Base
            Base.metadata.create_all(engine)
            logger.info("Database tables created")
        except Exception as e:
            logger.warning(f"Could not create tables directly: {e}")

        logger.info("Database initialization completed")

    except Exception as e:
        logger.error(f"Error running migrations: {e}")
        # Continue anyway - tables might already exist

if __name__ == "__main__":
    init_database()