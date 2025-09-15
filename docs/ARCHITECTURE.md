# Robo Trader Architecture

## Directory Structure

```
robo-trader/
├── libs/                    # Shared libraries
│   ├── database.py          # Database models (all tables)
│   ├── data_providers.py    # Market data providers (YFinance, Finnhub)
│   ├── alpha_vantage.py     # Alpha Vantage direct API (25/day limit)
│   ├── mcp_client.py        # Alpha Vantage MCP client (for top 25 only)
│   ├── news_aggregator.py   # FREE news sources (RSS, SEC, Yahoo)
│   ├── polygon_news.py      # Polygon news provider (5/min limit)
│   ├── news_watchlist.py    # News-driven watchlist builder
│   └── init_db.py           # Database initialization
│
├── services/
│   ├── ingest/              # Data fetching and storage
│   │   └── ingest.py        # Main ingestion orchestrator
│   │
│   ├── signals/             # Signal calculation from DB
│   │   ├── signals.py       # Main signal calculator
│   │   └── llm_copilot.py   # LLM for signal rationale
│   │
│   ├── risk/                # Risk management
│   ├── broker/              # Alpaca integration
│   ├── api/                 # FastAPI endpoints
│   ├── web/                 # Flask UI
│   ├── scheduler/           # Daily execution
│   └── reporter/            # Report generation
│
└── configs/                 # Configuration files
    ├── universe.yaml        # Dynamic watchlist (auto-generated from news)
    ├── strategy.yaml        # Trading strategy parameters
    └── rate_limits.yaml     # API rate limit tracking
```

## Data Flow

1. **News Aggregation** → Collect from FREE sources (RSS feeds, SEC, Yahoo)
2. **Watchlist Building** → Build dynamic list from news mentions
3. **Data Ingestion** → Fetch and store market data in PostgreSQL
4. **Alpha Vantage MCP** → Enhanced data for top 25 symbols only
5. **Signal Generation** → Calculate factors from DB data
6. **Risk Management** → Apply position limits and sizing
7. **Order Execution** → Send to Alpaca (paper/live)
8. **Reporting** → Generate human-readable reports

## API Rate Limits

### Primary Data Sources (High Limits)
- **YFinance**: 2000/hour - Primary data source
- **Finnhub**: 30/second - Backup data source

### Reserved Sources (Low Limits)
- **Alpha Vantage**: 25/day - Reserved for top 25 symbols via MCP
- **Polygon**: 5/minute - Reserved for news ingestion only

## Key Design Principles

1. **No Hardcoded Symbols**: All symbols discovered from news
2. **Separation of Concerns**:
   - `ingest/` only fetches and stores data
   - `signals/` only reads from database
3. **Shared Libraries**: All data providers in `libs/`
4. **Rate Limit Awareness**: Respects API limits with fallback chains

## Database Tables

- **price_data**: OHLCV price bars
- **news_articles**: News articles with sentiment
- **corporate_events**: Earnings, dividends, splits
- **signals**: Calculated trading signals
- **trade_plans**: Generated trading plans
- **orders**: Order history
- **positions**: Current positions

## MCP Integration

The Alpha Vantage MCP (Model Context Protocol) client allows Ollama to:
- Fetch real-time quotes (GLOBAL_QUOTE)
- Get historical prices (TIME_SERIES_DAILY)
- Analyze news sentiment (NEWS_SENTIMENT)
- Access company fundamentals (OVERVIEW)

MCP Server URL: `https://mcp.alphavantage.co/mcp?apikey=YOUR_KEY`

## News-Driven Universe

The system dynamically builds its trading universe from FREE sources:

### News Sources (No API Limits)
- **RSS Feeds**: MarketWatch, NASDAQ, Seeking Alpha, Yahoo, CNBC, Reuters, Benzinga
- **SEC Filings**: Real-time 8-K, 10-Q, 10-K from EDGAR RSS
- **Yahoo Trending**: Scrapes trending tickers
- **FinViz News**: Latest market news headlines

### Discovery Process
1. Aggregates news from all FREE sources (RSS, SEC, web scraping)
2. Extracts ticker mentions using pattern matching
3. Counts mentions and weights by source importance
4. Builds watchlist of top 200 most-mentioned symbols
5. Saves Alpha Vantage MCP calls for actual data fetching (top 25 only)
6. No hardcoded stock lists - completely data-driven