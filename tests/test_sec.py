#!/usr/bin/env python3
"""
Test SEC EDGAR functionality
"""

import sys
import json
from datetime import datetime, timezone, timedelta

sys.path.append('/app')
from libs.sec_edgar import SECEdgarClient

def test_sec_edgar():
    """Test SEC EDGAR data fetching"""

    client = SECEdgarClient()

    print("=" * 60)
    print("Testing SEC EDGAR Client")
    print("=" * 60)

    # Test 1: Update company mapping
    print("\n1. Updating company ticker->CIK mapping...")
    if client.update_company_map():
        print(f"✓ Successfully loaded {len(client.ticker_to_cik)} ticker mappings")
        print(f"Sample tickers: {list(client.ticker_to_cik.keys())[:10]}")
    else:
        print("✗ Failed to update company map")

    # Test 2: Get CIK for common tickers
    test_tickers = ['AAPL', 'MSFT', 'TSLA', 'WBD', 'TTD']
    print("\n2. Testing ticker->CIK lookup:")
    for ticker in test_tickers:
        cik = client.get_cik(ticker)
        if cik:
            print(f"  {ticker}: CIK {cik}")
        else:
            print(f"  {ticker}: Not found")

    # Test 3: Get recent filings for AAPL
    print("\n3. Getting recent filings for AAPL...")
    aapl_cik = client.get_cik('AAPL')
    if aapl_cik:
        filings = client.get_company_submissions(aapl_cik, limit=10)
        print(f"✓ Found {len(filings)} recent filings")
        for filing in filings[:3]:
            print(f"  - {filing['form']} on {filing['filing_date']}")
            if filing.get('items'):
                print(f"    Items: {filing['items']}")

    # Test 4: Get company facts (fundamentals) for AAPL
    print("\n4. Getting company facts for AAPL...")
    if aapl_cik:
        facts = client.get_company_facts(aapl_cik)
        if facts:
            print("✓ Successfully retrieved company facts")
            # Show available fact categories
            if 'facts' in facts:
                categories = list(facts['facts'].keys())
                print(f"  Available categories: {categories}")

                # Try to get revenue data
                if 'us-gaap' in facts['facts']:
                    gaap_metrics = list(facts['facts']['us-gaap'].keys())[:10]
                    print(f"  Sample GAAP metrics: {gaap_metrics}")

                    # Look for revenue
                    if 'Revenues' in facts['facts']['us-gaap']:
                        revenue_data = facts['facts']['us-gaap']['Revenues']
                        if 'units' in revenue_data and 'USD' in revenue_data['units']:
                            recent_revenue = revenue_data['units']['USD'][-1] if revenue_data['units']['USD'] else None
                            if recent_revenue:
                                print(f"  Latest revenue: ${recent_revenue['val']:,.0f} ({recent_revenue.get('frame', 'N/A')})")
        else:
            print("✗ Failed to get company facts")

    # Test 5: Get recent filings for watchlist
    print("\n5. Getting recent filings for watchlist (last 48 hours)...")
    watchlist = ['TSLA', 'MSFT', 'WBD']
    recent_filings = client.get_recent_filings_for_watchlist(watchlist, hours_back=48)

    for ticker, filings in recent_filings.items():
        if filings:
            print(f"  {ticker}: {len(filings)} recent filings")
            for filing in filings[:2]:
                print(f"    - {filing['form']} on {filing['filing_date']}")
                if filing.get('items'):
                    print(f"      Items: {filing['items']}")

    # Test 6: Get recent 8-K filings (material events)
    print("\n6. Checking for recent 8-K filings (material events)...")
    for ticker in ['AAPL', 'TSLA', 'MSFT']:
        cik = client.get_cik(ticker)
        if cik:
            submissions = client.get_company_submissions(cik, limit=20)
            eight_ks = [f for f in submissions if '8-K' in f['form']]
            if eight_ks:
                print(f"  {ticker}: {len(eight_ks)} recent 8-Ks")
                for filing in eight_ks[:1]:
                    print(f"    - {filing['filing_date']}: Items {filing.get('items', 'N/A')}")
                    print(f"      URL: {filing['url']}")

    print("\n" + "=" * 60)
    print("SEC EDGAR Test Complete")
    print("=" * 60)

if __name__ == "__main__":
    test_sec_edgar()