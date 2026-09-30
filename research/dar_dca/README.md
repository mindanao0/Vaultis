# DAR-DCA research (2026-09-30)

These files are the raw research record behind `analysis/dar_dca.py`. They are kept **byte-for-byte as
they were run** so the SHA-256 lines in `LOCK.sha256` / `LOCK_v2.sha256` still verify. Do not edit them;
a change to the formula is a new pre-registration with its own lock file.

```bash
cd research/dar_dca
sha256sum -c LOCK.sha256                    # round 1: formula + tests locked 2026-09-30 11:43 +07
grep -v "re-hashed\|^20" LOCK_v2.sha256 | sha256sum -c   # round 2: locked 13:00:53, conf2.py re-hashed 13:01:47
```

`LOCK_v2.sha256` holds two lines for `conf2.py`. The first run crashed inside the implementation check
(`cross_check` did not skip universes containing a country that starts after 1990-02). It crashed
before any result was computed. Only that helper changed, and the file was re-hashed before the rerun.
So one line fails and one line passes, as recorded.

## Question
The user asked for a new, ticker-agnostic monthly DCA split. The goal was maximum terminal wealth
(growth-optimal = E[log W]). The constraints kept were:

- buy every fund every month;
- never sell;
- invest the full budget.

To be adopted, a formula had to beat 1/N on terminal wealth and not lose by more than a locked margin.

## Design data vs confirmation data (`SPLIT.md`)
- **Design:** US Ken French industry and style portfolios, 1926-07 to 1974-12. The scripts are
  `explore_ic*.py` and `explore*.py`.
- **Round 1 confirmation** (`PREREG.md`, `conf_ff.py`, `conf_etf.py`): US industries and style portfolios
  1975–2026, gold, and the user's ETFs.
  - The primary test passed: 20-year DCA +2.62% vs 1/N, annual excess +0.16%/yr, CI95 [−0.12, +0.45].
  - The gold pool **vetoed** it: −2.67%, with the worst 10% of cases below −21%. The formula kept buying
    gold through the 1980–2001 bear market.
- **Round 2** (`PREREG_v2.md`, `conf2.py`) added a per-fund ceiling of 1.5/N. The confirmation data had
  never been examined before: 20 Ken French country markets and 9 World Bank commodities, 1990–2025.
  - **Every criterion passed:** countries +1.24% (win 75%), annual excess +0.11%/yr, CI95 [−0.10, +0.32].
  - Countries plus a commodity: +2.44% (win 81%, worst 10% −1.8%).

## What held up
- **Horizon principle.** In a no-sell DCA each purchase is held for years, so a signal only helps if its
  predictive power grows with the horizon.
  - Momentum 12-1 predicts 1–12 months ahead and then reverses.
  - As a DCA tilt it lost to 1/N in 67–77% of 20-year DCAs, both in the design data and out of sample.
- **Plain 5-year reversal fails on characteristic-sorted portfolios**, whose premia persist.
  - The drift adjustment (`+ 0.5 × return 15y→5y ago`) removes that failure.
- **The edge is small.** It is about +0.1–0.2%/yr and is not statistically distinguishable from zero.
  - Its size in US equities faded after 2000.

## Running the scripts
The scripts expect the layout of the session they ran in:
- `/scratch/data`: downloads from the Ken French library and the World Bank Pink Sheet
  (`CMO-Historical-Data-Monthly.xlsx`)
- `/scratch/cache`: pickles written by `data.py`, `build_style.py`, `conf_data.py` and `conf2_data.py`
- `/scratch/results`: outputs
- `/scratch/pylib`: `openpyxl`, which is not a project dependency

They ran inside the project's tests image:
`docker compose --profile dev run --rm -v "$PWD:/app" -v "<dir>:/scratch" -e PYTHONPATH=/scratch/pylib:/scratch/research:/app tests python /scratch/research/<script>.py`

`results/` holds the JSON the report was built from. `report.html` is the published report page.
