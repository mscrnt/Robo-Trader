import os
import sys
import logging
import requests
import json
from datetime import datetime, time, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_BASE = os.getenv('API_BASE', 'http://api:8000')
SCHEDULER_HOUR = int(os.getenv('SCHEDULER_HOUR', '6'))
SCHEDULER_MINUTE = int(os.getenv('SCHEDULER_MINUTE', '0'))
SCHEDULER_TIMEZONE = os.getenv('SCHEDULER_TIMEZONE', 'America/Los_Angeles')

def run_trading_pipeline():
    """Execute the daily trading pipeline"""
    logger.info("Starting scheduled trading pipeline run")

    try:
        # Check if trading is paused
        control_resp = requests.get(f'{API_BASE}/control/status')
        if control_resp.ok:
            control = control_resp.json()
            if control.get('paused'):
                logger.warning("Trading is paused, skipping scheduled run")
                return

        # Check if market is open
        from datetime import date
        today = date.today()
        # You could add market calendar check here

        # Trigger pipeline run
        response = requests.post(
            f'{API_BASE}/run',
            json={'force': False, 'dry_run': False},
            timeout=30
        )

        if response.ok:
            result = response.json()
            logger.info(f"Pipeline run triggered successfully: {result}")
        else:
            logger.error(f"Failed to trigger pipeline: {response.status_code} - {response.text}")

    except Exception as e:
        logger.error(f"Error in scheduled pipeline run: {e}", exc_info=True)

def health_check():
    """Periodic health check"""
    try:
        response = requests.get(f'{API_BASE}/health', timeout=5)
        if response.ok:
            logger.debug("Health check passed")
        else:
            logger.warning(f"Health check failed: {response.status_code}")
    except Exception as e:
        logger.error(f"Health check error: {e}")

def main():
    logger.info(f"Starting scheduler service")
    logger.info(f"Scheduled time: {SCHEDULER_HOUR:02d}:{SCHEDULER_MINUTE:02d} {SCHEDULER_TIMEZONE}")
    logger.info(f"API base: {API_BASE}")

    scheduler = BlockingScheduler(timezone=pytz.timezone(SCHEDULER_TIMEZONE))

    # Schedule daily trading pipeline
    scheduler.add_job(
        run_trading_pipeline,
        CronTrigger(
            hour=SCHEDULER_HOUR,
            minute=SCHEDULER_MINUTE,
            day_of_week='mon-fri',  # Weekdays only
            timezone=SCHEDULER_TIMEZONE
        ),
        id='daily_trading_pipeline',
        name='Daily Trading Pipeline',
        misfire_grace_time=300  # 5 minutes grace period
    )

    # Schedule health checks every 5 minutes
    scheduler.add_job(
        health_check,
        'interval',
        minutes=5,
        id='health_check',
        name='Health Check'
    )

    # Log scheduled jobs
    logger.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        logger.info(f"  - {job.name}: {job.trigger}")

    try:
        logger.info("Scheduler started, waiting for jobs...")
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Scheduler error: {e}", exc_info=True)
        scheduler.shutdown()

if __name__ == "__main__":
    main()