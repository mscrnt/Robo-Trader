"""
SEC EDGAR Data Client
Official SEC APIs for filings, fundamentals, and company data
No scraping, fully compliant with SEC rate limits
"""

import os
import sys
import json
import logging
import requests
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
from collections import defaultdict
import hashlib

sys.path.append('/app')

logger = logging.getLogger(__name__)

class SECEdgarClient:
    """
    SEC EDGAR API client following official guidelines
    - Declares User-Agent with contact info
    - Respects 10 requests/second limit
    - Uses official JSON endpoints (no HTML scraping)
    """

    def __init__(self):
        # SEC requires User-Agent with contact info
        self.headers = {
            "User-Agent": "RoboTrader/1.0 (Contact: trader@roboexample.com)"
        }
        self.base_url = "https://data.sec.gov"
        self.archives_url = "https://www.sec.gov/Archives/edgar"

        # Rate limiting: SEC allows 10 requests/second
        self.last_request = 0
        self.min_delay = 0.12  # ~8 requests/second to be safe

        # Cache for ticker->CIK mapping
        self.ticker_to_cik = {}
        self.cik_to_ticker = {}
        self.company_map_updated = None

        logger.info("SEC EDGAR client initialized")

    def _throttle(self):
        """Respect SEC rate limits (10 req/sec max)"""
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self.last_request = time.time()

    def _make_request(self, url: str, timeout: int = 30) -> Optional[Dict]:
        """Make throttled request with proper headers"""
        self._throttle()
        try:
            response = requests.get(url, headers=self.headers, timeout=timeout)
            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"SEC request failed: {url} returned {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"SEC request error for {url}: {e}")
            return None

    def update_company_map(self) -> bool:
        """
        Download and cache ticker->CIK mapping
        Should be updated weekly
        """
        url = "https://www.sec.gov/files/company_tickers.json"
        data = self._make_request(url)

        if data:
            self.ticker_to_cik = {}
            self.cik_to_ticker = {}

            for entry in data.values():
                ticker = entry.get('ticker', '').upper()
                cik = str(entry.get('cik_str', '')).zfill(10)  # Pad to 10 digits

                if ticker and cik:
                    self.ticker_to_cik[ticker] = cik
                    self.cik_to_ticker[cik] = ticker

            self.company_map_updated = datetime.now(timezone.utc)
            logger.info(f"Updated company map: {len(self.ticker_to_cik)} tickers")
            return True

        return False

    def get_cik(self, ticker: str) -> Optional[str]:
        """Get CIK for ticker (update map if needed)"""
        # Update map if not loaded or older than 7 days
        if not self.company_map_updated or \
           (datetime.now(timezone.utc) - self.company_map_updated).days > 7:
            self.update_company_map()

        return self.ticker_to_cik.get(ticker.upper())

    def get_daily_index(self, date: datetime) -> List[Dict]:
        """
        Get daily index of all filings for a specific date
        Returns list of filing entries
        """
        # Format: /Archives/edgar/daily-index/YYYY/QTRX/master.YYYY-MM-DD.json
        year = date.year
        quarter = f"QTR{((date.month - 1) // 3) + 1}"
        date_str = date.strftime("%Y-%m-%d")

        url = f"{self.archives_url}/daily-index/{year}/{quarter}/master.{date_str}.json"
        data = self._make_request(url)

        if data and 'directory' in data:
            filings = []
            for item in data['directory']['item']:
                # Filter for important forms
                form_type = item.get('type', '').upper()
                if any(f in form_type for f in ['8-K', '10-Q', '10-K', '13D', '13G', 'DEF 14A']):
                    filings.append({
                        'cik': item.get('cik'),
                        'company': item.get('name'),
                        'form': form_type,
                        'date': item.get('date'),
                        'url': f"{self.archives_url}/{item.get('href')}"
                    })

            logger.info(f"Found {len(filings)} relevant filings for {date_str}")
            return filings

        return []

    def get_company_submissions(self, cik: str, limit: int = 100) -> List[Dict]:
        """
        Get recent submissions for a specific company
        This endpoint updates in near real-time
        """
        cik = cik.zfill(10)  # Ensure 10 digits
        url = f"{self.base_url}/submissions/CIK{cik}.json"
        data = self._make_request(url)

        if data and 'filings' in data and 'recent' in data['filings']:
            recent = data['filings']['recent']
            filings = []

            # Get the number of filings (up to limit)
            n = min(len(recent.get('form', [])), limit)

            for i in range(n):
                form_type = recent['form'][i]
                # Filter for important forms
                if any(f in form_type.upper() for f in ['8-K', '10-Q', '10-K', '13D', '13G']):
                    filings.append({
                        'form': form_type,
                        'filing_date': recent['filingDate'][i],
                        'accession': recent['accessionNumber'][i],
                        'primary_doc': recent.get('primaryDocument', [None] * n)[i],
                        'items': recent.get('items', [None] * n)[i],  # 8-K items
                        'url': f"{self.archives_url}/data/{cik}/{recent['accessionNumber'][i].replace('-', '')}/{recent['accessionNumber'][i]}-index.htm"
                    })

            return filings

        return []

    def get_company_facts(self, cik: str) -> Optional[Dict]:
        """
        Get structured fundamentals (XBRL data) for a company
        Includes all financial metrics
        """
        cik = cik.zfill(10)
        url = f"{self.base_url}/api/xbrl/companyfacts/CIK{cik}.json"
        return self._make_request(url)

    def get_recent_filings_for_watchlist(self, tickers: List[str],
                                        hours_back: int = 24) -> Dict[str, List[Dict]]:
        """
        Get recent filings for a list of tickers
        Perfect for pre-market updates
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
        filings_by_ticker = defaultdict(list)

        for ticker in tickers:
            cik = self.get_cik(ticker)
            if not cik:
                logger.debug(f"No CIK found for {ticker}")
                continue

            submissions = self.get_company_submissions(cik, limit=50)

            for filing in submissions:
                # Parse filing date
                try:
                    filing_date = datetime.strptime(filing['filing_date'], '%Y-%m-%d')
                    filing_date = filing_date.replace(tzinfo=timezone.utc)

                    if filing_date >= cutoff:
                        filing['ticker'] = ticker
                        filings_by_ticker[ticker].append(filing)
                except:
                    pass

        logger.info(f"Found recent filings for {len(filings_by_ticker)} companies")
        return dict(filings_by_ticker)

    def extract_8k_items(self, filing: Dict) -> List[str]:
        """
        Extract 8-K item descriptions
        Item 2.02 = Results of Operations
        Item 5.02 = Officer changes
        Item 8.01 = Other Events
        etc.
        """
        items = filing.get('items', '')
        if not items:
            return []

        # Map common item codes to descriptions
        item_map = {
            '1.01': 'Entry into Material Agreement',
            '1.02': 'Termination of Material Agreement',
            '2.01': 'Completion of Acquisition',
            '2.02': 'Results of Operations',
            '2.03': 'Material Impairments',
            '3.01': 'Notice of Delisting',
            '4.01': 'Changes in Accountant',
            '5.01': 'Changes in Control',
            '5.02': 'Officer Departure/Appointment',
            '5.03': 'Amendments to Articles',
            '5.07': 'Submission to Stockholder Vote',
            '7.01': 'Regulation FD Disclosure',
            '8.01': 'Other Events'
        }

        descriptions = []
        for item_code in items.split(','):
            item_code = item_code.strip()
            if item_code in item_map:
                descriptions.append(f"{item_code}: {item_map[item_code]}")
            else:
                descriptions.append(item_code)

        return descriptions


def test_sec_client():
    """Test SEC EDGAR client"""
    client = SECEdgarClient()

    # Test company map
    print("Updating company map...")
    client.update_company_map()

    # Test ticker to CIK
    test_tickers = ['AAPL', 'MSFT', 'GOOGL', 'TSLA']
    for ticker in test_tickers:
        cik = client.get_cik(ticker)
        print(f"{ticker}: CIK {cik}")

    # Test recent filings
    print("\nGetting recent filings for AAPL...")
    cik = client.get_cik('AAPL')
    if cik:
        filings = client.get_company_submissions(cik, limit=5)
        for f in filings:
            print(f"  {f['filing_date']}: {f['form']}")
            if f['form'] == '8-K' and f.get('items'):
                items = client.extract_8k_items(f)
                for item in items:
                    print(f"    - {item}")

    # Test watchlist filings
    print("\nGetting 24hr filings for watchlist...")
    recent = client.get_recent_filings_for_watchlist(['AAPL', 'MSFT', 'GOOGL'], hours_back=48)
    for ticker, filings in recent.items():
        if filings:
            print(f"{ticker}: {len(filings)} recent filings")


if __name__ == "__main__":
    test_sec_client()