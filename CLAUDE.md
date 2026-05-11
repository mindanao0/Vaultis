# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Vaultis is a Thai retail investor platform for long-term ETF portfolio management (VOO, SCHD, QQQM, XLV, GLDM). It has three runtime components: a FastAPI backend, a Streamlit dashboard, and a Python scheduler. All are independently runnable.

## Commands

```bash
# Backend API (port 8000)
uvicorn backend.main:app --reload --port 8000
# or
./run.sh

# Streamlit dashboard
streamlit run dashboard/app.py

# Scheduler loop (Discord notifications, weekly/monthly jobs)
python main.py
python main.py --job weekly_summary   # manual trigger
python main.py --job monthly_advice

# Tests
pytest
pytest tests/test_screener.py         # single file

# Docker (Redis + backend)
docker-compose up
```

No linter config is present. No build step required.

## Architecture

```
backend/          FastAPI REST + WebSocket server
  main.py           App init; APScheduler daily screener at 07:00 Asia/Bangkok
  database.py       SQLAlchemy / SQLite (vaultis.db)
  schemas.py        All Pydantic request/response models
  models/orm.py     ORM models: Transaction, PriceAlert, Config
  routers/          11 route groups (one file per feature)
  services/         Business logic layer called by routers
  screener/         Daily technical screener engine + presets + history

analysis/         17 standalone modules: returns, risk, correlation,
                  backtesting (vectorbt), forecasting (Prophet), AI advisor
                  (Groq), sentiment, macro (FRED), financial model scoring

dashboard/app.py  Streamlit single-file app (~75 KB). Multi-page via sidebar.
                  Calls analysis/ modules directly OR BACKEND_URL for live data.

portfolio/        Transaction CSV tracker, DCA simulator, rebalance logic
alerts/           Discord webhook builder
data/             yfinance price fetcher with 3-retry logic
technical/        RSI, MA50/MA200 indicators
jobs/             daily_check.py → fetch snapshot + AI summary → Discord
main.py           Python `schedule` loop for background jobs
config.json       Persistent app config (tickers, DCA budget, display prefs)
```

## Key Conventions

**Thai language throughout.** User-facing text, error messages, and many docstrings are in Thai. English is used for ticker symbols, technical terms (RSI, MACD), and module/function names. Keep this convention when editing.

**Dependency flow.** Routers → Services → Analysis modules → Data layer. Never import upward. Routers must not call `analysis/` directly; use a service.

**Caching.** Expensive calls (10-year price history, returns, risk, correlation) use `@cache_data_1h` from `utils/cache_utils.py`. Do not add new expensive computations in the request path without caching.

**JSONResponse for UTF-8.** All endpoints that may return Thai text use `JSONResponse(..., media_type="application/json; charset=utf-8")` instead of returning dicts directly.

**Config normalization.** `utils/config_utils.py` `load_config()` merges `config.json` with defaults. Settings changes go through `save_config()`.

**yfinance column handling.** Price data may have multi-level columns after `.download()`. The data layer normalizes to `Adj Close` before returning. Always use the `data/` fetchers, not raw yfinance calls.

## Data Storage

- **SQLite (`vaultis.db`)** — Transactions, price alerts, app config key-value store
- **PostgreSQL (`DATABASE_URL`)** — Optional; stores `sentiment_results` from news analysis
- **CSV (`portfolio/data/transactions.csv`)** — Authoritative transaction ledger; SQLite is the API layer on top

## Environment Variables

Required for full functionality:

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | `POST /api/transactions/upload-slip` (slip OCR) |
| `GROQ_API_KEY` | AI Advisor, ETF analysis summaries (llama-3.3-70b-versatile) |
| `FRED_API_KEY` | Macro data endpoint |
| `DISCORD_WEBHOOK_URL` | Scheduled job notifications |
| `BACKEND_URL` | Streamlit dashboard → API (defaults to Render backend) |
| `DATABASE_URL` | PostgreSQL for sentiment (optional) |
| `NEWSAPI_KEY` | News sentiment analysis |
| `REDDIT_CLIENT_ID/SECRET` | Reddit sentiment via PRAW |

## Backend Router Map

| Prefix | File | Notes |
|---|---|---|
| `/api/etf` | `routers/etf.py` | Prices, snapshots, returns, risk, correlation, technical |
| `/api/etf/{symbol}` | `routers/etf_analysis.py` | Per-symbol analysis with Groq summary |
| `/api/backtest` | `routers/backtest.py` | vectorbt RSI+MACD strategy |
| `/api/forecast` | `routers/forecast.py` | Prophet forecaster, walk-forward backtester |
| `/api/portfolio` | `routers/portfolio.py` | Transaction CRUD, portfolio summary |
| `/api/alerts` | `routers/alerts.py` | Price alert CRUD + `/check` |
| `/api/ai` | `routers/ai.py` | Monthly advice, history, suggest-alerts |
| `/api/sentiment` | `routers/sentiment.py` | Reads PostgreSQL sentiment_results |
| `/api/screener` | `routers/screener.py` | Run presets/custom screener rules |
| `/api/analysis` | `routers/analysis.py` | Backtest, DCA sim, macro, DCF, full analysis |
| `/api/transactions` | `routers/transactions.py` | Slip OCR via Anthropic vision |
| `/ws/prices` | `routers/websocket.py` | Real-time price WebSocket |

## Screener Engine

`backend/screener/engine.py` runs `ScreenerEngine` daily at 07:00 Bangkok via APScheduler. Presets (oversold_momentum, golden_cross_alert, etc.) define rule sets evaluated with AND/OR logic. Signal strength is 0–10. Results are stored by `ScreenerHistoryService`; `ScreenerNotifier` sends Telegram if signals fire. To add a new preset, edit `backend/screener/presets.py`.

## Scheduled Jobs

Two separate scheduling systems run in parallel:

1. **APScheduler** (inside FastAPI process) — daily screener only
2. **Python `schedule` library** (`main.py`) — weekly summary, monthly advice, DCA reminders
3. **GitHub Actions** (`.github/workflows/scheduler.yml`) — production cron triggers for jobs/daily_check.py and AI advice
