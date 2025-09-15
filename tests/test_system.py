#!/usr/bin/env python3
"""
System Integration Test
Tests the complete trading pipeline with data providers and signals
"""

import os
import sys
import time
import requests
import json

# Test configuration
API_BASE = "http://localhost:8000"
WEB_BASE = "http://localhost:8080"

def test_health_check():
    """Test API health endpoint"""
    print("Testing API health check...")
    try:
        response = requests.get(f"{API_BASE}/health")
        if response.status_code == 200:
            print("✓ API is healthy")
            return True
        else:
            print(f"✗ API health check failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Cannot reach API: {e}")
        return False

def test_data_ingestion():
    """Test market data ingestion"""
    print("\nTesting data ingestion...")
    try:
        # Trigger pipeline run
        response = requests.post(
            f"{API_BASE}/run",
            headers={"Content-Type": "application/json"},
            json={"force": True, "dry_run": True}
        )

        if response.status_code == 200:
            result = response.json()
            print(f"✓ Pipeline started: {result.get('status')}")
            return True
        else:
            print(f"✗ Pipeline failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Pipeline error: {e}")
        return False

def test_positions():
    """Test positions endpoint"""
    print("\nTesting positions endpoint...")
    try:
        response = requests.get(f"{API_BASE}/positions")
        if response.status_code == 200:
            positions = response.json()
            print(f"✓ Got {len(positions)} positions")
            return True
        else:
            print(f"✗ Positions failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Positions error: {e}")
        return False

def test_signals():
    """Test signals endpoint"""
    print("\nTesting signals endpoint...")
    try:
        response = requests.get(f"{API_BASE}/signals")
        if response.status_code == 200:
            signals = response.json()
            print(f"✓ Got {len(signals)} signals")
            for signal in signals[:3]:
                print(f"  - {signal.get('symbol')}: {signal.get('action')} "
                      f"(score: {signal.get('score', 0):.2f})")
            return True
        else:
            print(f"✗ Signals failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Signals error: {e}")
        return False

def test_plan():
    """Test trade plan endpoint"""
    print("\nTesting trade plan...")
    try:
        response = requests.get(f"{API_BASE}/plan/latest")
        if response.status_code == 200:
            plan = response.json()
            print(f"✓ Got trade plan: {plan.get('plan_date')}")
            print(f"  Orders: {len(plan.get('orders', []))}")
            print(f"  Mode: {plan.get('mode')}")
            return True
        elif response.status_code == 404:
            print("✓ No trade plan yet (expected)")
            return True
        else:
            print(f"✗ Plan failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Plan error: {e}")
        return False

def test_web_ui():
    """Test web UI availability"""
    print("\nTesting Web UI...")
    pages = ['/', '/positions', '/orders', '/signals', '/plan', '/settings']
    success = True

    for page in pages:
        try:
            response = requests.get(f"{WEB_BASE}{page}")
            if response.status_code == 200:
                print(f"✓ {page} - OK")
            else:
                print(f"✗ {page} - Failed ({response.status_code})")
                success = False
        except Exception as e:
            print(f"✗ {page} - Error: {e}")
            success = False

    return success

def test_data_providers():
    """Test data provider availability"""
    print("\nTesting data providers...")

    # Check environment variables
    providers = {
        'Alpaca': os.getenv('ALPACA_KEY_ID'),
        'Finnhub': os.getenv('FINNHUB_API_KEY'),
        'Alpha Vantage': os.getenv('ALPHA_VANTAGE_API_KEY'),
        'Polygon': os.getenv('POLYGON_API_KEY')
    }

    configured = []
    for name, key in providers.items():
        if key and key != f'your_{name.lower().replace(" ", "_")}_api_key':
            print(f"✓ {name} configured")
            configured.append(name)
        else:
            print(f"✗ {name} not configured")

    print(f"\nConfigured providers: {', '.join(configured)}")
    return len(configured) > 0

def test_database():
    """Test database connectivity"""
    print("\nTesting database...")
    try:
        # This would normally connect directly, but we'll use the API
        response = requests.get(f"{API_BASE}/health")
        if response.status_code == 200:
            print("✓ Database accessible (via API)")
            return True
        return False
    except:
        return False

def main():
    print("=" * 50)
    print("ROBO TRADER SYSTEM TEST")
    print("=" * 50)

    tests = [
        ("Health Check", test_health_check),
        ("Database", test_database),
        ("Data Providers", test_data_providers),
        ("Data Ingestion", test_data_ingestion),
        ("Positions", test_positions),
        ("Signals", test_signals),
        ("Trade Plan", test_plan),
        ("Web UI", test_web_ui)
    ]

    results = []
    for name, test_func in tests:
        print(f"\n{'=' * 30}")
        result = test_func()
        results.append((name, result))
        time.sleep(1)  # Small delay between tests

    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{name:20} {status}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed! System is ready.")
    elif passed > total * 0.7:
        print("\n⚠️  Most tests passed. Check failed components.")
    else:
        print("\n❌ Multiple failures. System needs attention.")

    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())