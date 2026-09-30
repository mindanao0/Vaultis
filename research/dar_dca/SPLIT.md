# Data split — declared before any analysis (2026-09-30)

- EXPLORATION (may look, may inform design): Ken French US industry portfolios (10/17/49, value-weighted),
  months 1926-07 .. 1974-12 ONLY. Any statistic computed for design must not read a return after 1974-12
  (forward-return targets included: a forward window that crosses 1975-01 is truncated/dropped).
- CONFIRMATION (untouched until the formula is locked + hashed):
  1. Ken French industries 1975-01 .. 2026-08
  2. Gold (World Bank 1972+, spliced with GLD) mixed with equities
  3. The user's ETF universe: proxy window 2011-10+ (VOO/SCHD/QQQ/XLV/GLD) and real window 2020-11+
- Literature priors (half-lives, 5y reversal definitions) come partly from post-1975 data; this contamination
  is acknowledged, not avoidable.
