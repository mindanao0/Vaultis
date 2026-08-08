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
pytest                                # เทสต์ที่ต้องต่อเน็ตถูกกันออกอัตโนมัติ
pytest tests/test_money_math.py       # single file
pytest -m network                     # 4 ไฟล์ที่ยิง yfinance/Prophet จริง (ต้องมีเน็ต)
pytest -m "network or not network"    # ทั้งหมด

# Docker — postgres + backend + dashboard + scheduler (3 services from one image)
cp .env.example .env                  # optional; compose runs without it
docker compose up -d
python scripts/init_db.py             # ครั้งเดียวต่อฐานใหม่: สร้าง 3 ตารางบน Postgres
# เทสต์ใน Docker — ต้อง mount repo ทับ /app เสมอ ไม่งั้นได้ "ผ่าน" จากโค้ดที่อบไว้ใน image
docker compose --profile dev run --rm -v "$PWD:/app" tests
docker compose --profile dev run --rm -v "$PWD:/app" tests pytest -q tests/test_money_math.py
docker compose exec postgres pg_dump -U vaultis vaultis > backup.sql   # สำรอง Postgres
```

No linter config is present. No build step required.

**The default test run must not need the internet.** `pytest.ini` sets `addopts = -m "not network"`, and the four suites that hit live yfinance/Prophet (`test_etf_analysis`, `test_screener`, `test_backtest`, `test_forecast`) carry `pytestmark = pytest.mark.network`. Before this, `test_etf_analysis.py` and `test_screener.py` called `asyncio.run(test())` at module level, so collection itself downloaded prices — offline the whole suite died with `Interrupted: 2 errors during collection` and not one test ran, while online the pass count depended on Yahoo's uptime (AUDIT_2026-08-06 §0-B). Never call a test body at module scope, and mark any new test that reaches the network. `tests/pipeline_smoke.py` is deliberately not named `test_*` — it calls `get_ai_advice()` and costs real money; run it by hand only. `tests/test_offline_collection.py` fails if any of this regresses.

**Docker specifics.** The container runs as `${DOCKER_UID:-1000}` so files it writes (ledger, alerts, `vaultis.db`) stay owned by the host user; set `DOCKER_UID`/`DOCKER_GID` in `.env` if yours differ. All caches point at `/tmp` (`NUMBA_CACHE_DIR`, `MPLCONFIGDIR`, `HOME`) because a non-root uid cannot write to site-packages — without this `vectorbt` dies on import. Data lives on the host via bind mounts (`portfolio/data`, `alerts/data`, `./.docker-data` for SQLite), so rebuilds never lose it. **Postgres is the one exception — it uses the named volume `pgdata`, not a bind mount**: the postgres image runs as its own uid inside the container and `initdb` fails on a directory owned by the host uid. Back it up with `pg_dump`, not by copying files. `DATABASE_URL` is set in `environment:` (not `env_file`) so containers reach it as `postgres:5432` while `.env` keeps the `localhost:5432` form for running outside Docker; the `tests` service clears it because the suite must pass with no database at all. **The `tests` service must never see the real data.** It mounts no host volume and sets `VAULTIS_DB_PATH=/tmp/test_vaultis.db`, so SQLite lives inside the container and dies with `--rm`. It used to mount `./.docker-data:/data` while inheriting the image's `VAULTIS_DB_PATH=/data/vaultis.db`, so anything the suite ran that touched `SessionLocal` wrote straight through to the user's real goals/net-worth/reports database — and it did (AUDIT_2026-08-06 §0.1). `tests/test_db_isolation.py` fails if that mount or an in-`/data` path comes back. Note the run command mounts the repo at `/app`: that is the working tree, so the ledger CSV, `alerts/data/*.json` and `config.json` are physically writable during a test run — and a probe that called `_save_alerts()`/`delete_transaction()` without stubbing did wipe the user's alert store (AUDIT_ROUND2_2026-08-07). Two mechanisms now enforce it instead of a convention: the `tests` service sets `VAULTIS_LEDGER_PATH` / `VAULTIS_ALERTS_PATH` under `/tmp` (read once at import by `portfolio/tracker.py` and `alerts/price_alert.py` — keep them module-level constants, tests monkeypatch them by name), and `tests/conftest.py`'s autouse `_isolate_user_data_files` moves any path still pointing at a real user file into a per-test sandbox, re-checks after each test, and fingerprints the two gitignored stores across the whole session. Never publish a fix that makes those functions read the env on every call — that silently disables the net. `tests/test_data_file_isolation.py` covers all three layers. **Protected routes return 503 in Docker unless `VAULTIS_API_KEY` is set** — requests arrive from the bridge IP, not `127.0.0.1`, so the localhost exemption in `backend/security.py` does not apply. That is the intended fail-closed behavior; the dashboard is unaffected because it calls `analysis/` directly.

## Architecture

```
backend/          FastAPI REST + WebSocket server
  main.py           App init + `configure_logging()`; APScheduler (Asia/Bangkok):
                    daily screener 07:00 **and** monthly report on the 1st at 08:00 (sends Telegram)
  database.py       SQLAlchemy / SQLite (vaultis.db)
  schemas.py        All Pydantic request/response models
  models/orm.py     ORM models: Transaction, PriceAlert, Config, ...
  routers/          19 route groups (one file per feature)
  services/         Business logic layer called by routers
  screener/         Daily technical screener engine + presets + history

analysis/         Standalone analysis modules: returns, risk, correlation,
                  backtesting (vectorbt), forecasting (Prophet), AI advisor,
                  sentiment, macro (FRED), financial model scoring
  llm.py            **Single entry point for every LLM call** (Claude Sonnet 5 — ผู้ให้บริการเดียว,
                    thinking ปิดทุกครั้ง) + `log_anthropic_usage()` ให้เส้นทาง vision รายงานต้นทุนเข้าที่เดียวกัน
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
- A percentage that cannot be computed is `None` plus a note, never `0.0` — `+0.00% 🟢` reads as "flat today", which is a claim, not an absence (`/ws/prices` `change_pct`, `build_weekly_summary_message()`'s return when nothing has been invested yet).
- Never introduce a `except: return 0` / `return "neutral"` / `fillna(0)` on a price path.

**One signal definition.** `technical/signal_rules.py` defines RSI zones, trend, and the buy/sell label. `financial_model.score_from_prices()` is the only scoring function. The dashboard, screener, per-symbol analysis, the AI advisor, the Discord card builder (`alerts/notifier.py`) and the scheduler's daily RSI check (`main.py`) all read from these — never re-implement a threshold locally. (Oversold in an uptrend = accumulate, NOT a sell.) The last two were the 2026-08-07 offenders and are worth remembering, because both looked harmless: `notifier.py` decided colour and wording itself with `if rsi < 30 → green`, so RSI 22 **below** MA200 — oversold in a downtrend, i.e. a falling knife — produced a byte-identical buy-green card to RSI 22 above it; and `main.py` wrote `if 30 <= rsi <= 70: continue`, a private copy of `RSI_OVERSOLD`/`RSI_OVERBOUGHT` that would have silently kept the old line the day anyone moved the central one. A duplicated threshold does not fail — it just drifts.

**AI explains, code computes.** All numbers — scores, DCA allocation, price-alert levels — are computed in Python. The LLM receives finished figures and only writes the explanation. Never parse numbers back out of model output.

**LLM calls go through `analysis/llm.py`.** `chat_text()` calls Claude Sonnet 5 via `ANTHROPIC_API_KEY` — **single provider, no fallback** (Groq was removed 2026-08-02: silently degrading to a weaker model contradicts the project's fail-loud rule). A missing key or a failed call raises `RuntimeError` with a readable message; scores/signals are unaffected because they are computed in Python. It handles truncation (retries at 2× budget — note the truncated first attempt is still billed) and logs token usage + estimated cost from `_MODEL_PRICES_USD_PER_MTOK`, which **must cover every model the project actually calls**, not just whatever `ANTHROPIC_MODEL` currently is. Do not instantiate `anthropic.Anthropic()` elsewhere — the one exception is slip OCR (`routers/transactions.py`), which needs vision and `chat_text()` only takes text. That path now reports its own spend: it calls `analysis.llm.log_anthropic_usage()` the moment the API returns — **before** parsing, because a slip that fails to parse cost exactly the same money — and that helper funnels into the same `_log_cost()`. Until this was added, every OCR call was money that appeared nowhere in the token/cost log, so the user's visible spend was structurally lower than the real one (AUDIT_ROUND2_2026-08-07). Two rules keep it honest: the model name is the constant `OCR_MODEL` (`claude-haiku-4-5`), which is simultaneously the model requested and the key that opens `_MODEL_PRICES_USD_PER_MTOK` (change one without the other and the log prices a different model with nothing to complain about); and `log_anthropic_usage()` never turns a missing token count into `0` — no `usage` from the SDK means a WARNING that this call's cost is unknown, and it never raises, because bookkeeping must not fail a request that already succeeded.

**`max_tokens` on Sonnet 5 is a *combined* ceiling, so thinking is switched off on every call.** `analysis/llm.py` sends `thinking={"type": "disabled"}` (`_THINKING_DISABLED`, passed through `extra_body` because the pinned `anthropic` SDK has no typed field for it — do not unpin the SDK over this). Read that constant before touching `max_tokens`: on Sonnet 5, *omitting* the field means adaptive thinking (the opposite of Sonnet 4.6), and `max_tokens` caps thinking **plus** the reply together. With the 512–2500 budgets this project asks for, thinking can consume the whole quota and return `stop_reason="max_tokens"` with no text at all — and the retry at 2× budget then pays twice for one answer, because **the truncated first attempt is still billed**. Never send `budget_tokens`: it was removed from Sonnet 5 and returns HTTP 400. Disabling thinking costs this project nothing, because "AI explains, code computes" means the model only writes Thai prose over numbers Python already finished. (Haiku 4.5 — the OCR model — still has the old behaviour, where omitting `thinking` means no thinking, so `routers/transactions.py` sends nothing. Moving OCR to a 5-series model means disabling it there too.)

**LLM is OFF unless the user explicitly asks for it.** `chat_text(..., user_initiated=True)` is required; without it the call raises `LLMDisabledError`. This is a cost guard, not an error path:

- **Automatic jobs never pay.** Cron, GitHub Actions, the 07:00 screener, and the monthly report all run with `user_initiated=False` and degrade to the model's own numbers (scores, allocation, signals) — which is the information that actually drives decisions. They must catch `LLMDisabledError` and fall back, never crash.
- **Only a click pays.** The dashboard's "ให้ AI อธิบายด้วย" button, `POST /api/ai/advice`, and API routes called with `?include_ai=true`.
- `VAULTIS_LLM_AUTO=1` lifts the gate for automatic jobs (opt-in; the user pays every run).

When adding a new LLM call, thread `user_initiated` from the entry point. Never default it to `True`.

**ข่าวกับ sentiment เป็นคนละเส้นทางกัน — อย่ารวมกลับเป็นเส้นเดียว.**

- **หน้า News (ฟรี)** — `render_news_page()` เรียก `get_news_with_status()` ตรง ๆ: ไม่ผ่าน LLM ไม่ผ่านฐานข้อมูล จึงใช้ได้เสมอแม้ `DATABASE_URL` ล่มและแม้ไม่มี API key สักตัว (Yahoo RSS + StockTwits ไม่ต้องใช้ key) แคช 30 นาทีด้วย `st.cache_data` — **ล้มเหลวไม่ถูกแคช** เพราะ `cached_news()` โยน `NewsSourcesUnavailable` เมื่อแหล่งข่าวจริงพังหมด (Streamlit ไม่เก็บผลของ call ที่ throw)
- **sentiment (มีค่าใช้จ่าย)** — `run_sentiment_job()` → LLM ต่อบทความ → PostgreSQL → `/api/sentiment/{symbol}` + กล่องบริบทในหน้า AI Advisor ต้องมีครบทั้ง `VAULTIS_LLM_AUTO=1` และ `DATABASE_URL` ที่ต่อได้ ไม่งั้น job ข้ามตัวเองเงียบ ๆ ตามนโยบายคุมค่าใช้จ่าย · step รายสัปดาห์ใน `.github/workflows/scheduler.yml` **ปิดอยู่โดยดีฟอลต์**: ต้องตั้ง repository variable `VAULTIS_SENTIMENT_ENABLED=1` ก่อน step ถึงจะรัน (แล้ว step นั้นตั้ง `VAULTIS_LLM_AUTO=1` ให้เอง = ยอมจ่ายทุกวันจันทร์) เดิม step ส่ง secrets เข้าไปทุกสัปดาห์แต่ไม่เคยตั้ง `VAULTIS_LLM_AUTO` จึง return ทันทีทุกรอบ ⇒ ตาราง sentiment ว่างเปล่าถาวรและ `/api/sentiment/{symbol}` ตอบ 404 ตลอด (AUDIT_ROUND2_2026-08-07) · เปิดแล้วแต่ไม่มี `DATABASE_URL`/`ANTHROPIC_API_KEY` = step แดง ไม่ใช่เขียวเงียบ
- **`ดึงไม่สำเร็จ` ≠ `ไม่มีข่าว`** ทุก `fetch_*_status` คืนสถานะ `ok`/`error`/`off` (`off` = ไม่ได้ตั้ง key ซึ่งไม่ใช่ความล้มเหลว) หน้าจอต้องเตือนเมื่อ `error` ห้ามแสดงลิสต์ว่างเป็น "ไม่มีข่าว" (C1)
- ข่าว/sentiment เป็น**บริบทข้าง ๆ เท่านั้น — ห้ามเข้าเลขคะแนนหรือการจัดสรร DCA** (invariant เดียวกับ `trend_channel.py`)

**Indicators go through `analysis/ta_compat.py`.** `pandas-ta` was removed (dead upstream, breaks on numpy≥2). Warm-up periods stay `NaN` — never fill them with 0 or 100.

**Secrets are env-only.** `DISCORD_WEBHOOK_URL` and API keys live in `.env` / GitHub Secrets. `load_config()` overlays env over `config.json`, and `save_config()` refuses to write the webhook to disk (`config.json` is tracked in git).

**Dependencies are pinned.** `requirements.txt` pins the **full transitive closure** (129 packages), not just the ~28 the code imports directly; `requirements-dev.txt` does the same for the test tooling. This became literally true on 2026-08-07 — before that it pinned 28 of 134 and the numeric libraries (`scipy`, `numba`, `llvmlite`, `starlette`, `urllib3`) floated, so two installs of the same commit could produce different numbers. CI reinstalls from these files on every scheduled run. Adding a dependency means adding **its** transitive deps too; `tests/test_docs_and_deps.py` goes red if anything installed is left unpinned. Do not unpin or bump without running `pytest`.

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

**Latest price + previous close:** the rule is "both numbers come from **the same two daily bars**". `alerts/price_alert.py` `get_price_snapshots()` is the implementation to reach for — the price-alert checker and `/ws/prices` both call it; `jobs/daily_check._yfinance_snapshot` and `etf_service.get_etf_daily_eod_snapshot` follow the same rule in their own code, so a new call site should read `get_price_snapshots()` rather than add a fourth. Never pair `fast_info["last_price"]` with `fast_info["previous_close"]`: those come from different endpoints with different meanings, and during market hours the second is the bar before the last *full* day. Measured live in the container, that mismatch printed `-0.07%` in red for an ETF that had closed `+0.69%` green — on the price bar that sits at the top of every page, where people read the colour before the number (AUDIT_ROUND2_2026-08-07).

**FX rate:** one source only — `utils/fx.py` `get_usdthb()`. It fetches live, sanity-checks the 20–50 band, caches for an hour, and reports `is_live=False` when it falls back to the config value. Never read `default_fx_rate` directly.

**Target portfolio weights:** one source only — `portfolio/targets.py` `get_target_weights()`, driven by `config.json` (`portfolio.risk_profile` + optional `portfolio.target_weights`). The DCA plan, the rebalance plan, and the dashboard sliders all read from it. (There used to be two disagreeing sets, so the DCA plan and the rebalance plan pulled the portfolio in different directions.)

**Two average returns, and they are not interchangeable.** `analysis/risk.portfolio_return_stats()` returns `mu_arithmetic` (`mean(daily) × 252` — the one `calculate_sharpe_ratio` uses) *and* `mu_geometric` (CAGR), because the two formulas downstream want different ones. Anything that compounds takes the geometric mean: `goal_service.calculate_pmt()` raises the rate to a power, so feeding it the arithmetic mean overstates growth by roughly σ²/2 per year (~1.1 points at σ 15%) and tells the user to save **less** than they actually must. Monte Carlo takes the arithmetic one: `calculate_probability()` draws monthly returns from a normal and multiplies them, so the simulation subtracts that gap itself — hand it the CAGR and it gets subtracted twice. `portfolio_mu_sigma()` is the two-value short form and returns the **arithmetic** μ; do not compound its output. Picking the right μ is only half of it — **converting it to a monthly rate must compound too**: `goal_service.monthly_compound_rate()` is `(1+r)^(1/12) − 1`, and the `rate / 12` it replaced is a *nominal* rate that compounds back to `(1+r/12)^12 − 1` (9.00% becomes 9.38%). That understates the required saving in the same direction as the σ²/2 bug, and it also made the two percentages on screen disagree, since `required_annual_return()` converts back with `(1+m)^12 − 1`. The number shown as `assumed_annual_return_pct` must be the number the formulas actually compound. The same function also returns the window actually used (`window_start`/`window_end`/`window_days`/`window_days_available`), and the label the user sees must report it: `dropna` cuts the series down to the portfolio's shortest common history (QQQM listed in 2020, so a 10-year request yields ~5.4 years with no major crash in it), and a number captioned "ย้อนหลัง 10 ปี" that isn't is a fabricated number like any other.

**DCA allocation policy:** target weight is the base; the monthly score only *tilts* it (0.6×–1.4×, `financial_model.TILT_MIN/TILT_MAX`). Every ETF with data is bought every month — a weak signal reduces its share but never drops it. Do not re-introduce score-only allocation: that silently turns a DCA plan into market timing.

## Backend Auth

`backend/security.py`. Read-only routes (`/api/etf/*`, `/ws/prices`, `/health`) are open. Everything that mutates state, touches personal data, or costs money (LLM, slip OCR) requires `X-API-Key` matching `VAULTIS_API_KEY`.

If `VAULTIS_API_KEY` is unset, protected routes accept **localhost only** — so a public deploy that forgets the key fails closed instead of exposing the ledger. CORS is restricted to `VAULTIS_ALLOWED_ORIGINS` (default: local Streamlit).

## Environment Variables

Required for full functionality:

| Variable | Used by |
|---|---|
| `ANTHROPIC_API_KEY` | **The only LLM** — AI Advisor, ETF summaries, reports (Claude Sonnet 5) + slip OCR (`claude-haiku-4-5`, the `OCR_MODEL` constant in `routers/transactions.py`, which must stay equal to a key in `_MODEL_PRICES_USD_PER_MTOK`). No fallback: unset = AI buttons fail loudly, everything else still works |
| `FRED_API_KEY` | Macro data endpoint |
| `DISCORD_WEBHOOK_URL` | Scheduled job notifications (**env only — never in config.json**) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Screener alerts + monthly report |
| `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_TARGET_ID` | แจ้งเตือน LINE (เสริม — weekly summary; ไม่ตั้ง = ข้ามเงียบ ๆ) |
| `BACKEND_URL` | Streamlit dashboard → API (defaults to Render backend) |
| `DATABASE_URL` | PostgreSQL for sentiment + screener history (optional) |
| `NEWSAPI_KEY` | News sentiment analysis |
| `REDDIT_CLIENT_ID/SECRET` | Reddit sentiment via PRAW |

Vaultis's own switches — all optional, all read with `os.getenv`. **Every `VAULTIS_*` name the code reads must have a row here**; `tests/test_docs_and_deps.py` scans the source and goes red on one that ships undocumented. That is exactly how `VAULTIS_WS_URL` and `VAULTIS_LOG_LEVEL` shipped in AUDIT_ROUND2_2026-08-07 — read by the code, mentioned in neither this table, `.env.example`, nor `docker-compose.yml`, so the one deployment mode that needed `VAULTIS_WS_URL` (Docker) had no way to know it existed.

| Variable | Default when unset | Used by |
|---|---|---|
| `VAULTIS_API_KEY` | localhost-only (fail closed) | `backend/security.py` — see Backend Auth. **Must be set under Docker**: requests arrive from the bridge IP, so the localhost exemption never applies |
| `VAULTIS_ALLOWED_ORIGINS` | `http://localhost:8501`, `http://127.0.0.1:8501` | CORS allow-list (comma-separated) in `backend/security.py` |
| `VAULTIS_LLM_AUTO` | off — automatic jobs never pay | `analysis/llm.py`; `1` lets cron/CI/screener spend money every run |
| `VAULTIS_LOG_LEVEL` | `INFO` | Both entry points (`backend/main.py`, `main.py`) — see Scheduled Jobs |
| `VAULTIS_WS_URL` | derived from `BACKEND_URL`, with an on-screen note when it had to guess | Dashboard real-time price ticker. This URL is dialled by the **user's browser**, so it is a different network view from `BACKEND_URL` (used from inside the container) — never copy one into the other: `ws://backend:8000` is unresolvable outside the compose network. Compose sets it to `ws://127.0.0.1:8000/ws/prices` for the dashboard service only |
| `VAULTIS_DB_PATH` | `./vaultis.db` | `backend/database.py` — SQLite location. Compose points it at `/data/vaultis.db`; the `tests` service at `/tmp` so a suite run can never touch the real goals/net-worth DB |
| `VAULTIS_LEDGER_PATH` | `portfolio/data/transactions.csv` | `portfolio/tracker.py` — **read once at import**, on purpose (see the Docker section) |
| `VAULTIS_ALERTS_PATH` | `alerts/data/price_alerts.json` | `alerts/price_alert.py` — **read once at import**, on purpose (see the Docker section) |
| `VAULTIS_PDF_THAI_FONT` | auto-detected from system font paths | `utils/pdf_export.py` — path to a Thai-capable `.ttf`. With no font found the PDF prints an English note instead of boxes, never silently blank text |

## Backend Router Map

Every prefix the app registers must appear here — `tests/test_docs_and_deps.py` compares this table against `backend.main.app.routes` and goes red on drift. (It listed 11 of 18 until AUDIT_ROUND2_2026-08-07: `/api/goals`, `/api/reports`, `/api/networth`, `/api/debt` and four more existed with no mention anywhere, which is exactly how a duplicate route gets written — the bug `tests/test_route_uniqueness.py` exists to catch.)

| Prefix | File | Notes |
|---|---|---|
| `/api/etf` | `routers/etf.py` | Prices, snapshots, returns, risk, correlation, technical |
| `/api/etf/{symbol}` | `routers/etf_analysis.py` | Per-symbol analysis with Claude summary |
| `/api/backtest` | `routers/backtest.py` | vectorbt RSI+MACD strategy |
| `/api/forecast` | `routers/forecast.py` | Prophet forecaster, walk-forward backtester |
| `/api/portfolio` | `routers/portfolio.py` | Transaction CRUD, portfolio summary |
| `/api/portfolio/rebalance` | `routers/rebalance.py` | Rebalance plan — same prefix as `portfolio.py`, different file |
| `/api/alerts` | `routers/alerts.py` | Price alert CRUD + `/check` |
| `/api/ai` | `routers/ai.py` | Monthly advice, history, suggest-alerts |
| `/api/sentiment` | `routers/sentiment.py` | Reads PostgreSQL sentiment_results |
| `/api/screener` | `routers/screener.py` | Run presets/custom screener rules |
| `/api/analysis` | `routers/analysis.py` | `/analysis/backtest`, `/analysis/dcf/{ticker}`, `/analysis/full` |
| `/api/dca` | `routers/analysis.py` | `/dca/simulate` — same file as `/api/analysis`, different prefix |
| `/api/macro` | `routers/analysis.py` | FRED macro snapshot (needs `FRED_API_KEY`) |
| `/api/goals` | `routers/goals.py` | Goal CRUD + `/progress`, `/contribute` (SQLite) |
| `/api/reports` | `routers/reports.py` | `/generate`, list, `/{month}` — monthly PDF/report store (SQLite) |
| `/api/networth` | `routers/networth.py` | `/current`, `/history`, `/snapshot` (SQLite) |
| `/api/cashflow` | `routers/cashflow.py` | `/forecast`, `/scenario`, `/transactions/bulk` |
| `/api/debt` | `routers/debt.py` | `/optimize` (snowball/avalanche), `/sensitivity` |
| `/api/emergency-fund` | `routers/emergency_fund.py` | `/calculate` — months-of-expenses target |
| `/api/transactions` | `routers/transactions.py` | Slip OCR via Anthropic vision |
| `/ws/prices` | `routers/websocket.py` | Real-time price WebSocket — reads `get_price_snapshots()` (not `fast_info`); a ticker it cannot price goes in `unavailable`, and `change_pct` it cannot compute is `null` + `note` |

## Screener Engine

`backend/screener/engine.py` runs `ScreenerEngine` daily at 07:00 Bangkok via APScheduler. Presets (oversold_momentum, golden_cross_alert, etc.) define rule sets evaluated with AND/OR logic. Signal strength is 0–10. Results are stored by `ScreenerHistoryService`; `ScreenerNotifier` sends Telegram if signals fire. To add a new preset, edit `backend/screener/presets.py`.

## Scheduled Jobs

Three separate scheduling systems run in parallel:

1. **APScheduler** (inside FastAPI process) — **two** jobs, both registered in `lifespan()`: the daily screener at 07:00 and `report_service.generate_and_save_report` on the 1st at 08:00. The monthly one **sends Telegram**, so `uvicorn backend.main:app` or `docker compose up -d` left running notifies the outside world on its own — it costs no LLM money (`user_initiated` defaults to `False`). The docs used to say "daily screener only" and undercounted what leaves the machine automatically (AUDIT_ROUND2_2026-08-07).
2. **Python `schedule` library** (`main.py`) — weekly summary, monthly advice, DCA reminders
3. **GitHub Actions** (`.github/workflows/scheduler.yml`) — production cron triggers for jobs/daily_check.py and AI advice, plus the weekly sentiment job, which is **off unless the repository variable `VAULTIS_SENTIMENT_ENABLED=1` is set** (see the news/sentiment section: it is the one automatic job that spends money every run)

Logging is configured **once per process at the entry point** — `backend.main.configure_logging()` honours `VAULTIS_LOG_LEVEL` (default `INFO`) and is the reason `logger.info` lines are visible at all. uvicorn's own `--log-level` does not help: it only configures loggers named `uvicorn*`, so before this every application INFO line — including the screener's `Screener run complete: … ตรวจไม่ได้` summary written for rule C1 — was dropped by the root `lastResort` handler at WARNING (AUDIT_ROUND2_2026-08-07). Do not sprinkle `basicConfig` into modules; libraries configure nothing, entry points configure once.

**There are two entry points and both must call it.** `backend/main.py` calls it at import (uvicorn loads that module). `main.py` — the `vaultis-scheduler` container — calls `_configure_logging_for_scheduler()` at the top of its `__main__` block, which imports the *same* function rather than defining a second one; that import is lazy because `backend.main` pulls in FastAPI and every router (~3s) and is only worth paying in the real process, not in the test files that import `main.py` as a library. The scheduler was missed on the first pass, and it is the container where it matters most: `analysis/llm.py`'s token+cost INFO line is the only record that a `VAULTIS_LLM_AUTO=1` run spent money, and `sentiment_analyzer`'s "ข้าม sentiment — LLM ปิดอยู่" is what separates "the job ran and skipped itself" from "the job never ran". `tests/test_logging_config.py` pins both entry points behaviourally (subprocess probes) and fails if any module outside `backend/main.py` calls `basicConfig`.
