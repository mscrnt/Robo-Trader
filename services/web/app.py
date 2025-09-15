from flask import Flask, render_template, request, jsonify, redirect, url_for
import requests
import os
import json
from datetime import datetime, timezone

app = Flask(__name__)
API_BASE = os.getenv('API_BASE', 'http://api:8000')

@app.route('/')
def dashboard():
    try:
        # Get control status
        control_resp = requests.get(f'{API_BASE}/control/status')
        control_data = control_resp.json() if control_resp.ok else {}

        # Get positions
        positions_resp = requests.get(f'{API_BASE}/positions')
        positions = positions_resp.json() if positions_resp.ok else []

        # Get latest plan
        plan_resp = requests.get(f'{API_BASE}/plan/latest')
        latest_plan = plan_resp.json() if plan_resp.ok else None

        # Calculate portfolio metrics
        total_value = sum(p.get('market_value', 0) for p in positions)
        total_pl = sum(p.get('unrealized_pl', 0) for p in positions)

        return render_template('dashboard.html',
                             control=control_data,
                             positions=positions[:5],  # Show top 5
                             latest_plan=latest_plan,
                             total_value=total_value,
                             total_pl=total_pl)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/positions')
def positions():
    try:
        resp = requests.get(f'{API_BASE}/positions')
        positions = resp.json() if resp.ok else []
        return render_template('positions.html', positions=positions)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/orders')
def orders():
    try:
        resp = requests.get(f'{API_BASE}/orders')
        orders = resp.json() if resp.ok else []
        return render_template('orders.html', orders=orders)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/signals')
def signals():
    # Get all signals, not just for one symbol
    try:
        resp = requests.get(f'{API_BASE}/signals')
        signals = resp.json() if resp.ok else []
        return render_template('signals.html', signals=signals)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/watchlist')
def watchlist():
    try:
        # Get watchlist from database via API
        resp = requests.get(f'{API_BASE}/watchlist')
        watchlist = resp.json() if resp.ok else []

        # Get RSS feed status
        feed_status = requests.get(f'{API_BASE}/feeds/status')
        feeds = feed_status.json() if feed_status.ok else {}

        return render_template('watchlist.html', watchlist=watchlist, feeds=feeds)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/plan')
def plan():
    try:
        resp = requests.get(f'{API_BASE}/plan/latest')
        plan = resp.json() if resp.ok else None
        return render_template('plan.html', plan=plan)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/settings')
def settings():
    try:
        resp = requests.get(f'{API_BASE}/control/status')
        status = resp.json() if resp.ok else {}

        env_vars = {
            'TRADING_MODE': os.getenv('TRADING_MODE', 'paper'),
            'AUTO_EXECUTE': os.getenv('AUTO_EXECUTE', 'true'),
            'LIVE_TRADING_ENABLED': os.getenv('LIVE_TRADING_ENABLED', 'false'),
            'LIVE_CONFIRM_PHRASE': bool(os.getenv('LIVE_CONFIRM_PHRASE', '')),
            'LLM_BASE_URL': os.getenv('LLM_BASE_URL', ''),
            'LLM_SUMMARY_MODEL': os.getenv('LLM_SUMMARY_MODEL', 'deepseek-v2:16b'),
            'LLM_SELECTOR_MODEL': os.getenv('LLM_SELECTOR_MODEL', 'deepseek-r1:32b'),
            'ALPHA_VANTAGE_API_KEY': 'Configured' if os.getenv('ALPHA_VANTAGE_API_KEY') else 'Not Set',
            'FINNHUB_API_KEY': 'Configured' if os.getenv('FINNHUB_API_KEY') else 'Not Set'
        }

        return render_template('settings.html', status=status, env_vars=env_vars)
    except Exception as e:
        return render_template('error.html', error=str(e))

@app.route('/control/pause', methods=['POST'])
def pause_trading():
    try:
        resp = requests.post(f'{API_BASE}/control/pause')
        return jsonify(resp.json() if resp.ok else {'error': 'Failed to pause'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/control/resume', methods=['POST'])
def resume_trading():
    try:
        resp = requests.post(f'{API_BASE}/control/resume')
        return jsonify(resp.json() if resp.ok else {'error': 'Failed to resume'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/run', methods=['POST'])
def trigger_run():
    try:
        data = request.get_json() or {}
        resp = requests.post(f'{API_BASE}/run', json=data)
        return jsonify(resp.json() if resp.ok else {'error': 'Failed to trigger run'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/health')
def health():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now(timezone.utc).isoformat()})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)