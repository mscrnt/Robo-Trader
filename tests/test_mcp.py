#!/usr/bin/env python3
"""
Test Alpha Vantage MCP directly
"""

import os
import sys
import json
import asyncio
import httpx
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

async def test_mcp():
    """Test MCP calls directly"""

    # Get API key from environment
    api_key = os.getenv('ALPHA_VANTAGE_API_KEY', 'Y0MTYXCH5H2Q1CUE')
    mcp_url = f"https://mcp.alphavantage.co/mcp?apikey={api_key}"

    print(f"Testing Alpha Vantage MCP")
    print(f"API Key: {api_key[:6]}...")
    print(f"URL: {mcp_url}")
    print("-" * 60)

    async with httpx.AsyncClient() as client:
        # Test 1: List tools
        print("\n1. Testing tools/list:")
        response = await client.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/list",
                "id": 1
            },
            timeout=30.0
        )

        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            tools = data.get('result', {}).get('tools', [])
            print(f"Available tools: {[t.get('name') for t in tools]}")
        else:
            print(f"Response: {response.text[:500]}")

        # Test 2: Get quote for a known good symbol (AAPL)
        print("\n2. Testing GLOBAL_QUOTE for AAPL:")
        response = await client.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "GLOBAL_QUOTE",
                    "arguments": {"symbol": "AAPL"}
                },
                "id": 2
            },
            timeout=30.0
        )

        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"Response keys: {list(data.keys())}")
            result = data.get('result', {})
            if result:
                print(f"Result keys: {list(result.keys())}")
                content = result.get('content', [])
                if content:
                    print(f"Content type: {type(content)}")
                    if isinstance(content, list) and len(content) > 0:
                        print(f"First content item: {json.dumps(content[0], indent=2)[:500]}")
                else:
                    print("No content in result")
            else:
                print("No result in response")
                print(f"Full response: {json.dumps(data, indent=2)[:1000]}")
        else:
            print(f"Response: {response.text[:500]}")

        # Test 3: Get time series for AAPL
        print("\n3. Testing TIME_SERIES_DAILY for AAPL:")
        response = await client.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "TIME_SERIES_DAILY",
                    "arguments": {
                        "symbol": "AAPL",
                        "outputsize": "compact"
                    }
                },
                "id": 3
            },
            timeout=30.0
        )

        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            result = data.get('result', {})
            if result:
                content = result.get('content', [])
                if content and isinstance(content, list) and len(content) > 0:
                    text = content[0].get('text', '')
                    print(f"Content text length: {len(text)}")
                    print(f"First 500 chars: {text[:500]}")

                    # Try to parse as JSON
                    try:
                        parsed = json.loads(text)
                        print(f"Parsed JSON keys: {list(parsed.keys())}")
                        if 'Time Series (Daily)' in parsed:
                            ts = parsed['Time Series (Daily)']
                            dates = list(ts.keys())[:3]
                            print(f"First 3 dates: {dates}")
                            if dates:
                                print(f"Data for {dates[0]}: {ts[dates[0]]}")
                    except json.JSONDecodeError as e:
                        print(f"JSON decode error: {e}")
                else:
                    print("No content in result")
            else:
                print("No result in response")
                print(f"Full response: {json.dumps(data, indent=2)[:1000]}")
        else:
            print(f"Response: {response.text[:500]}")

        # Test 4: Test with a symbol from our watchlist (WBD)
        print("\n4. Testing TIME_SERIES_DAILY for WBD:")
        response = await client.post(
            mcp_url,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": "TIME_SERIES_DAILY",
                    "arguments": {
                        "symbol": "WBD",
                        "outputsize": "compact"
                    }
                },
                "id": 4
            },
            timeout=30.0
        )

        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            result = data.get('result', {})
            if result:
                content = result.get('content', [])
                if content and isinstance(content, list) and len(content) > 0:
                    text = content[0].get('text', '')
                    print(f"Content text length: {len(text)}")
                    if len(text) < 100:
                        print(f"Full text: {text}")
                    else:
                        print(f"First 500 chars: {text[:500]}")
                else:
                    print("No content in result")
            else:
                print("No result in response")
        else:
            print(f"Response: {response.text[:500]}")

if __name__ == "__main__":
    asyncio.run(test_mcp())