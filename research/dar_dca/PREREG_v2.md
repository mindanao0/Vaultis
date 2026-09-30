# PRE-REGISTRATION ROUND 2 — DAR-DCA v2 (locked before any round-2 data is examined)

Date: 2026-09-30 · Session vaultis-ea · The user delegated the choice of next steps and asked for a separate
DAR page with its own portfolio starting from zero, plan messages to Discord.

## What changed and why (honest provenance)
- v2 = v1 exactly (dar_formula.py, hash fd1d…/7759… unchanged) + a per-fund ceiling of 1.5 × equal share
  (dar_formula_v2.py). The ceiling was chosen AFTER seeing round-1 confirmation data (gold pool). Round 1
  therefore cannot confirm it; only this round can.

## Data (never examined before this file was hashed)
- C1 PRIMARY: Ken French international country markets ('Mkt', USD, with dividends), 20 countries
  (Malaysia excluded: 94 months only). Random 5-country universes, 200, numpy seed 7202.
- C2 VETO TEST: 4 random countries + 1 random commodity from the World Bank Pink Sheet
  (crude oil avg, silver, platinum, copper, aluminum, nickel, zinc, lead, tin; monthly average prices;
  gold excluded because it is spent). Same seed stream.
- Outcomes: purchases from 1990-02 (first month the 1975 countries have 181 month-ends) to 2025-12 (C1)
  / 2024-12 (C2). Signals may read earlier prices.

## Statistics (same as round 1)
(a) rolling 20-year DCAs, starts every 6 months from 1990-02; (b) continuous DCA per universe over the
whole window, monthly excess log return vs 1/N averaged over universes, Newey–West (24 lags) 95% CI.
Arms: equal, DAR v1, DAR v2. Implementation check: vectorised v2 vs reference dar_weights_v2 < 1e-9.

## Decision rule for v2
- PASS = on C1: mean of (a) > 0 AND annual mean of (b) > 0 AND lower 95% bound of (b) > −0.25%/yr;
  AND on C2: mean of (a) ≥ −2.0% (veto line, as in round 1).
- v1 is reported alongside (it is expected to be worse on C2 if the round-1 diagnosis is right).

## Deployment rule (fixed now, whatever the result)
- The user explicitly asked for a separate, experimental DAR page with its own portfolio. That page ships
  with v2 either way (the ceiling only limits concentration). Its banner states the verdict:
  PASS → "ผ่านการยืนยันรอบ 2"; FAIL → "ยังไม่ผ่านเกณฑ์ — ใช้เพื่อทดลองเท่านั้น".
- The main DCA plan (ERC + score tilt) is not touched by this work.

## Predictions (stated in advance, not criteria)
- Country markets mean-revert (Balvers et al. 2000) → C1 positive for both v1 and v2.
- Commodity boom-busts (silver 1980, oil 2008) → v1 has a fat left tail on C2; v2's p10 is shallower.
- Effect sizes small (≈ +0.1–0.3%/yr); round 1 showed the equity edge fading after 2000.
