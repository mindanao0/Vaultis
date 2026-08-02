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

# Tests — test tooling lives in requirements-dev.txt (pytest.ini needs pytest-asyncio)
pip install -r requirements.txt -r requirements-dev.txt
pytest
pytest tests/test_screener.py         # single file

# Docker — postgres + backend + dashboard + scheduler (3 services from one image)
cp .env.example .env                  # optional; compose runs without it
docker compose up -d
python scripts/init_db.py             # ครั้งเดียวต่อฐานใหม่: สร้าง 3 ตารางบน Postgres
docker compose --profile dev run --rm tests
docker compose exec postgres pg_dump -U vaultis vaultis > backup.sql   # สำรอง Postgres
```

No linter config is present. No build step required.

**Docker specifics.** The container runs as `${DOCKER_UID:-1000}` so files it writes (ledger, alerts, `vaultis.db`) stay owned by the host user; set `DOCKER_UID`/`DOCKER_GID` in `.env` if yours differ. All caches point at `/tmp` (`NUMBA_CACHE_DIR`, `MPLCONFIGDIR`, `HOME`) because a non-root uid cannot write to site-packages — without this `vectorbt` dies on import. Data lives on the host via bind mounts (`portfolio/data`, `alerts/data`, `./.docker-data` for SQLite), so rebuilds never lose it. **Postgres is the one exception — it uses the named volume `pgdata`, not a bind mount**: the postgres image runs as its own uid inside the container and `initdb` fails on a directory owned by the host uid. Back it up with `pg_dump`, not by copying files. `DATABASE_URL` is set in `environment:` (not `env_file`) so containers reach it as `postgres:5432` while `.env` keeps the `localhost:5432` form for running outside Docker; the `tests` service clears it because the suite must pass with no database at all. **Protected routes return 503 in Docker unless `VAULTIS_API_KEY` is set** — requests arrive from the bridge IP, not `127.0.0.1`, so the localhost exemption in `backend/security.py` does not apply. That is the intended fail-closed behavior; the dashboard is unaffected because it calls `analysis/` directly.

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
  llm.py            **Single entry point for every LLM call** (Claude Sonnet 5 — ผู้ให้บริการเดียว)
  news_fetcher.py   ข่าวราย ticker (Yahoo RSS + NewsAPI + Reddit + StockTwits) — `get_news()`
                    คืนรายการอย่างเดียว, `get_news_with_status()` คืนสถานะรายแหล่งด้วย
  ta_compat.py      **Single indicator layer** (sma/rsi/macd/bbands) — pandas-ta is gone
  financial_model.py `score_from_prices()` = the one scoring function for the whole system
  trend_channel.py  log-linear trend ±σ (สถิติพรรณนา — ไม่เข้าเลขคะแนน/จัดสรร)

technical/
  signal_rules.py   **Single source of truth for buy/sell signals** — every subsystem imports this
  indicators.py     RSI, MA50/MA200 helpers

dashboard/app.py  Streamlit single-file app. Multi-page via sidebar (13 pages).
                  Calls analysis/ modules directly OR BACKEND_URL for live data.

portfolio/        Transaction CSV tracker (buy+dividend), DCA simulator, rebalance logic
  fees.py           สูตรค่าธรรมเนียมเดียว (Dime 0.15% ทุก transaction)
  costs.py          ชั้นภาษี/ต้นทุน: withholding ปันผล US 15%, FX spread (config `costs.fx_spread_pct`)
  drip.py           จำลอง DRIP จากปันผลที่บันทึกจริง
  benchmark.py      shadow VOO ("เงินเดียวกัน วันเดียวกัน") + XIRR money-weighted ของพอร์ตจริง
  cashflow_rebalance.py  rebalance ด้วยเงินใหม่ (opt-in ใน Scorecard, ไม่ขาย)
  ab_backtest.py    A/B harness ด่านกั้น edge (plain vs tilt vs VOO, point-in-time)
  edge_lab.py       ห้องทดลอง edge candidates ผ่าน harness — ผล 2 รอบ: ไม่มี tilt ไหนมี edge จริง
alerts/           Discord webhook builder + price alert store (JSON) + LINE notifier (env-only)
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

**LLM calls go through `analysis/llm.py`.** `chat_text()` calls Claude Sonnet 5 via `ANTHROPIC_API_KEY` — **single provider, no fallback** (Groq was removed 2026-08-02: silently degrading to a weaker model contradicts the project's fail-loud rule). A missing key or a failed call raises `RuntimeError` with a readable message; scores/signals are unaffected because they are computed in Python. It handles truncation (retries at 2× budget — note the truncated first attempt is still billed) and logs token usage + estimated cost from `_MODEL_PRICES_USD_PER_MTOK`, which **must be updated whenever `ANTHROPIC_MODEL` changes**. Do not instantiate `anthropic.Anthropic()` elsewhere — the one exception is slip OCR (`routers/transactions.py`), which needs vision.

**LLM is OFF unless the user explicitly asks for it.** `chat_text(..., user_initiated=True)` is required; without it the call raises `LLMDisabledError`. This is a cost guard, not an error path:

- **Automatic jobs never pay.** Cron, GitHub Actions, the 07:00 screener, and the monthly report all run with `user_initiated=False` and degrade to the model's own numbers (scores, allocation, signals) — which is the information that actually drives decisions. They must catch `LLMDisabledError` and fall back, never crash.
- **Only a click pays.** The dashboard's "ให้ AI อธิบายด้วย" button, `POST /api/ai/advice`, and API routes called with `?include_ai=true`.
- `VAULTIS_LLM_AUTO=1` lifts the gate for automatic jobs (opt-in; the user pays every run).

When adding a new LLM call, thread `user_initiated` from the entry point. Never default it to `True`.

**ข่าวกับ sentiment เป็นคนละเส้นทางกัน — อย่ารวมกลับเป็นเส้นเดียว.**

- **หน้า News (ฟรี)** — `render_news_page()` เรียก `get_news_with_status()` ตรง ๆ: ไม่ผ่าน LLM ไม่ผ่านฐานข้อมูล จึงใช้ได้เสมอแม้ `DATABASE_URL` ล่มและแม้ไม่มี API key สักตัว (Yahoo RSS + StockTwits ไม่ต้องใช้ key) แคช 30 นาทีด้วย `st.cache_data` — **ล้มเหลวไม่ถูกแคช** เพราะ `cached_news()` โยน `NewsSourcesUnavailable` เมื่อแหล่งข่าวจริงพังหมด (Streamlit ไม่เก็บผลของ call ที่ throw)
- **sentiment (มีค่าใช้จ่าย)** — `run_sentiment_job()` → LLM ต่อบทความ → PostgreSQL → `/api/sentiment/{symbol}` + กล่องบริบทในหน้า AI Advisor ต้องมีครบทั้ง `VAULTIS_LLM_AUTO=1` และ `DATABASE_URL` ที่ต่อได้ ไม่งั้น job ข้ามตัวเองเงียบ ๆ ตามนโยบายคุมค่าใช้จ่าย
- **`ดึงไม่สำเร็จ` ≠ `ไม่มีข่าว`** ทุก `fetch_*_status` คืนสถานะ `ok`/`error`/`off` (`off` = ไม่ได้ตั้ง key ซึ่งไม่ใช่ความล้มเหลว) หน้าจอต้องเตือนเมื่อ `error` ห้ามแสดงลิสต์ว่างเป็น "ไม่มีข่าว" (C1)
- ข่าว/sentiment เป็น**บริบทข้าง ๆ เท่านั้น — ห้ามเข้าเลขคะแนนหรือการจัดสรร DCA** (invariant เดียวกับ `trend_channel.py`)

**Indicators go through `analysis/ta_compat.py`.** `pandas-ta` was removed (dead upstream, breaks on numpy≥2). Warm-up periods stay `NaN` — never fill them with 0 or 100.

**Secrets are env-only.** `DISCORD_WEBHOOK_URL` and API keys live in `.env` / GitHub Secrets. `load_config()` overlays env over `config.json`, and `save_config()` refuses to write the webhook to disk (`config.json` is tracked in git).

**Dependencies are pinned.** `requirements.txt` pins every package; CI reinstalls it on every scheduled run. Do not unpin or bump without running `pytest`.

**Caching.** `utils/cache.py`'s `cache_data_1h` is a real in-process TTL memoizer (1h, thread-safe; keys are content hashes, DataFrame args supported). It **never caches failures**: exceptions, empty results (`None`/`{}`/empty frame), and dicts with `data_ok=False` are recomputed on every call (C1 — ความล้มเหลวต้องเกิดซ้ำ ไม่ค้างเป็นผลลัพธ์), and it always returns copies. Hot paths covered: `calculate_signal_score`/`dcf_valuation` cached per ticker (`/api/analysis/full` is ~10ms warm; a failing ticker is retried every request), `get_macro_data`, plus `backend/services/cache_service.TTLCache` (price history 1h, latest prices 5m, technical 15m, ETF info 6h). The dashboard adds `@st.cache_data(ttl=3600)` on top. Redis was removed from docker-compose — nothing ever called it. (AUDIT.md H3 closed 2026-07-18.)

**JSONResponse for UTF-8.** All endpoints that may return Thai text use `JSONResponse(..., media_type="application/json; charset=utf-8")` instead of returning dicts directly.

**Config normalization.** `utils/config.py` `load_config()` merges `config.json` with defaults and env. Settings changes go through `save_config()`.

**yfinance column handling.** `.download()` returns MultiIndex columns even for a single ticker. Always normalize (see `portfolio/tracker._close_series_from`) — `df.get("Close")` returns a DataFrame and will break `pd.to_numeric`. Use `auto_adjust=True` for new call sites; use the `data/` fetchers where possible.

## Data Storage

**One store per kind of data — do not add a second one.** (The old duplicates were silently broken: `POST /api/portfolio/add` raised a `TypeError` on every call, so the SQLite `transactions` table was always empty, and alerts created via the API were never checked by cron.)

| Data | Single source of truth | Used by |
|---|---|---|
| Transactions | **CSV** `portfolio/data/transactions.csv` via `portfolio/tracker.py` (rows keyed by `tx_id`; `tx_type` = buy\|dividend — แถวปันผล**ไม่เข้า** cost basis/ลำดับเทรด, แถวเก่า/ค่าว่าง = buy เสมอ) | dashboard, backend (`portfolio_service` delegates here), AI advisor, PDF |
| Price alerts | **JSON** `alerts/data/price_alerts.json` via `alerts/price_alert.py` | dashboard, backend (`alert_service` delegates), Discord cron |
| Goals / net worth / reports / config | **SQLite** `vaultis.db` | backend only |
| Sentiment + screener history | **PostgreSQL** (`DATABASE_URL`) — `docker compose` ยกให้ในเครื่อง (service `postgres`, named volume `pgdata`) สร้างตารางด้วย `python scripts/init_db.py` | backend (screener 07:00), sentiment job, dashboard |

**The repo is public — the ledger, `vaultis.db`, and `alerts/data/price_alerts.json` are gitignored and must stay that way.** Consequences, both stated explicitly rather than papered over:

- GitHub Actions cannot see the portfolio, so the monthly AI advisor runs without holdings context.
- GitHub Actions cannot see price alerts either (untracked 2026-07-28 — it used to be committed back by CI, which published the user's tickers and target prices). The daily Discord price summary still runs in CI; **per-alert checking only works from the local/Docker scheduler**, which reads the real file through a volume.

`alerts/data/.gitkeep` and `portfolio/data/.gitkeep` are tracked on purpose: the directories must exist in a fresh clone, otherwise the Docker bind mounts create them as `root` and the container (running as the host uid) cannot write.

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
| `ANTHROPIC_API_KEY` | **The only LLM** — AI Advisor, ETF summaries, reports, slip OCR (Claude Sonnet 5). No fallback: unset = AI buttons fail loudly, everything else still works |
| `FRED_API_KEY` | Macro data endpoint |
| `DISCORD_WEBHOOK_URL` | Scheduled job notifications (**env only — never in config.json**) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Screener alerts + monthly report |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_TARGET_ID` | แจ้งเตือน LINE (เสริม — weekly summary; ไม่ตั้ง = ข้ามเงียบ ๆ) |
| `BACKEND_URL` | Streamlit dashboard → API (defaults to Render backend) |
| `DATABASE_URL` | PostgreSQL for sentiment + screener history (optional) |
| `NEWSAPI_KEY` | News sentiment analysis |
| `REDDIT_CLIENT_ID/SECRET` | Reddit sentiment via PRAW |

## Backend Router Map

| Prefix | File | Notes |
|---|---|---|
| `/api/etf` | `routers/etf.py` | Prices, snapshots, returns, risk, correlation, technical |
| `/api/etf/{symbol}` | `routers/etf_analysis.py` | Per-symbol analysis with Claude summary |
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
