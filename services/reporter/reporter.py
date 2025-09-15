import os
import sys
import logging
import json
from datetime import datetime, timezone
from typing import Dict, List, Any
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import httpx

sys.path.append('/app')
from libs.database import get_session, TradePlan, Order, Position

logging.basicConfig(level=os.getenv('LOG_LEVEL', 'INFO'))
logger = logging.getLogger(__name__)

class Reporter:
    def __init__(self):
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        self.email_config = self._get_email_config()

    def _get_email_config(self) -> Dict:
        return {
            'smtp_host': os.getenv('EMAIL_SMTP_HOST'),
            'smtp_port': int(os.getenv('EMAIL_SMTP_PORT', '587')),
            'from_addr': os.getenv('EMAIL_FROM'),
            'to_addr': os.getenv('EMAIL_TO'),
            'password': os.getenv('EMAIL_PASSWORD')
        }

    def generate_daily_report(self, trade_plan: Dict, execution_results: Dict = None) -> str:
        """Generate markdown daily report"""
        report_lines = []

        # Header
        report_lines.append("# Daily Trading Report")
        report_lines.append(f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        report_lines.append(f"**Mode:** {trade_plan.get('mode', 'paper').upper()}")
        report_lines.append("")

        # Executive Summary
        report_lines.append("## Executive Summary")
        report_lines.append(f"- **Orders Generated:** {len(trade_plan.get('orders', []))}")
        if execution_results:
            report_lines.append(f"- **Orders Executed:** {len(execution_results.get('submitted', []))}")
            report_lines.append(f"- **Orders Failed:** {len(execution_results.get('failed', []))}")

        # Risk Metrics
        risk_metrics = trade_plan.get('risk_metrics', {})
        report_lines.append("")
        report_lines.append("## Risk Metrics")
        report_lines.append(f"- **Gross Exposure:** {risk_metrics.get('gross_exposure', 0):.1%}")
        report_lines.append(f"- **Net Exposure:** {risk_metrics.get('net_exposure', 0):.1%}")
        report_lines.append(f"- **Position Count:** {risk_metrics.get('position_count', 0)}")

        # Top Signals
        report_lines.append("")
        report_lines.append("## Top Trading Signals")
        signals = trade_plan.get('signals', [])[:5]
        for i, signal in enumerate(signals, 1):
            report_lines.append(f"{i}. **{signal['symbol']}** - {signal['action'].upper()} (Score: {signal.get('score', 0):.2f})")
            if signal.get('rationale'):
                report_lines.append(f"   - {signal['rationale']}")

        # Orders
        report_lines.append("")
        report_lines.append("## Orders")
        orders = trade_plan.get('orders', [])
        if orders:
            report_lines.append("| Symbol | Side | Quantity | Type | Confidence |")
            report_lines.append("|--------|------|----------|------|------------|")
            for order in orders[:10]:
                report_lines.append(
                    f"| {order['symbol']} | {order['side']} | {order['qty']} | "
                    f"{order.get('order_type', 'market')} | {order.get('confidence', 0):.2f} |"
                )
        else:
            report_lines.append("*No orders generated today*")

        # Performance Metrics
        perf_metrics = trade_plan.get('performance_metrics', {})
        if perf_metrics:
            report_lines.append("")
            report_lines.append("## Performance Metrics")
            report_lines.append(f"- **Sharpe Ratio (60d):** {perf_metrics.get('sharpe_60d', 0):.2f}")
            report_lines.append(f"- **Max Drawdown (60d):** {perf_metrics.get('max_dd_60d', 0):.1%}")
            report_lines.append(f"- **Win Rate:** {perf_metrics.get('win_rate', 0):.1%}")

        # Notes
        if trade_plan.get('notes'):
            report_lines.append("")
            report_lines.append("## Notes")
            report_lines.append(trade_plan['notes'])

        # Footer
        report_lines.append("")
        report_lines.append("---")
        report_lines.append(f"*Report generated at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC*")

        return "\n".join(report_lines)

    def save_report(self, report_content: str, report_type: str = "daily"):
        """Save report to storage"""
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        report_dir = f"/app/storage/reports/{date_str}"
        os.makedirs(report_dir, exist_ok=True)

        filename = f"{report_dir}/{report_type}_report.md"
        with open(filename, 'w') as f:
            f.write(report_content)

        logger.info(f"Report saved to {filename}")
        return filename

    def save_trade_plan(self, trade_plan: Dict):
        """Save trade plan as JSON"""
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        plan_dir = f"/app/storage/plans/{date_str}"
        os.makedirs(plan_dir, exist_ok=True)

        filename = f"{plan_dir}/trade_plan.json"
        with open(filename, 'w') as f:
            json.dump(trade_plan, f, indent=2, default=str)

        logger.info(f"Trade plan saved to {filename}")
        return filename

    def send_slack_notification(self, message: str, report_url: str = None):
        """Send notification to Slack"""
        if not self.slack_webhook:
            logger.debug("Slack webhook not configured")
            return

        try:
            payload = {
                "text": message,
                "attachments": []
            }

            if report_url:
                payload["attachments"].append({
                    "title": "View Full Report",
                    "title_link": report_url,
                    "color": "good"
                })

            response = httpx.post(self.slack_webhook, json=payload)
            if response.status_code == 200:
                logger.info("Slack notification sent successfully")
            else:
                logger.error(f"Slack notification failed: {response.status_code}")

        except Exception as e:
            logger.error(f"Error sending Slack notification: {e}")

    def send_email_report(self, subject: str, body: str):
        """Send email report"""
        config = self.email_config

        if not all([config['smtp_host'], config['from_addr'], config['to_addr']]):
            logger.debug("Email not configured")
            return

        try:
            msg = MIMEMultipart()
            msg['From'] = config['from_addr']
            msg['To'] = config['to_addr']
            msg['Subject'] = subject

            msg.attach(MIMEText(body, 'plain'))

            with smtplib.SMTP(config['smtp_host'], config['smtp_port']) as server:
                server.starttls()
                if config['password']:
                    server.login(config['from_addr'], config['password'])
                server.send_message(msg)

            logger.info(f"Email sent to {config['to_addr']}")

        except Exception as e:
            logger.error(f"Error sending email: {e}")

    def generate_summary_message(self, trade_plan: Dict) -> str:
        """Generate a summary message for notifications"""
        orders = trade_plan.get('orders', [])
        risk = trade_plan.get('risk_metrics', {})

        summary = f"📊 Trading Report - {trade_plan.get('mode', 'paper').upper()}\n"
        summary += f"• Orders: {len(orders)}\n"
        summary += f"• Gross Exposure: {risk.get('gross_exposure', 0):.1%}\n"
        summary += f"• Net Exposure: {risk.get('net_exposure', 0):.1%}\n"

        if orders:
            top_orders = orders[:3]
            summary += "\nTop Orders:\n"
            for order in top_orders:
                summary += f"• {order['symbol']} - {order['side']} {order['qty']} shares\n"

        return summary

    def process_daily_reports(self, trade_plan: Dict, execution_results: Dict = None):
        """Process and distribute all daily reports"""
        logger.info("Processing daily reports")

        # Generate report
        report_content = self.generate_daily_report(trade_plan, execution_results)

        # Save files
        report_file = self.save_report(report_content)
        plan_file = self.save_trade_plan(trade_plan)

        # Send notifications
        summary = self.generate_summary_message(trade_plan)

        # Slack
        self.send_slack_notification(summary)

        # Email
        subject = f"Trading Report - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        self.send_email_report(subject, report_content)

        logger.info("Daily reports completed")

        return {
            'report_file': report_file,
            'plan_file': plan_file,
            'notifications_sent': True
        }

if __name__ == "__main__":
    try:
        reporter = Reporter()

        # Test with sample trade plan
        sample_plan = {
            'mode': 'paper',
            'orders': [
                {'symbol': 'AAPL', 'side': 'buy', 'qty': 10, 'confidence': 0.75},
                {'symbol': 'MSFT', 'side': 'buy', 'qty': 5, 'confidence': 0.68}
            ],
            'risk_metrics': {
                'gross_exposure': 0.45,
                'net_exposure': 0.30,
                'position_count': 12
            },
            'signals': [
                {'symbol': 'AAPL', 'action': 'buy', 'score': 0.75, 'rationale': 'Strong momentum'},
                {'symbol': 'MSFT', 'action': 'buy', 'score': 0.68, 'rationale': 'Oversold conditions'}
            ]
        }

        reporter.process_daily_reports(sample_plan)
        logger.info("Reporter test completed")
    except Exception as e:
        logger.error(f"Reporter service failed: {e}")

    # Keep the service running but idle
    logger.info("Reporter service ready and waiting...")
    import time
    while True:
        time.sleep(3600)  # Sleep for an hour