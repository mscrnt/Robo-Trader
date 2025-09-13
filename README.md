# Robo Trader — DeepSeek/Ollama + Alpaca (Paper default • Live opt‑in • Auto‑exec)

> ⚠️ **Educational / Research Use.** This project can **automatically place trades**. It defaults to **paper** trading; switching to **live** is entirely controlled by your `.env`. This is **not financial advice**. You are responsible for compliance with your broker’s ToS and local laws.

## Overview

An end‑to‑end, Dockerized **robo trader** that runs daily, generates a risk‑aware trade plan using a local LLM (**DeepSeek via Ollama**), produces a human‑readable report, and—when enabled—**automatically executes** orders via Alpaca (**paper by default, live opt‑in**). Includes a **Flask UI** to review account state (equity, cash, PnL), positions, orders, signals, and generated plans, plus controls to **pause/resume** trading at runtime.

**Key capabilities**

* Data ingest: prices, events (earnings/splits/dividends), news headlines, SEC filings digests
* Factor/Signal engine: momentum, RSI, MACD, gap/volume, earnings drift, simple news sentiment
* Risk & sizing: per‑name caps, sector caps, gross/net exposure, volatility‑targeted sizing, circuit breakers
* Backtest sanity: rolling 60/252 trading‑day metrics (Sharpe, hit‑rate, max DD, turnover)
* Plan & Report: `trade_plan.json` + `report.md` artifacts; optional Slack/Email push
* **Auto‑execution**: controlled by `.env`; **paper** or **live**; idempotent orders & fill reconciliation
* **Runtime controls**: `GLOBAL_KILL_SWITCH` (pause), daily drawdown halt, UI pause/resume
* **Flask UI**: dashboard, positions, orders, signals, latest plan/report, settings banner for mode/guards

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

# LLM (Ollama or compatible)
LLM_BASE_URL=http://ollama:11434
LLM_MODEL=deepseek-r1:latest
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

### `configs/strategy.yaml` (sample)

```yaml
universe: configs/universe.yaml
risk:
  max_single_name: 0.02      # 2% of equity per position
  gross_max: 0.60            # 60% gross exposure
  net_max: 0.40              # 40% net exposure
  daily_drawdown_halt: 0.02  # stop trading for the day at -2%
  sector_caps:
    XLK: 0.30
    XLV: 0.30
sizing:
  method: vol_target
  target_vol: 0.20
factors:
  momentum_12_1: { weight: 0.35 }
  rsi_14:        { weight: 0.15 }
  macd:          { weight: 0.15 }
  gap_vol:       { weight: 0.15 }
  earnings_drift:{ weight: 0.10 }
  news_sentiment:{ weight: 0.10 }
execution:
  time_in_force: day
  default_stop_pct: 0.03
  default_take_pct: 0.05
backtest:
  windows: [60, 252]
```

### `configs/universe.yaml` (sample)

```yaml
tickers:
  - AAPL
  - MSFT
  - NVDA
  - AMZN
  - GOOGL
  - META
filters:
  min_price: 2.0
  min_adv_usd: 2_000_000
exclusions: []
```

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

## Extensibility

* Add a factor: implement in `services/signals/factors/` and register in `strategy.yaml`.
* New broker: implement `Broker` interface (`positions`, `balances`, `place`, `cancel`, `stream`) under `services/broker/`.

## Security & Compliance

* Keep keys in `.env` or Docker secrets; never commit secrets.
* Prefer official APIs to scraping; respect vendor ToS.
* **Live trading** requires explicit `.env` toggles as shown above.

## Disclaimer

This software is provided “as is” without warranty. Markets carry risk. Backtests are not indicative of future results. Use at your own risk.
