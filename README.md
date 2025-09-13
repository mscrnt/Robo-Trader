# Robo Trader (Paper) – DeepSeek + Alpaca

> ⚠️ **Educational / Research Use.** This project places **paper** trades only. It is **not financial advice.** You are responsible for compliance with your broker’s ToS and local laws.

## Overview

An end‑to‑end, Dockerized **robo trader** that runs daily, generates a risk‑aware trade plan using a local LLM (**DeepSeek**), produces a human‑readable report, and executes **paper orders** via **Alpaca Paper API** after manual approval.

**Key capabilities**

* Data ingest: prices, events (earnings/splits/dividends), news headlines, SEC filings digests
* Factor/Signal engine: momentum, RSI, MACD, gap/volume, earnings drift, simple news sentiment
* Risk & sizing: per‑name caps, sector caps, gross/net exposure, volatility‑targeted sizing
* Backtest sanity window: rolling 60/252 trading days
* Plan & Report: `trade_plan.json` + `report.md` artifacts, Slack/Email push
* Execution (paper): require **explicit approval** before placing Alpaca paper orders
* Auditability: inputs, scores, orders, fills, and metrics persisted

## Architecture

```
trader/
  services/
    api/            # FastAPI: approve/run, artifacts, health
    scheduler/      # APScheduler cron: 06:00 America/Los_Angeles
    ingest/         # vendors -> Postgres cache (prices/news/events)
    signals/        # factor calc + model scoring (DeepSeek-assisted)
    risk/           # sizing, constraints, optimizer, breakers
    broker/         # alpaca_paper adapter (positions/orders/fills)
    reporter/       # report emit (md/json) + Slack/Email
  libs/             # shared utils (logging, tz, io, validation)
  configs/          # .env.example, strategy.yaml, universe.yaml
  storage/          # plans/, reports/, backtests/, logs/
  docker/           # Dockerfiles per service
  docker-compose.yml
```

## Prerequisites

* Docker & Docker Compose
* Alpaca **Paper** account + API keys
* (Optional) Slack/Email webhook for notifications
* Local DeepSeek server (OpenAI‑compatible API preferred) or any HTTP inference endpoint

## Quick Start

1. **Clone & configure**

   ```bash
   cp configs/.env.example .env
   # Fill in ALPACA_*, DB_URL, REDIS_URL, TZ, and DeepSeek vars below
   ```
2. **Edit strategy & universe** (simple examples below) and save to `configs/`.
3. **Boot services**

   ```bash
   docker compose up -d --build
   ```
4. **Verify**

   * API health: `GET http://localhost:8080/health`
   * Dry run: `POST http://localhost:8080/run` (generates plan, no execution)
5. **Approve (paper only)**

   * `POST http://localhost:8080/approve` with the `plan_id` from `/run` or `/plan/latest`

## Configuration

**.env (example)**

```env
# Broker
ALPACA_KEY_ID=your_key
ALPACA_SECRET_KEY=your_secret
ALPACA_BASE_URL=https://paper-api.alpaca.markets

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
GLOBAL_KILL_SWITCH=false

# LLM (DeepSeek)
LLM_BASE_URL=http://deepseek:8000/v1
LLM_MODEL=deepseek-chat
LLM_API_KEY=local-or-empty
```

**`configs/strategy.yaml` (sample)**

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

**`configs/universe.yaml` (sample)**

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

## API Endpoints

* `GET /health` → uptime & versions
* `POST /run` → run daily DAG (ingest → signals → risk → backtest → plan → report). Returns `{ plan_id }`.
* `GET /plan/latest` → latest plan + report links
* `POST /approve` → `{ plan_id }` → places **paper** orders via Alpaca
* `GET /positions` → Alpaca paper positions snapshot

**Example (curl)**

```bash
curl -X POST http://localhost:8080/run
curl http://localhost:8080/plan/latest
curl -X POST http://localhost:8080/approve -H 'Content-Type: application/json' \
  -d '{"plan_id":"2025-09-13T06-00-01Z"}'
```

## Scheduler

* Default: **06:00 America/Los\_Angeles** (pre‑market) using APScheduler in `services/scheduler`.
* Override via env `SCHED_CRON` or an ISO next‑run.

## Artifacts & Storage

* `storage/plans/YYYY-MM-DD/plan.json`
* `storage/reports/YYYY-MM-DD/report.md`
* `storage/backtests/YYYY-MM-DD/*.json`
* `storage/logs/*.ndjson`

## Development & Testing

* **Tests**: `pytest` (unit + broker integration against Alpaca paper)
* **Style**: `ruff` + `black`
* **Observability**: JSON logs, Prometheus metrics exposed via `/metrics`

## Extensibility

* Add a factor: drop a module in `services/signals/factors/` and register in `strategy.yaml`.
* New broker: implement `Broker` interface (`positions`, `balances`, `place`, `cancel`, `stream`) in `services/broker/`.

## Security & Compliance

* Keep keys in `.env` or Docker secrets; never commit secrets.
* Prefer official APIs to scraping; respect vendor ToS.
* Enforce **paper‑only** until you intentionally toggle to live (not included by default).

## Disclaimer

This software is provided “as is” without warranty. Markets carry risk. Backtests are not indicative of future results. Use at your own risk.
