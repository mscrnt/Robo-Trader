from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime, date, timezone
import os
import sys
import redis
import json
import logging

sys.path.append('/app')
from libs.database import get_session, TradePlan, Order, Position, Signal

app = FastAPI(title="Robo Trader API", version="1.0.0")

logging.basicConfig(
    level=os.getenv('LOG_LEVEL', 'INFO'),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

redis_client = redis.from_url(os.getenv('REDIS_URL', 'redis://redis:6379/0'))

class RunRequest(BaseModel):
    force: bool = False
    dry_run: bool = False

class ControlRequest(BaseModel):
    action: str  # pause or resume

class PlanResponse(BaseModel):
    id: int
    plan_date: datetime
    mode: str
    universe: List[str]
    orders: List[Dict[str, Any]]
    risk_metrics: Dict[str, float]
    performance_metrics: Dict[str, float]
    notes: str
    approved: bool
    executed: bool
    created_at: datetime

@app.get("/health")
async def health_check():
    try:
        from sqlalchemy import text
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
        db_status = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "unhealthy"

    try:
        redis_client.ping()
        redis_status = "healthy"
    except:
        redis_status = "unhealthy"

    return {
        "status": "healthy" if db_status == "healthy" and redis_status == "healthy" else "degraded",
        "db": db_status,
        "redis": redis_status,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.post("/run")
async def run_pipeline(request: RunRequest, background_tasks: BackgroundTasks):
    kill_switch = redis_client.get("GLOBAL_KILL_SWITCH")
    if kill_switch and kill_switch.decode() == "true":
        raise HTTPException(status_code=403, detail="Trading is paused (GLOBAL_KILL_SWITCH is active)")

    trading_mode = os.getenv('TRADING_MODE', 'paper')
    auto_execute = os.getenv('AUTO_EXECUTE', 'true') == 'true'

    if trading_mode == 'live':
        live_enabled = os.getenv('LIVE_TRADING_ENABLED', 'false') == 'true'
        live_phrase = os.getenv('LIVE_CONFIRM_PHRASE', '')
        if not (live_enabled and live_phrase == 'I_UNDERSTAND_THE_RISKS'):
            logger.warning("Live trading requested but not properly configured")
            trading_mode = 'paper'

    logger.info(f"Starting pipeline run - Mode: {trading_mode}, Auto-execute: {auto_execute}, Dry-run: {request.dry_run}")

    # Execute pipeline directly (in production, use Celery or similar)
    from services.api.pipeline import TradingPipeline
    pipeline = TradingPipeline()

    # Run in background
    background_tasks.add_task(pipeline.run, request.force, request.dry_run)

    return {
        "status": "started",
        "mode": trading_mode,
        "auto_execute": auto_execute and not request.dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/plan/latest")
async def get_latest_plan():
    session = get_session()
    try:
        plan = session.query(TradePlan).order_by(TradePlan.created_at.desc()).first()
        if not plan:
            raise HTTPException(status_code=404, detail="No trade plans found")

        return PlanResponse(
            id=plan.id,
            plan_date=plan.plan_date,
            mode=plan.mode,
            universe=plan.universe or [],
            orders=plan.orders or [],
            risk_metrics=plan.risk_metrics or {},
            performance_metrics=plan.performance_metrics or {},
            notes=plan.notes or "",
            approved=plan.approved,
            executed=plan.executed,
            created_at=plan.created_at
        )
    finally:
        session.close()

@app.get("/positions")
async def get_positions():
    session = get_session()
    try:
        positions = session.query(Position).all()
        return [
            {
                "symbol": p.symbol,
                "qty": p.qty,
                "avg_entry_price": p.avg_entry_price,
                "market_value": p.market_value,
                "cost_basis": p.cost_basis,
                "unrealized_pl": p.unrealized_pl,
                "unrealized_plpc": p.unrealized_plpc,
                "current_price": p.current_price,
                "change_today": p.change_today,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None
            }
            for p in positions
        ]
    finally:
        session.close()

@app.get("/signals")
async def get_signals():
    """Get latest trading signals"""
    from datetime import timedelta
    session = get_session()
    try:
        # Get signals from last 24 hours
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        signals = session.query(Signal).filter(
            Signal.signal_date >= cutoff
        ).order_by(Signal.signal_date.desc()).all()

        # Group by symbol and get latest composite score
        symbol_signals = {}
        for signal in signals:
            if signal.symbol not in symbol_signals:
                symbol_signals[signal.symbol] = {
                    'symbol': signal.symbol,
                    'score': 0,
                    'factors': {},
                    'timestamp': signal.signal_date.isoformat() if signal.signal_date else None
                }

            # Add factor scores
            if signal.factor_name:
                score_val = float(signal.normalized_score or 0)
                # Handle NaN and Infinity values
                if not (score_val != score_val or score_val == float('inf') or score_val == float('-inf')):
                    symbol_signals[signal.symbol]['factors'][signal.factor_name] = score_val
                else:
                    symbol_signals[signal.symbol]['factors'][signal.factor_name] = 0.5  # Default to neutral

        # Calculate composite scores
        results = []
        for symbol, data in symbol_signals.items():
            if data['factors']:
                data['score'] = sum(data['factors'].values()) / len(data['factors'])
                data['action'] = 'buy' if data['score'] > 0.6 else ('sell' if data['score'] < 0.4 else 'hold')
                results.append(data)

        # Sort by score
        results.sort(key=lambda x: abs(x['score'] - 0.5), reverse=True)

        return results[:50]  # Return top 50 signals

    finally:
        session.close()

@app.get("/orders")
async def get_orders(limit: int = 100):
    session = get_session()
    try:
        orders = session.query(Order).order_by(Order.created_at.desc()).limit(limit).all()
        return [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side,
                "qty": o.qty,
                "order_type": o.order_type,
                "limit_price": o.limit_price,
                "stop_price": o.stop_price,
                "status": o.status,
                "filled_qty": o.filled_qty,
                "filled_avg_price": o.filled_avg_price,
                "submitted_at": o.submitted_at.isoformat() if o.submitted_at else None,
                "filled_at": o.filled_at.isoformat() if o.filled_at else None,
                "created_at": o.created_at.isoformat() if o.created_at else None
            }
            for o in orders
        ]
    finally:
        session.close()

@app.post("/control/pause")
async def pause_trading():
    redis_client.set("GLOBAL_KILL_SWITCH", "true")
    logger.warning("Trading paused via API")
    return {"status": "paused", "kill_switch": True}

@app.post("/control/resume")
async def resume_trading():
    redis_client.set("GLOBAL_KILL_SWITCH", "false")
    logger.info("Trading resumed via API")
    return {"status": "resumed", "kill_switch": False}

@app.get("/control/status")
async def get_control_status():
    kill_switch = redis_client.get("GLOBAL_KILL_SWITCH")
    is_paused = kill_switch and kill_switch.decode() == "true"

    return {
        "paused": is_paused,
        "trading_mode": os.getenv('TRADING_MODE', 'paper'),
        "auto_execute": os.getenv('AUTO_EXECUTE', 'true') == 'true',
        "live_enabled": os.getenv('LIVE_TRADING_ENABLED', 'false') == 'true',
        "risk_limits": {
            "max_single_name": float(os.getenv('RISK_MAX_SINGLE_NAME', '0.02')),
            "gross_max": float(os.getenv('RISK_GROSS_MAX', '0.60')),
            "net_max": float(os.getenv('RISK_NET_MAX', '0.40')),
            "daily_drawdown_halt": float(os.getenv('DAILY_DRAWDOWN_HALT', '0.02'))
        }
    }

@app.get("/metrics")
async def get_metrics():
    # Prometheus-compatible metrics endpoint
    session = get_session()
    try:
        plan_count = session.query(TradePlan).count()
        order_count = session.query(Order).count()
        position_count = session.query(Position).count()

        metrics = []
        metrics.append(f'# HELP robo_trader_plans_total Total number of trade plans')
        metrics.append(f'# TYPE robo_trader_plans_total counter')
        metrics.append(f'robo_trader_plans_total {plan_count}')

        metrics.append(f'# HELP robo_trader_orders_total Total number of orders')
        metrics.append(f'# TYPE robo_trader_orders_total counter')
        metrics.append(f'robo_trader_orders_total {order_count}')

        metrics.append(f'# HELP robo_trader_positions_active Current active positions')
        metrics.append(f'# TYPE robo_trader_positions_active gauge')
        metrics.append(f'robo_trader_positions_active {position_count}')

        return "\n".join(metrics)
    finally:
        session.close()

@app.get("/watchlist")
async def get_watchlist():
    """Get current watchlist from database"""
    from libs.database import Watchlist
    session = get_session()
    try:
        watchlist = session.query(Watchlist).order_by(Watchlist.score.desc()).all()
        return [
            {
                "symbol": w.symbol,
                "score": float(w.score),
                "mention_count": w.mention_count,
                "source": w.source,
                "categories": w.categories or [],
                "last_seen": w.last_seen.isoformat() if w.last_seen else None
            }
            for w in watchlist
        ]
    finally:
        session.close()

@app.get("/feeds/status")
async def get_feeds_status():
    """Get RSS feed status"""
    return {
        "active_feeds": 9,
        "last_update": "Recently",
        "articles_count": 197  # Could be dynamic from database
    }

@app.get("/signals/{symbol}")
async def get_signals_by_symbol(symbol: str, days: int = 30):
    session = get_session()
    try:
        from datetime import timedelta
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

        signals = session.query(Signal).filter(
            Signal.symbol == symbol.upper(),
            Signal.signal_date >= cutoff_date
        ).order_by(Signal.signal_date.desc()).all()

        return [
            {
                "symbol": s.symbol,
                "signal_date": s.signal_date.isoformat(),
                "factor_name": s.factor_name,
                "raw_value": s.raw_value,
                "normalized_score": s.normalized_score,
                "weight": s.weight,
                "rationale": s.rationale
            }
            for s in signals
        ]
    finally:
        session.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)