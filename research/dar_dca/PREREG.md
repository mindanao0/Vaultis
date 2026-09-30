# PRE-REGISTRATION — DAR-DCA (locked before any confirmation data is examined)

Date: 2026-09-30 · Author: Claude (Vaultis session vaultis-ea) · Requested by the user in this session.

## 0. What the user fixed (their answers, verbatim choices)
- Universe: the user picks the funds; the formula only splits the money (ticker-agnostic).
- Objective: **maximum terminal wealth** ("เงินปลายทางมากที่สุด"), any drawdown accepted.
- Constraints kept: buy every fund every month · never sell · invest the full budget every month.
- Acceptance: use it for real if it **wins on terminal wealth, measurably, and does not lose** to the
  equal split (1/N) by more than a margin locked here.

Interpretation of "maximum terminal wealth": maximise E[log W_T] (growth-optimal; equals maximising
the typical/median outcome). The mean of W_T is dominated by a few lucky paths; one investor lives one path.

## 1. The formula (implementation: research/dar_formula.py — hash below)
Monthly, for each fund, using month-end total-return prices strictly before the purchase month:

    AMP  = log( mean price at the 13 month-ends 54..66 months ago ) − log( price now )   # 5-year value
    OLD  = log( price 60 months ago ) − log( price 180 months ago )                     # the 10 years before
    DAR  = AMP + 0.5 × OLD          (0.5 = 5y/10y: a persistent drift μ gives AMP≈−5μ, OLD≈10μ → cancels)
    z    = (DAR − mean over funds) / max(std over funds, 0.15)   (funds with < 181 month-ends: z = 0, neutral)
    w    = max(1 + 1.0·z, 0) / N, then projected so Σw = 1 and every w ≥ 0.2/N

Constants: K = 1.0, FLOOR_FRAC = 0.2, DRIFT_COEF = 0.5, SD_MIN = 0.15, HISTORY = 181 month-ends.
No other parameters.
History before a fund's inception may come from a sibling tracking the same index (VOO←SPY, QQQM←QQQ,
GLDM←GLD), level-rescaled at the join; no sibling = neutral until the fund has its own 181 month-ends.

Where each number came from:
- 5-year window & the 54..66 averaging: Asness, Moskowitz & Pedersen (2013) value measure (literature, not fitted).
- 0.5 drift coefficient: algebra (window lengths), not fitted.
- K = 1.0: exploration knee — smallest K reaching ≥ 80% of the best mean gain in the exploration 'mixed' pool
  (K=3.0 gave +4.03%; K=1.0 gave +3.33%; K=0.5 gave +2.17%).
- FLOOR 0.2: user's "buy every fund every month" (≥ 200 THB of 5,000 for N = 5).
- SD_MIN 0.15: below the typical cross-sectional std of DAR in every exploration pool (median 0.18 style,
  0.29 industries), so it binds only in calm cross-sections; it stops near-identical funds (VOO vs SPY)
  from being tilted to the floor on noise. Exploration cost: mixed N=5 +3.30% → +3.18%.

## 2. Evidence used for design (EXPLORATION ONLY: US data 1926-07 … 1974-12)
- Horizon term structure: momentum 12-1 predicts +1..12 months, reverses at 60–120 months; 5y reversal
  predicts nothing short-term, IC rises to +0.13..0.23 at 60–120 months (industries).
- 20-year no-sell DCA sims, random 5-asset universes: momentum tilt −0.9 to −1.1% terminal wealth vs 1/N
  (industries); plain 5y reversal +2..3% in industries but −2 to −4% in style portfolios (persistent premia);
  DAR fixes the style failure (+1.0..1.9%) without hurting industries (+3.6..5.2%), mixed +2.2..3.3%.
- Holdings-aware (buy toward target holdings) did not beat the plain purchase tilt → not used.
- Robustness of the locked form (K=1, floor 0.2, SD_MIN 0.15), 20-year DCA, exploration:
  industries N=2/3/5/8: +3.2/+4.1/+5.2/+5.6% (win 77/80/87/93%); style: +0.6/+0.9/+1.7/+2.2% (win 52–57%);
  mixed N=2/3/5/8: +2.1/+2.4/+3.2/+3.7% (win 69–75%). 10-year DCA, mixed: +0.7..+0.9% (win ~60%).

## 3. Confirmation data (never examined before this file is hashed)
Outcomes = purchases from 1975-01 to 2026-08 only (signals may read earlier prices).
- P1 (PRIMARY): 'mixed' pool = 17 FF industries + 18 FF style portfolios (MKT, D/P, BE/ME, size×BM).
- S1: 17 industries · S2: 49 industries · S3: style pool · S4: mixed pool + GOLD (WB monthly avg to 2004-11,
  GLD after), every universe contains GOLD.
- Universes: 200 random 5-fund subsets, numpy seed 2026 (S4: 4 random + GOLD).
- E1 (descriptive): the user's ETFs through the project's harness (portfolio/ab_backtest.simulate_dca_dynamic),
  proxy window 2011-10+ and real window 2020-11+, vs 1/N, the old preset, old preset + score tilt,
  ERC + score tilt (current default), VOO only.

## 4. Statistics
(a) Rolling 20-year DCAs, starts every 6 months from 1975-01: log(W_DAR / W_1/N) per (universe, start).
(b) One continuous DCA per universe 1975-01 → 2026-08: monthly excess log return (time-weighted) of DAR over
    1/N averaged across universes; annualised mean ×12 with Newey–West (24 lags) 95% CI.

## 5. Decision rule (applies to P1)
- WIN (terminal wealth, measurable): mean of (a) > 0 **and** annualised mean of (b) > 0.
- NOT LOSE (non-inferiority): lower 95% bound of (b) > −0.25 %/yr.
  (0.25%/yr ≈ the whole expected edge; losing that much would erase a typical 20-year gain.)
- PASS = WIN and NOT LOSE → recommend DAR as the default split (integration after the user approves).
- VETO: if any of S1–S4 has a mean of (a) below −2.0% → at most an optional mode, never the default.
- E1 cannot resolve effects this small (MDE ≫ 0.25%/yr on 6–15 years); it is reported, not scored.
- Implementation check: vectorised simulator vs reference implementation must agree to < 1e-9.

## 6. Pre-registered predictions (not decision criteria; stated to be checked)
- MOM 12-1 tilt: rolling mean ≤ 0 in P1 and S1 (horizon principle).
- AMP-only < DAR in S3 (style) and P1.
- Out-of-sample decay: P1 rolling mean below the exploration +3.2% (a decay of 30–60% would be normal).
- Style pools (S3) are the weak spot: expect a win rate near 50% there even if the mean is positive.

## 7. Known limitations stated in advance
- Pre-tax total returns. A Thai investor loses 15% of dividends (US–Thailand treaty); high-yield funds carry a
  known extra drag the formula does not model (discussed separately, not part of this test).
- Gold before 2004-12 is a monthly-average price (smoothed returns).
- Literature parameters (5-year window) were themselves estimated on data overlapping 1975+.
