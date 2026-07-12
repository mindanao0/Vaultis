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
  models/orm.py     ORM models: Transaction, PriceAlert, Config, ...
  routers/          19 route groups (one file per feature)
  services/         Business logic layer called by routers
  screener/         Daily technical screener engine + presets + history

analysis/         Standalone analysis modules: returns, risk, correlation,
                  backtesting (vectorbt), forecasting (Prophet), AI advisor,
                  sentiment, macro (FRED), financial model scoring
  llm.py            **Single entry point for every LLM call** (Haiku 4.5 → Groq fallback)
  ta_compat.py      **Single indicator layer** (sma/rsi/macd/bbands) — pandas-ta is gone
  financial_model.py `score_from_prices()` = the one scoring function for the whole system

technical/
  signal_rules.py   **Single source of truth for buy/sell signals** — every subsystem imports this
  indicators.py     RSI, MA50/MA200 helpers

dashboard/app.py  Streamlit single-file app. Multi-page via sidebar.
                  Calls analysis/ modules directly OR BACKEND_URL for live data.

portfolio/        Transaction CSV tracker, DCA simulator, rebalance logic
alerts/           Discord webhook builder + price alert store (JSON)
data/             yfinance price fetcher (3 retries, then raises)
jobs/             daily_check.py → fetch snapshot + AI summary → Discord
main.py           Python `schedule` loop for background jobs
config.json       Persistent app config (tickers, DCA budget, display prefs) — NO secrets
```

## Key Conventions

**Thai language throughout.** User-facing text, error messages, and many docstrings are in Thai. English is used for ticker symbols, technical terms (RSI, MACD), and module/function names. Keep this convention when editing.

**Fail loud on missing data — never fabricate.** This system drives real-money decisions. A data-fetch failure must NEVER become a price, score, or signal:
- `data/fetcher.py` raises `PriceDataUnavailableError` after 3 retries (it does not return an empty frame).
- Scores/holdings carry a `data_ok` / `Price OK` flag; missing prices stay `NaN`, never `0.0`.
- Tickers with `data_ok=False` are excluded from scoring and from DCA allocation.
- Never introduce a `except: return 0` / `return "neutral"` / `fillna(0)` on a price path.

**One signal definition.** `technical/signal_rules.py` defines RSI zones, trend, and the buy/sell label. `financial_model.score_from_prices()` is the only scoring function. The dashboard, screener, per-symbol analysis, and AI advisor all read from these — never re-implement a threshold locally. (Oversold in an uptrend = accumulate, NOT a sell.)

**AI explains, code computes.** All numbers — scores, DCA allocation, price-alert levels — are computed in Python. The LLM receives finished figures and only writes the explanation. Never parse numbers back out of model output.

**LLM calls go through `analysis/llm.py`.** `chat_text()` prefers Claude Haiku 4.5 (`ANTHROPIC_API_KEY`) and falls back to Groq llama-3.3-70b (`GROQ_API_KEY`). It handles truncation (retries at 2× budget). Do not instantiate `Groq()` or `anthropic.Anthropic()` elsewhere — the one exception is slip OCR (`routers/transactions.py`), which needs vision.

**Indicators go through `analysis/ta_compat.py`.** `pandas-ta` was removed (dead upstream, breaks on numpy≥2). Warm-up periods stay `NaN` — never fill them with 0 or 100.

**Secrets are env-only.** `DISCORD_WEBHOOK_URL` and API keys live in `.env` / GitHub Secrets. `load_config()` overlays env over `config.json`, and `save_config()` refuses to write the webhook to disk (`config.json` is tracked in git).

**Dependencies are pinned.** `requirements.txt` pins every package; CI reinstalls it on every scheduled run. Do not unpin or bump without running `pytest`.

**Caching.** `utils/cache.py`'s `cache_data_1h` is currently a **no-op**. The dashboard caches prices with `@st.cache_data(ttl=3600)` (`cached_prices`). Backend request-path caching is still a known gap (see AUDIT.md H3).

**JSONResponse for UTF-8.** All endpoints that may return Thai text use `JSONResponse(..., media_type="application/json; charset=utf-8")` instead of returning dicts directly.

**Config normalization.** `utils/config.py` `load_config()` merges `config.json` with defaults and env. Settings changes go through `save_config()`.

**yfinance column handling.** `.download()` returns MultiIndex columns even for a single ticker. Always normalize (see `portfolio/tracker._close_series_from`) — `df.get("Close")` returns a DataFrame and will break `pd.to_numeric`. Use `auto_adjust=True` for new call sites; use the `data/` fetchers where possible.

## Data Storage

**One store per kind of data — do not add a second one.** (The old duplicates were silently broken: `POST /api/portfolio/add` raised a `TypeError` on every call, so the SQLite `transactions` table was always empty, and alerts created via the API were never checked by cron.)

| Data | Single source of truth | Used by |
|---|---|---|
| Transactions | **CSV** `portfolio/data/transactions.csv` via `portfolio/tracker.py` (rows keyed by `tx_id`) | dashboard, backend (`portfolio_service` delegates here), AI advisor, PDF |
| Price alerts | **JSON** `alerts/data/price_alerts.json` via `alerts/price_alert.py` | dashboard, backend (`alert_service` delegates), Discord cron |
| Goals / net worth / reports / config | **SQLite** `vaultis.db` | backend only |
| Sentiment + screener history | **PostgreSQL** (`DATABASE_URL`, optional) | scheduled jobs |

**The repo is public — the ledger and `vaultis.db` are gitignored and must stay that way.** A consequence: GitHub Actions cannot see the portfolio, so the monthly AI advisor runs without holdings context (it says so explicitly rather than pretending).

**FX rate:** one source only — `utils/fx.py` `get_usdthb()`. It fetches live, sanity-checks the 20–50 band, caches for an hour, and reports `is_live=False` when it falls back to the config value. Never read `default_fx_rate` directly.

**Target portfolio weights:** one source only — `portfolio/targets.py` `get_target_weights()`, driven by `config.json` (`portfolio.risk_profile` + optional `portfolio.target_weights`). The DCA plan, the rebalance plan, and the dashboard sliders all read from it. (There used to be two disagreeing sets, so the DCA plan and the rebalance plan pulled the portfolio in different directions.)

**DCA allocation policy:** target weight is the base; the monthly score only *tilts* it (0.6×–1.4×, `financial_model.TILT_MIN/TILT_MAX`). Every ETF with data is bought every month — a weak signal reduces its share but never drops it. Do not re-introduce score-only allocation: that silently turns a DCA plan into market timing.

## Backend Auth

`backend/security.py`. Read-only routes (`/api/etf/*`, `/ws/prices`, `/health`) are open. Everything that mutates state, touches personal data, or costs money (LLM, slip OCR) requires `X-API-Key` matching `VAULTIS_API_KEY`.

If `VAULTIS_API_KEY` is unset, protected routes accept **localhost only** — so a public deploy that forgets the key fails closed instead of exposing the ledger. CORS is restricted to `VAULTIS_ALLOWED_ORIGINS` (default: local Streamlit).

## Environment Variables

Required for full functionality:

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | **Primary LLM** — AI Advisor, ETF summaries, reports, slip OCR (Claude Haiku 4.5) |
| `GROQ_API_KEY` | LLM fallback when `ANTHROPIC_API_KEY` is unset (llama-3.3-70b-versatile) |
| `FRED_API_KEY` | Macro data endpoint |
| `DISCORD_WEBHOOK_URL` | Scheduled job notifications (**env only — never in config.json**) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Screener alerts + monthly report |
| `BACKEND_URL` | Streamlit dashboard → API (defaults to Render backend) |
| `DATABASE_URL` | PostgreSQL for sentiment + screener history (optional) |
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
