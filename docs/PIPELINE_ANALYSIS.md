# Robo Trader Pipeline Analysis
*Generated: 2025-09-14*

## Current Pipeline Status

### ✅ Working Components
1. **RSS Feed Ingestion** - Successfully fetching from 9 feeds
2. **LLM Ticker Extraction** - deepseek-v2:16b extracting tickers from news
3. **Database Watchlist** - 19 symbols stored with scores
4. **YFinance Data** - 764 price records (41 days × 19 symbols)
5. **Database State Management** - RSS states and seen articles in DB

### ⚠️ Issues Identified
1. **Alpha Vantage MCP** - Returns "No data" for all symbols (not rate limit message)
2. ~~**Signals Service** - Waiting for 100+ records (stuck at 95)~~ **FIXED** - Lowered threshold to 50
3. ~~**No Signal Generation** - 0 signals in database~~ **FIXED** - 72 signals now generating
4. **API Container** - Needs rebuild after pipeline.py fixes (remove NewsSignal references)
5. **No Trading Execution** - Pipeline creates plans but with 0 orders

## Detailed Pipeline Flow

### Stage 1: News Discovery (✅ WORKING)
```
RSS Feeds (9 sources) → LLM Ticker Extraction → Database Watchlist
```

**Current State:**
- Fetching 197 articles from RSS feeds
- LLM successfully extracting tickers (WBD, TTD, MNST, ADBE, TSLA, etc.)
- Using database for state management (no JSON files)
- ETags stored for efficient conditional fetching

**Key Files:**
- `libs/rss_manager.py` - RSS feed management with LLM extraction
- `libs/news_watchlist.py` - Builds dynamic watchlist
- `configs/rss_feeds.yaml` - Feed configuration

### Stage 2: Market Data Ingestion (⚠️ PARTIAL)
```
YFinance (2000/hr) → Finnhub (30/sec) → Alpha Vantage MCP (25/day)
```

**Current State:**
- ✅ YFinance: Successfully fetched 41 days for 19 symbols
- ❓ Finnhub: Not attempted (no API key configured)
- ❌ Alpha Vantage MCP: Returns no data (19/25 calls used)

**Alpha Vantage Issue:**
- API responds with 200 OK but no data
- Not showing rate limit error
- Possible causes:
  - Weekend/after-hours (some APIs don't work on weekends)
  - Invalid symbols (PSKY, BMNR, BTDR might not exist)
  - API key issue

**Key Files:**
- `services/ingest/ingest.py` - Main ingestion orchestrator
- `libs/mcp_client.py` - Alpha Vantage MCP client
- `libs/data_providers.py` - Multi-source data fetching

### Stage 3: Signal Generation (❌ NOT RUNNING)
```
Factor Calculation → LLM Analysis → Signal Scoring
```

**Current State:**
- Signals service waiting for 100+ price records
- Currently stuck at 95 records
- No signals generated (0 in database)
- LLM copilot configured but not triggered

**Configuration:**
- Summary Model: deepseek-v2:16b (for news summarization)
- Selector Model: deepseek-r1:32b (for final selection)
- Model warm-up strategy implemented

**Key Files:**
- `services/signals/signals.py` - Signal generation
- `services/signals/llm_copilot.py` - LLM-powered analysis
- `services/signals/factors/` - Technical factors

### Stage 4: Risk Management (❓ NOT TESTED)
```
Position Sizing → Exposure Limits → Circuit Breakers
```

**Configuration:**
- Max single position: 2%
- Gross exposure max: 60%
- Net exposure max: 40%
- Daily drawdown halt: 2%
- Global kill switch: Available via Redis

**Key Files:**
- `services/risk/risk_manager.py` - Risk calculations
- Environment variables for limits

### Stage 5: Trade Execution (❓ NOT TESTED)
```
Order Generation → Broker Submission → Fill Tracking
```

**Current State:**
- Alpaca broker implemented
- Paper/Live mode switching
- Auto-execute flag (default: true)
- No trades executed yet

**Safety Features:**
- Three-level confirmation for live trading
- Global kill switch via Redis
- Order tracking in database

**Key Files:**
- `services/broker/alpaca_broker.py` - Alpaca integration
- `services/api/main.py` - API endpoints
- `services/api/pipeline.py` - Pipeline orchestration

## Database Schema

### Current Tables
```sql
watchlist          - 19 symbols with scores
price_data         - 764 records (41 days × 19 symbols)
signals            - 0 records (not generating)
rss_feed_state     - 9 feeds with ETags
seen_articles      - Deduplication tracking
trade_plans        - Trade execution plans
orders             - Order tracking
positions          - Position tracking
```

## Environment Configuration

### Critical Settings
```bash
# Execution Control
AUTO_EXECUTE=true                    # Auto-execute trades
TRADING_MODE=paper                   # paper/live
GLOBAL_KILL_SWITCH=false             # Emergency stop

# LLM Configuration
LLM_BASE_URL=http://192.168.69.197:11434
LLM_SUMMARY_MODEL=deepseek-v2:16b   # Fast summarization
LLM_SELECTOR_MODEL=deepseek-r1:32b  # Accurate selection

# Risk Limits
RISK_MAX_SINGLE_NAME=0.02
RISK_GROSS_MAX=0.60
RISK_NET_MAX=0.40
DAILY_DRAWDOWN_HALT=0.02
```

## API Endpoints

### Available
- `POST /run` - Execute trading pipeline
- `GET /health` - System health check
- `GET /plan/latest` - Latest trade plan
- `GET /positions` - Current positions
- `GET /signals` - Trading signals
- `GET /orders` - Order history
- `POST /control/pause` - Set kill switch
- `POST /control/resume` - Clear kill switch

## Immediate Issues to Fix

### 1. Signals Service Threshold
The signals service waits for 100+ records but only has 95:
```python
# In services/signals/startup.py
if count >= 100:  # Should be lowered or made configurable
```

### 2. Alpha Vantage MCP
- Check if API works on weekends
- Validate symbols exist
- Consider fallback to free tier instead of MCP

### 3. Complete Pipeline
No automated flow from signals → risk → execution:
- Signals not generating
- Risk module not integrated
- Broker not receiving orders

## Recommendations

### Short Term (Immediate)
1. **Lower signals threshold** to 50 records to start generating
2. **Debug Alpha Vantage** - test with known good symbols
3. **Manual pipeline test** - use API to trigger full flow

### Medium Term
1. **Implement scheduler** - automated daily runs
2. **Add monitoring** - track pipeline health
3. **Improve error handling** - better failure recovery

### Long Term
1. **Add backtesting** - validate strategies
2. **Implement paper trading validation** - compare with live
3. **Add performance tracking** - P&L, Sharpe, etc.

## Test Commands

### Check Pipeline Health
```bash
# Database status
docker compose exec db psql -U trader -d trader -c "
  SELECT 'Watchlist' as table, COUNT(*) FROM watchlist
  UNION SELECT 'Price Data', COUNT(*) FROM price_data
  UNION SELECT 'Signals', COUNT(*) FROM signals
  UNION SELECT 'Orders', COUNT(*) FROM orders;"

# Service logs
docker compose logs --tail 20 ingest signals broker

# Trigger pipeline
curl -X POST http://localhost:8000/run
```

### Manual Symbol Test
```bash
# Test Alpha Vantage directly
curl "https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=AAPL&apikey=YOUR_KEY"

# Test from container
docker compose exec ingest python -c "
from libs.mcp_client import AlphaVantageMCPClient
client = AlphaVantageMCPClient()
print(client.get_daily_bars(['AAPL', 'MSFT']))"
```

## Current Architecture
```
┌──────────────────────────────────────────────────┐
│                  News Sources                     │
│  RSS Feeds → SEC EDGAR → Yahoo → FinViz          │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│              LLM Ticker Extraction                │
│         deepseek-v2:16b (Summarization)          │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│             Database Watchlist                    │
│         19 symbols with weighted scores           │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│              Market Data Fetching                 │
│   YFinance ✓ → Finnhub ? → Alpha Vantage ✗       │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│             Signal Generation (BLOCKED)           │
│   Waiting for 100+ records (stuck at 95)         │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│         Risk Management (NOT REACHED)             │
│      Position sizing, exposure limits             │
└────────────────────┬─────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────┐
│         Trade Execution (NOT REACHED)             │
│           Alpaca (Paper/Live mode)                │
└──────────────────────────────────────────────────┘
```

## Conclusion

The pipeline is **partially operational**:
- ✅ News discovery and ticker extraction working well
- ✅ Database storage properly implemented
- ⚠️ Market data partially working (YFinance only)
- ❌ Signal generation blocked by threshold
- ❌ No trading execution yet

**Next Steps:**
1. Fix signals threshold (change 100 to 50)
2. Debug Alpha Vantage MCP issues
3. Test complete pipeline flow
4. Implement proper scheduling