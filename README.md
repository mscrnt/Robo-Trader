# Robo Trader — News-Driven Dynamic Trading with DeepSeek/Ollama + Alpaca

> ⚠️ **Educational / Research Use.** This project can **automatically place trades**. It defaults to **paper** trading; switching to **live** is entirely controlled by your `.env`. This is **not financial advice**. You are responsible for compliance with your broker's ToS and local laws.

## Overview

A fully automated, news-driven trading system that dynamically discovers opportunities from RSS feeds and SEC filings, generates signals using technical analysis, and executes trades via Alpaca. The system uses a two-model LLM pipeline (DeepSeek via Ollama) for intelligent news analysis and trade selection.

**Key Features**

* **Dynamic Symbol Discovery**: No hardcoded watchlists - discovers trending stocks from 9+ RSS news feeds
* **Two-Model LLM Pipeline**:
  - `deepseek-v2:16b` for fast news summarization and ticker extraction
  - `deepseek-r1:32b` for accurate final trade selection
* **Multi-Source Data Integration**:
  - YFinance (2000/hr) - Primary price data source
  - SEC EDGAR API - Real-time filings (8-K, 10-Q, 10-K)
  - RSS Feeds - Major financial news sources
  - Alpha Vantage MCP - Enhanced data (25/day limit)
* **Technical Analysis**: Momentum, RSI, MACD, volume surge indicators
* **Risk Management**: Position sizing (2% max), exposure limits (60% gross, 40% net), stop-loss/take-profit
* **Auto-Execution**: Places orders automatically via Alpaca (paper/live modes)
* **Flask UI**: Monitor positions, orders, signals, and control trading
* **Database-Driven**: PostgreSQL for all state management (no JSON files)

## Architecture

```
trader/
  services/
    api/        # FastAPI: /run /plan/latest /positions /orders /health /metrics
    web/        # Flask UI: overview, positions, orders, signals, plan, settings, pause/resume
    scheduler/  # APScheduler cron: 06:00 America/Los_Angeles (pre‑market)
    ingest/     # vendors -> Postgres cache (prices/news/events)
    signals/    # factor calc + LLM rationale (Ollama via env)
    risk/       # sizing, constraints, optimizer, circuit breakers
    broker/     # Alpaca adapter (paper/live switch via env)
    reporter/   # report emit (md/json) + Slack/Email
  libs/         # shared utils (logging, tz, io, validation, schemas)
  configs/      # .env.example, strategy.yaml, universe.yaml
  storage/      # plans/, reports/, backtests/, logs/
  docker/       # Dockerfiles per service
  docker-compose.yml
```

## Prerequisites

* Docker & Docker Compose
* Alpaca **Paper** account + API keys (Live optional)
* Local **Ollama** server (or any OpenAI‑compatible HTTP endpoint)
* (Optional) Slack/Email webhook for notifications

## Quick Start

1. **Clone & configure**

   ```bash
   cp configs/.env.example .env
   # Fill in ALPACA_*, DB_URL, REDIS_URL, TZ, and LLM_* vars (Ollama)
   ```
2. **Edit strategy & universe** (examples below) and save to `configs/`.
3. **Boot services**

   ```bash
   docker compose up -d --build
   ```
4. **Open the UI**

   * Flask UI: [http://localhost:8080](http://localhost:8080)
   * API (if exposed): [http://localhost:8000/health](http://localhost:8000/health)
5. **Run a cycle**

   * From UI: click **Run now**
   * Or via API: `POST http://localhost:8000/run`

### Execution modes & switches

* **Default**: paper + auto‑exec
* Disable auto‑exec (generate plan/report only): set `AUTO_EXECUTE=false`
* Enable **live** execution:

  ```env
  TRADING_MODE=live
  LIVE_TRADING_ENABLED=true
  LIVE_CONFIRM_PHRASE=I_UNDERSTAND_THE_RISKS
  ```
* **Emergency pause** (no orders placed): set `GLOBAL_KILL_SWITCH=true` (also available via UI Pause)

## Configuration

### `.env` (example)

```env
# Execution & mode
AUTO_EXECUTE=true
TRADING_MODE=paper                 # paper | live
LIVE_TRADING_ENABLED=false         # set true to allow live
LIVE_CONFIRM_PHRASE=I_UNDERSTAND_THE_RISKS
GLOBAL_KILL_SWITCH=false           # runtime pause

# Alpaca
ALPACA_KEY_ID=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_PAPER_BASE=https://paper-api.alpaca.markets
ALPACA_LIVE_BASE=https://api.alpaca.markets

# LLM Configuration (Two-model pipeline)
LLM_BASE_URL=http://192.168.69.197:11434  # Your Ollama server
LLM_SUMMARY_MODEL=deepseek-v2:16b        # Fast model for news summarization
LLM_SELECTOR_MODEL=deepseek-r1:32b       # Accurate model for trade selection
LLM_API_KEY=local-or-empty

# Time & storage
TZ=America/Los_Angeles
DB_URL=postgresql://trader:trader@db:5432/trader
REDIS_URL=redis://redis:6379/0

# Notifications
SLACK_WEBHOOK_URL=
EMAIL_SMTP_URL=

# Guardrails
RISK_MAX_SINGLE_NAME=0.02
RISK_GROSS_MAX=0.60
RISK_NET_MAX=0.40
DAILY_DRAWDOWN_HALT=0.02
```

### Data Sources Configuration

```yaml
# configs/rss_feeds.yaml - News sources for dynamic discovery
feeds:
  - name: sa_all_news
    url: https://seekingalpha.com/market_currents.xml
    weight: 1.0
  - name: marketwatch_top_stories
    url: https://feeds.content.dowjones.io/public/rss/mw_topstories
    weight: 0.7
  - name: yahoo_top_stories
    url: https://finance.yahoo.com/rss/topstories
    weight: 0.7
  # ... 9+ feeds total

# configs/strategy.yaml - Trading strategy
risk:
  max_single_name: 0.02      # 2% of equity per position
  gross_max: 0.60            # 60% gross exposure
  net_max: 0.40              # 40% net exposure
  daily_drawdown_halt: 0.02  # stop trading at -2%
factors:
  momentum:       { weight: 0.35, enabled: true }
  rsi:           { weight: 0.20, enabled: true }
  macd_histogram: { weight: 0.20, enabled: true }
  volume_surge:   { weight: 0.25, enabled: true }
execution:
  stop_loss_pct: 0.05       # 5% stop loss
  take_profit_pct: 0.15     # 15% take profit
```

**Note**: No `universe.yaml` needed - symbols are discovered dynamically from news!

## API Endpoints (core)

* `GET  /health` → uptime & versions
* `POST /run` → run daily DAG (ingest → signals → risk → backtest → plan → (auto‑exec) → report). Returns `{ plan_id }`.
* `GET  /plan/latest` → latest plan + report links
* `GET  /positions` → Alpaca positions snapshot
* `GET  /orders` → recent orders & fills
* `POST /control/pause` / `POST /control/resume` → toggle `GLOBAL_KILL_SWITCH`

**Example (curl)**

```bash
curl -X POST http://localhost:8000/run
curl       http://localhost:8000/plan/latest
curl       http://localhost:8000/positions
curl -X POST http://localhost:8000/control/pause
```

## Scheduler

* Default: **06:00 America/Los\_Angeles** (pre‑market) using APScheduler in `services/scheduler`.
* Override via env `SCHED_CRON` or schedule config.

## Artifacts & Storage

* `storage/plans/YYYY-MM-DD/trade_plan.json`
* `storage/reports/YYYY-MM-DD/report.md`
* `storage/backtests/YYYY-MM-DD/*.json`
* `storage/logs/*.ndjson`
* `storage/orders/YYYY-MM-DD/orders.csv`, `storage/fills/YYYY-MM-DD/fills.csv`

## Development & Testing

* **Tests**: `pytest` (unit + broker integration against Alpaca paper)
* **Style**: `ruff` + `black`
* **Observability**: JSON logs, Prometheus metrics via `/metrics`

## How It Works

### Daily Pipeline Flow

1. **News Discovery** (RSS + SEC)
   - Fetches from 9 RSS feeds (Seeking Alpha, MarketWatch, Yahoo, CNBC, etc.)
   - Checks SEC EDGAR for new 8-K, 10-Q, 10-K filings
   - Uses `deepseek-v2:16b` to extract ticker symbols from news

2. **Dynamic Watchlist Building**
   - No hardcoded symbols - purely news-driven
   - Weights symbols by mention frequency and recency
   - Stores in PostgreSQL `watchlist` table

3. **Market Data Fetching**
   - YFinance: Primary source (2000 requests/hour)
   - SEC EDGAR: Company fundamentals and filings
   - Alpha Vantage MCP: Enhanced data for top symbols (25/day limit)

4. **Signal Generation**
   - Technical indicators: Momentum, RSI, MACD, Volume Surge
   - Composite scoring (0.0 to 1.0)
   - Signals > 0.6 trigger buy orders

5. **Risk Management**
   - Position sizing: 2% max per symbol
   - Portfolio limits: 60% gross, 40% net exposure
   - Stop-loss: 5% below entry
   - Take-profit: 15% above entry

6. **Trade Execution**
   - Alpaca API (paper or live mode)
   - Market orders by default
   - Automatic order tracking and reconciliation

## Current Status (Production Ready)

✅ **Working Features:**
- Dynamic symbol discovery from news
- Two-model LLM pipeline for analysis
- Technical signal generation
- Risk-managed portfolio optimization
- Automated trade execution
- Database-driven state management
- Flask UI for monitoring

📊 **Example Performance** (from latest run):
- Discovered 14 symbols from news
- Generated 7 buy orders
- Allocated $11,467 (11.5% of $100k portfolio)
- Top signals: WBD (1.00), RNA (1.00), MPW (0.95), TSLA (0.90)

## Monitoring & Control

### Flask UI (http://localhost:8080)
- Dashboard: Account overview, P&L
- Positions: Current holdings
- Orders: Execution history
- Signals: Latest scores and factors
- Control: Pause/Resume trading

### Command Line
```bash
# Check system status
curl http://localhost:8000/health

# View latest trade plan
curl http://localhost:8000/plan/latest

# Pause trading
curl -X POST http://localhost:8000/control/pause

# Resume trading
curl -X POST http://localhost:8000/control/resume
```

## Troubleshooting

### Common Issues

1. **No trades executing**: Check if market is open (weekday 9:30 AM - 4:00 PM ET)
2. **Empty watchlist**: Verify RSS feeds are accessible and LLM is running
3. **Rate limits**: Alpha Vantage limited to 25/day, resets at midnight ET
4. **SEC data missing**: Normal on weekends (no filings published)

### Logs
```bash
# View all logs
docker compose logs -f

# Service-specific logs
docker compose logs -f signals
docker compose logs -f broker
docker compose logs -f api
```

## Security & Compliance

* Keep API keys in `.env` - never commit to git
* Uses official APIs only (SEC EDGAR, Alpaca, YFinance)
* **Live trading** requires three explicit confirmations
* All trades logged in PostgreSQL for audit trail

## Disclaimer

This software is provided "as is" without warranty. Markets carry risk. Past performance does not indicate future results. You are responsible for your trading decisions. Use at your own risk.
