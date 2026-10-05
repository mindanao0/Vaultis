# -*- coding: utf-8 -*-
"""เอนจิน simulation: สร้างเส้นทางอนาคตรายเดือนแบบเวกเตอร์ข้ามเส้นทาง + กลยุทธ์ DCA หลายแบบบนเส้นทางเดียวกัน.

โครงสร้างเดียวกับ ``research/dar_sim/sim.py`` (ที่ล็อกด้วย SHA-256 และเป็นที่มาของหลักฐานทั้งหมด) แต่ **รองรับกองกี่ตัวก็ได้**
ลำดับการเรียกตัวสุ่มเหมือนกันทุกบรรทัด — ``tests/test_simulation_engine.py`` ล็อกให้ผลตรงกับตัววิจัยทุกตัวเลขเมื่อใช้ห้ากองเดิม
(ถ้าอยากเปลี่ยนวิธีสุ่ม ต้องเปลี่ยนที่ตัววิจัยด้วยเหตุผลและหลักฐานใหม่ ไม่ใช่แก้เงียบ ๆ ที่นี่)

  1) regime ตลาด 4 แบบ (calm/stress/crisis/highinfl) เปลี่ยนสถานะตามเมทริกซ์ที่นับจากข้อมูลจริง 1990–ปัจจุบัน
  2) ทุกเดือนสุ่ม "เดือนจริงในอดีต" จากกอง regime นั้นทั้งเวกเตอร์ (กอง + ค่าเงิน + เงินเฟ้อ + ดอกเบี้ย + น้ำมัน พร้อมกัน)
  3) ลบค่าเฉลี่ยในอดีต แล้วใส่ผลตอบแทนคาดหวังข้างหน้า (ข้อสมมติ) + ความไม่แน่นอนของพารามิเตอร์ต่อเส้นทาง
  4) เหตุการณ์ใหญ่แบบ Poisson ซ้อนทับ (เพิ่มความเสี่ยง/หาง แต่ **ไม่เลื่อนค่าเฉลี่ย**)
  5) "โลกของส่วนต่างระหว่างกอง": rw / rev / mom / prem / boot — ตัดสินว่าสูตรเอียงตามราคาได้หรือเสีย
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from analysis import dar_dca as _dar  # ค่าคงที่ของสูตร DAR อ่านจากของจริง ไม่คัดลอก (เลขซ้ำ = เพี้ยนเงียบ ๆ)
from portfolio.fees import DIME_FEE_RATE as FEE
from portfolio.risk_weights import BLEND_ERC_SHARE

HORIZONS = (60, 120, 180, 240)
T_MAX = 240
AMP_FROM, AMP_TO, DRIFT_COEF, SD_MIN = _dar.AMP_FROM, _dar.AMP_TO, _dar.DRIFT_COEF, _dar.SD_MIN
FLOOR_FRAC, CAP_MULT, HISTORY_MONTHS = _dar.FLOOR_FRAC, _dar.CAP_MULT, _dar.HISTORY_MONTHS
UNIT_THB = int(_dar.ALLOCATION_UNIT_THB)
DEFAULT_BUDGET_THB = float(_dar.MONTHLY_BUDGET_THB)

# ---------------------------------------------------------------- ข้อสมมติ (ประกาศชัด ไม่ใช่ข้อมูล)
#: ผลตอบแทนคาดหวังต่อปี (CAGR, USD, รวมปันผล) ตามชนิดกอง · ห้ากองเดิมต้องได้ค่าเท่าเดิมทุกตัว (เทสต์ parity)
DRIFT_BY_KIND = {
    "mid": dict(us_equity=0.07, us_dividend=0.07, us_growth=0.08, us_health=0.07, gold=0.04,
                intl_equity=0.065, em_equity=0.075, reit=0.06, bond=0.04, other_equity=0.07),
    "low": dict(us_equity=0.04, us_dividend=0.05, us_growth=0.045, us_health=0.05, gold=0.03,
                intl_equity=0.04, em_equity=0.05, reit=0.04, bond=0.03, other_equity=0.04),
    "hist": None,  # ไม่ลบค่าเฉลี่ยในอดีต — ขอบบนที่ไม่ควรเชื่อ
}
WORLDS = {
    "rw": dict(kappa=0.0, dsd=0.01),
    "rev": dict(kappa=0.3, dsd=0.01),
    "mom": dict(kappa=-0.3, dsd=0.01),
    "prem": dict(kappa=0.0, dsd=0.03),
    "boot": dict(kappa=0.0, dsd=0.0),   # สุ่มบล็อกประวัติจริงตามลำดับเวลา — ไม่พึ่ง regime/เหตุการณ์
}
WORLD_TH = {
    "rw": "สุ่มเดิน (ส่วนต่างระหว่างกองไม่มีรูปแบบ)",
    "rev": "ย้อนกลับ (กองที่ชนะ 5 ปีมักถอย)",
    "mom": "ต่อเนื่อง (กองที่ชนะ 5 ปีชนะต่อ)",
    "prem": "พรีเมียมถาวร (บางกองโตเร็วกว่าตลอดไป)",
    "boot": "สุ่มบล็อกประวัติจริง (ไม่พึ่งข้อสมมติเหตุการณ์)",
}
BOOT_BLOCK = 12
VOL_SCALE_SD = 0.20
COMMON_DRIFT_SD = 0.015
FX_DRIFT_SD = 0.01
FX_REVERT = 0.006
EVENT_NAMES = ["war_supply", "growth_bust", "stagflation", "usd_debase", "baht_crisis", "baht_surge", "productivity", "rate_shock"]

# ขนาดเหตุการณ์ต่อชนิดกอง (ผลรวม log-return ตลอดเหตุการณ์). ห้าชนิดแรกตรงกับ research/dar_sim ทุกตัวเลข;
# ชนิดที่เพิ่ม (intl/em/reit/bond/other) เป็น ASSUMED ทั้งหมด — ไม่มีข้อมูลกองเหล่านั้นให้วัดขนาดเหตุการณ์
_SHOCK_ROWS = {
    "war_supply": dict(us_equity=-0.25, us_dividend=-0.18, us_growth=-0.32, us_health=-0.15, gold=0.18,
                       intl_equity=-0.27, em_equity=-0.33, reit=-0.28, bond=0.03, other_equity=-0.25),
    "stagflation": dict(us_equity=0.05, us_dividend=0.15, us_growth=-0.05, us_health=0.15, gold=0.90,
                        intl_equity=0.05, em_equity=0.0, reit=0.0, bond=-0.20, other_equity=0.05),
    "usd_debase": dict(us_equity=0.0, us_dividend=0.0, us_growth=0.0, us_health=0.0, gold=0.45,
                       intl_equity=0.10, em_equity=0.15, reit=0.0, bond=-0.05, other_equity=0.0),
    "baht_crisis": dict(),
    "baht_surge": dict(),
    "productivity": dict(us_equity=0.12, us_dividend=0.05, us_growth=0.30, us_health=0.10, gold=-0.05,
                         intl_equity=0.06, em_equity=0.08, reit=0.05, bond=-0.02, other_equity=0.12),
    "rate_shock": dict(us_equity=-0.15, us_dividend=-0.10, us_growth=-0.22, us_health=-0.10, gold=-0.08,
                       intl_equity=-0.14, em_equity=-0.18, reit=-0.22, bond=-0.12, other_equity=-0.15),
}


def build_events(meas: dict, kinds: list[str]) -> list[dict]:
    """เหตุการณ์ใหญ่ 8 แบบ — ``shock`` เรียงตามกองใน ``kinds`` · ตัวที่ "MEASURED" วัดจากข้อมูลจริงที่ส่งมาใน ``meas``."""
    d = meas["dotcom"]
    spy = d["VOO"]
    bust = dict(us_equity=spy, us_dividend=spy + 0.10, us_growth=d["QQQM"], us_health=d["XLV"], gold=0.10,
                intl_equity=spy, em_equity=spy * 1.1, reit=spy, bond=0.15, other_equity=spy)

    def vec(row: dict) -> list[float]:
        return [float(row.get(k, 0.0)) for k in kinds]

    return [
        dict(name="war_supply", rate=0.10, months=6, shock=vec(_SHOCK_ROWS["war_supply"]), fx=0.06, infl=4.0, oil=0.50, y10=0.5,
             note="ASSUMED: ความขัดแย้งมหาอำนาจ/ช็อกพลังงาน ~1 ครั้ง/ทศวรรษ ขนาดอิงปี 1990, 2008(น้ำมัน), 2022"),
        dict(name="growth_bust", rate=0.05, months=int(d["months"]), shock=vec(bust), fx=0.0, infl=-0.5, oil=-0.2, y10=-1.5,
             note="MEASURED SPY/QQQ/XLV 2000-03→2002-10 (ผลตอบแทนรวมจริง) · ชนิดอื่นเป็น ASSUMED"),
        dict(name="stagflation", rate=0.03, months=72, shock=vec(_SHOCK_ROWS["stagflation"]), fx=0.15, infl=5.0, oil=0.60, y10=3.0,
             note="ASSUMED: เงินเฟ้อสูงยาวแบบทศวรรษ 1970 — ไม่มีข้อมูลกองในช่วงนั้นให้วัด"),
        dict(name="usd_debase", rate=0.04, months=36, shock=vec(_SHOCK_ROWS["usd_debase"]), fx=-0.15, infl=1.0, oil=0.1, y10=1.0,
             note="ASSUMED: ดอลลาร์อ่อนต่อเนื่อง: ทองขึ้น บาทแข็ง"),
        dict(name="baht_crisis", rate=0.02, months=6, shock=vec({}), fx=meas["fx_up_6m"] * 0.5, infl=3.0, oil=0.0, y10=0.0,
             note=f"MEASURED ขนาดจากปี 1997 (บาทอ่อนสูงสุดใน 6 เดือน {meas['fx_up_6m']:.2f} log) ใช้ครึ่งเดียว · อัตราเกิด ASSUMED"),
        dict(name="baht_surge", rate=0.03, months=24, shock=vec({}), fx=-0.12, infl=-0.5, oil=0.0, y10=0.0,
             note="ASSUMED: บาทแข็งจากเงินทุนไหลเข้า"),
        dict(name="productivity", rate=0.08, months=60, shock=vec(_SHOCK_ROWS["productivity"]), fx=0.0, infl=0.0, oil=0.0, y10=0.5,
             note="ASSUMED: ช่วงบูมจากผลิตภาพ (ด้านบวก — กันไม่ให้โมเดลมีแต่ข่าวร้าย)"),
        dict(name="rate_shock", rate=0.04, months=9, shock=vec(_SHOCK_ROWS["rate_shock"]), fx=0.0, infl=0.0, oil=0.0, y10=1.5,
             note="ASSUMED: ช็อกดอกเบี้ยระยะยาว ขนาดอิงปี 2022"),
    ]


# ---------------------------------------------------------------- สูตรแบบเวกเตอร์
def floor_project_b(w: np.ndarray, floor: float) -> np.ndarray:
    """เวอร์ชันเวกเตอร์ของ ``analysis.dar_dca.floor_project`` (water-filling)."""
    w = np.maximum(w, 0.0)
    P, n = w.shape
    s = w.sum(1, keepdims=True)
    w = np.where(s > 0, w / np.where(s > 0, s, 1), 1.0 / n)
    fixed = np.zeros((P, n), dtype=bool)
    out = w
    for _ in range(n):
        budget = 1.0 - floor * fixed.sum(1, keepdims=True)
        free = ~fixed
        fw = np.where(free, w, 0.0)
        ss = fw.sum(1, keepdims=True)
        nfree = free.sum(1, keepdims=True)
        scaled = np.where(ss > 0, fw / np.where(ss > 0, ss, 1) * budget, np.where(free, budget / np.maximum(nfree, 1), 0.0))
        out = np.where(fixed, floor, scaled)
        low = free & (out < floor - 1e-15)
        if not low.any():
            break
        fixed = fixed | low
    return out


def cap_project_b(w: np.ndarray, cap: float) -> np.ndarray:
    """เวอร์ชันเวกเตอร์ของ ``analysis.dar_dca.cap_project``."""
    w = w.copy()
    P, n = w.shape
    for _ in range(n):
        over = w > cap + 1e-15
        if not over.any():
            break
        excess = np.where(over, w - cap, 0.0).sum(1, keepdims=True)
        w = np.where(over, cap, w)
        room = (~over) & (w < cap - 1e-15)
        share = np.where(room, w, 0.0)
        ss = share.sum(1, keepdims=True)
        w = w + np.where(ss > 0, share / np.where(ss > 0, ss, 1) * excess, 0.0)
    return w / w.sum(1, keepdims=True)


def dar_weights_b(LL: np.ndarray, n: int, valid: np.ndarray) -> np.ndarray:
    """น้ำหนัก DAR ของทุกเส้นทาง ณ สิ้นเดือน index ``n`` (log ระดับราคา total return)."""
    P, N = LL.shape[0], LL.shape[2]
    cur = LL[:, n, :]
    ref = np.log(np.exp(LL[:, n - AMP_TO: n - AMP_FROM + 1, :]).mean(axis=1))
    amp = ref - cur
    old = LL[:, n - 60, :] - LL[:, n - 180, :]
    dar = amp + DRIFT_COEF * old
    z = np.zeros((P, N))
    vi = np.where(valid)[0]
    if len(vi) >= 2:
        x = dar[:, vi]
        sd = np.maximum(x.std(axis=1, ddof=0, keepdims=True), SD_MIN)
        z[:, vi] = (x - x.mean(axis=1, keepdims=True)) / sd
    raw = np.maximum(1.0 + z, 0.0) / N
    w = floor_project_b(raw, FLOOR_FRAC / N)
    if CAP_MULT / N < 1.0:
        w = cap_project_b(w, CAP_MULT / N)
    return w


def round_units_b(w: np.ndarray, units: int) -> np.ndarray:
    """largest-remainder เป็นหน่วยร้อยบาท (เหมือน ``round_to_units``) → สัดส่วนหลังปัด."""
    N = w.shape[1]
    raw = w * units
    base = np.maximum(np.floor(raw), 1.0)
    if (base.sum(1) > units).any():
        raise AssertionError(f"การยกขั้นต่ำ 1 หน่วยทำให้เกินงบ (กอง {N} ตัว งบ {units} หน่วย) — กองเยอะเกินกว่างบจะซื้อได้ครบ")
    rem = (units - base.sum(1)).astype(int)
    frac = raw - base
    order = np.argsort(-frac, axis=1, kind="stable")
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.arange(N)[None, :].repeat(w.shape[0], 0), axis=1)
    base = base + (rank < rem[:, None])
    return base / units


def erc_batch(cov: np.ndarray, y0: np.ndarray | None = None, max_sweeps: int = 400, tol: float = 1e-10):
    """ERC ของ covariance หลายก้อนพร้อมกัน — อัลกอริทึมเดียวกับ ``portfolio.risk_weights.erc_weights``."""
    P, N = cov.shape[0], cov.shape[1]
    diag = np.einsum("pii->pi", cov)
    y = (1.0 / np.sqrt(diag)) if y0 is None else y0.copy()
    budget = 1.0 / N
    for sweep in range(max_sweeps):
        step = np.zeros(P)
        for i in range(N):
            c = (cov[:, i, :] * y).sum(1) - diag[:, i] * y[:, i]
            new = (-c + np.sqrt(c * c + 4.0 * diag[:, i] * budget)) / (2.0 * diag[:, i])
            step = np.maximum(step, np.abs(new - y[:, i]) / np.abs(y[:, i]))
            y[:, i] = new
        if step.max() < tol:
            break
    w = y / y.sum(1, keepdims=True)
    return w, y, sweep + 1


def erc_cov_from_S(S12, d12, S60, d60, iu, N: int) -> np.ndarray:
    """covariance รายปีแบบเดียวกับ ``portfolio.risk_weights.estimate_covariance``: vol 12 เดือนล่าสุด × correlation 60 เดือนล่าสุด
    จากผลตอบแทนรายวัน **เป็นบาท** — รับ S = Σ r rᵀ ของแต่ละเดือน."""
    P = S12.shape[0]
    tot12 = S12.sum(1) / d12.sum(1, keepdims=True)
    tot60 = S60.sum(1) / d60.sum(1, keepdims=True)
    diag_ix = [i for i, (a, b) in enumerate(zip(*iu)) if a == b]
    vol = np.sqrt(tot12[:, diag_ix] * 252.0)
    c60 = np.zeros((P, N, N)); c60[:, iu[0], iu[1]] = tot60; c60[:, iu[1], iu[0]] = tot60
    sd = np.sqrt(np.einsum("pii->pi", c60))
    corr = c60 / (sd[:, :, None] * sd[:, None, :])
    return corr * vol[:, :, None] * vol[:, None, :]


# ---------------------------------------------------------------- ตัวสร้างเส้นทาง
@dataclass
class Config:
    world: str = "rw"
    drift: str = "mid"
    event_mult: float = 1.0       # 0 = ปิดเหตุการณ์, 2 = เกิดถี่ขึ้นเท่าตัว
    P: int = 5000
    T: int = T_MAX
    seed: int = 0
    round_units: bool = True
    fx_revert: float = FX_REVERT
    kappa: float | None = None
    arms: tuple = ("DAR", "EQ", "ERC", "BLEND")
    horizons: tuple = HORIZONS
    fixed: dict | None = None     # {ชื่อ: น้ำหนักคงที่} — เช่น แผนที่ผู้ใช้ตั้งเอง / 35-25-20-10-10 / VOO ล้วน
    events_only: tuple | None = None
    budget_thb: float = DEFAULT_BUDGET_THB
    drift_overrides: dict = field(default_factory=dict)   # {ticker: CAGR} ทับค่าตามชนิด
    #: ภาษีหัก ณ ที่จ่ายปันผล (สัดส่วน เช่น 0.15) — หักจากผลตอบแทนรายเดือนตาม yield 12 เดือนล่าสุดของแต่ละกอง (panel["yields"])
    #: ค่าเริ่มต้น 0 = เหมือนตัววิจัย (parity) · ``service`` ใส่ค่าจริงจาก ``portfolio.costs`` เสมอ
    withholding_pct: float = 0.0
    #: FX spread ต่อการซื้อ (%) — หักจากเงินที่แปลงเป็น USD ทุกเดือน (ค่าประมาณจาก config costs.fx_spread_pct)
    fx_spread_pct: float = 0.0
    debug: int = 0


def _stationary(trans: np.ndarray) -> np.ndarray:
    w, v = np.linalg.eig(trans.T)
    pi = np.real(v[:, np.argmax(np.real(w))])
    return pi / pi.sum()


def _windowed(mag: np.ndarray, d: int) -> np.ndarray:
    """ผลรวมของ mag ในหน้าต่าง d เดือนล่าสุดที่ลงท้ายที่เดือน t (รวมเดือน t)."""
    c = np.cumsum(mag, axis=1)
    out = c.copy()
    out[:, d:] = c[:, d:] - c[:, :-d]
    return out


def _irr(V: np.ndarray, h: int, budget: float) -> np.ndarray:
    """IRR รายปี (บาท) จากเงินลงต้นเดือนทุกเดือน h เดือน ได้มูลค่า V ปลายเดือนที่ h (bisection, ปิดรูปของมูลค่าอนาคต)."""
    lo = np.full(V.shape, -0.05)
    hi = np.full(V.shape, 0.10)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        small = np.abs(mid) < 1e-12
        g = 1.0 + mid
        fv = budget * np.where(small, float(h), g * (g ** h - 1.0) / np.where(small, 1.0, mid))
        too_low = fv < V
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    return (1.0 + (lo + hi) / 2.0) ** 12 - 1.0


def run_chunk(panel: dict, cfg: Config) -> dict:
    rng = np.random.default_rng(cfg.seed)
    P, T = cfg.P, cfg.T
    funds: list[str] = list(panel["funds"])
    kinds: list[str] = list(panel["kinds"])
    N = len(funds)
    BUDGET_THB = float(cfg.budget_thb)
    units = int(BUDGET_THB // UNIT_THB)
    D = len(panel["cols"])  # funds + fx us_infl d_ffr d_10y oil
    pools = panel["pools"]
    K = len(pools)
    sizes = np.array([len(p) for p in pools])
    padded = np.zeros((K, sizes.max(), D))
    for k, p in enumerate(pools):
        padded[k, : len(p)] = p
    trans = panel["trans"]
    pi = _stationary(trans)
    mu_sim = sum(pi[k] * pools[k].mean(axis=0) for k in range(K))

    boot = cfg.world == "boot"
    drift_name = "hist" if boot else cfg.drift
    event_mult = 0.0 if boot else cfg.event_mult
    nS = len(panel["pool_S"][0][0])
    if boot:
        ch = panel["chron"]
        n_ch = len(ch["X"])
        nb = -(-T // BOOT_BLOCK)
        starts = rng.integers(0, n_ch - BOOT_BLOCK + 1, size=(P, nb))
        idx = (starts[:, :, None] + np.arange(BOOT_BLOCK)[None, None, :]).reshape(P, -1)[:, :T]
        X = ch["X"][idx]
        S_draw = ch["S"][idx]
        days_draw = ch["days"][idx]
        reg = ch["reg"][idx].astype(np.int8)
        mu_sim = np.zeros(D)
    else:
        cum = np.cumsum(trans, axis=1)
        reg = np.empty((P, T), dtype=np.int8)
        cur = np.full(P, panel["start_regime"], dtype=np.int64)
        u_reg = rng.random((P, T))
        for t in range(T):
            if t > 0:
                cur = (u_reg[:, t, None] > cum[cur]).sum(1)
            reg[:, t] = cur
        jj = (rng.random((P, T)) * sizes[reg]).astype(np.int64)
        X = padded[reg, jj]
        pS = np.zeros((K, sizes.max(), nS)); pDays = np.zeros((K, sizes.max()))
        for k in range(K):
            pS[k, : sizes[k]] = panel["pool_S"][k]; pDays[k, : sizes[k]] = panel["pool_days"][k]
        S_draw = pS[reg, jj]
        days_draw = pDays[reg, jj]

    volk = np.ones((P, 1, 1)) if boot else np.exp(rng.normal(0.0, VOL_SCALE_SD, size=(P, 1, 1)))
    dev = (X - mu_sim) * volk
    world = dict(WORLDS[cfg.world])
    if cfg.kappa is not None:
        world["kappa"] = cfg.kappa
    lr = dev[:, :, :N].copy()
    if DRIFT_BY_KIND[drift_name] is None:
        lr += mu_sim[:N] * 1.0
    else:
        table = DRIFT_BY_KIND[drift_name]
        g = np.array([cfg.drift_overrides.get(f, table[k]) for f, k in zip(funds, kinds)])
        common = rng.normal(0.0, COMMON_DRIFT_SD, size=(P, 1, 1))
        persistent = rng.normal(0.0, world["dsd"], size=(P, 1, N))
        drift_m = (np.log1p(g)[None, None, :] + common + persistent) / 12.0
        lr += drift_m
    if cfg.withholding_pct:
        if "yields" not in panel:
            raise ValueError("แผงข้อมูลไม่มี yield ของกอง — คำนวณภาษีปันผลไม่ได้ (ดึงข้อมูลใหม่)")
        y = np.array([float(panel["yields"][f]) for f in funds])
        lr -= (cfg.withholding_pct * y / 12.0)[None, None, :]
    fx = dev[:, :, N] + (0.0 if boot else rng.normal(0.0, FX_DRIFT_SD, size=(P, 1)) / 12.0)
    us_m = dev[:, :, N + 1] + mu_sim[N + 1]
    d_10 = dev[:, :, N + 3]
    oil = dev[:, :, N + 4]

    events = build_events(panel["events_measured"], kinds)
    dmax = max(ev["months"] for ev in events)
    flags = np.zeros((P, len(events), T), dtype=bool)
    ind_store = {}
    if event_mult > 0:
        for e, ev in enumerate(events):
            if cfg.events_only is not None and ev["name"] not in cfg.events_only:
                continue
            start = rng.random((P, T + dmax)) < (ev["rate"] * event_mult / 12.0)
            mag = start * np.exp(rng.normal(-0.03, 0.25, size=(P, T + dmax)))
            d = ev["months"]
            ind = _windowed(mag, d)[:, dmax:]
            active = _windowed(start.astype(np.float64), d)[:, dmax:] > 0
            flags[:, e, :] = active
            ind_store[e] = ind
            tot = ind / d
            lr += tot[:, :, None] * np.array(ev["shock"])[None, None, :]
            fx += tot * ev["fx"]
            e_month = ev["rate"] * event_mult / 12.0 * math.exp(-0.03 + 0.25 ** 2 / 2)
            lr -= (e_month * np.array(ev["shock"]))[None, None, :]
            fx -= e_month * ev["fx"]
            us_m += ind * (ev["infl"] / 100.0 / 12.0)
            oil += tot * ev["oil"]
            d_10 += tot * ev["y10"]

    th = panel["th_fit"]
    th_m = th["a"] / 12.0 + th["b"] * us_m + rng.normal(0.0, th["resid_sd_annual"] / math.sqrt(12), size=(P, T))
    for e, ev in enumerate(events):
        if ev["name"] in ("baht_crisis", "baht_surge") and e in ind_store:
            th_m += ind_store[e] * (ev["infl"] / 100.0 / 12.0)
    cpi_th = np.cumsum(th_m, axis=1)

    live = panel["live_me_logs"]
    Hr = live.shape[0]
    if Hr < 61:
        raise ValueError(f"ประวัติราคาจริงมีแค่ {Hr} เดือน (ต้องการอย่างน้อย 61) — โลกที่ดูผลตอบแทน 5 ปีย้อนหลังคำนวณไม่ได้")
    if "DAR" in cfg.arms and Hr < HISTORY_MONTHS:
        raise ValueError(f"ประวัติราคาจริงมีแค่ {Hr} เดือน สูตร DAR ต้องการ {HISTORY_MONTHS} — จำลอง DAR ไม่ได้")
    first_valid = np.array([panel["first_valid"][f] for f in funds])
    LL = np.empty((P, Hr + T, N))
    LL[:, :Hr, :] = live[None, :, :]
    RS = np.empty((P, 60 + T, nS))
    RS[:, :60, :] = panel["live_S"][None, :, :]
    RS[:, 60:, :] = S_draw * (volk[:, :, 0:1] ** 2)
    RD = np.empty((P, 60 + T))
    RD[:, :60] = panel["live_days"][None, :]
    RD[:, 60:] = days_draw
    iu = np.triu_indices(N)
    LF = np.empty((P, T + 1))
    LF[:, 0] = math.log(panel["fx0"])
    lf_mean = math.log(panel["fx0"])
    for t_ in range(T):
        LF[:, t_ + 1] = LF[:, t_] + fx[:, t_] - cfg.fx_revert * (LF[:, t_] - lf_mean)

    arms = tuple(cfg.arms) + tuple((cfg.fixed or {}).keys())
    sh = {a: np.zeros((P, N)) for a in arms}
    idx_twr = {a: np.ones(P) for a in arms}
    run_max = {a: np.ones(P) for a in arms}
    max_dd = {a: np.zeros(P) for a in arms}
    under = {a: np.zeros(P) for a in arms}
    vusd_prev = {a: np.zeros(P) for a in arms}
    horizon_out = {a: {h: {} for h in cfg.horizons if h <= T} for a in arms}
    contrib_real = np.zeros(P)
    ercy = None
    erc_sweeps = []
    dbg = []
    wsum = {a: np.zeros(N) for a in arms}

    for k in range(T):
        n = Hr - 1 + k
        valid = (n - first_valid) >= (HISTORY_MONTHS - 1)

        if world["kappa"] != 0.0:
            rel = LL[:, n, :] - LL[:, n - 60, :]
            rel = rel - rel.mean(axis=1, keepdims=True)
            delta = -world["kappa"] * rel / 60.0
        else:
            delta = 0.0

        fx_now = np.exp(LF[:, k])
        buy_usd = BUDGET_THB * (1.0 - cfg.fx_spread_pct / 100.0) / fx_now
        price_now = np.exp(LL[:, n, :])

        W = {}
        if "DAR" in arms:
            W["DAR"] = dar_weights_b(LL, n, valid)
        if "EQ" in arms:
            W["EQ"] = np.full((P, N), 1.0 / N)
        if any(a in arms for a in ("ERC", "BLEND")):
            cov = erc_cov_from_S(RS[:, k + 12: k + 60, :][:, -12:, :], RD[:, k + 48: k + 60],
                                 RS[:, k: k + 60, :], RD[:, k: k + 60], iu, N)
            w_erc, ercy, sw = erc_batch(cov, ercy)
            erc_sweeps.append(sw)
            if "ERC" in arms:
                W["ERC"] = w_erc
            if "BLEND" in arms:
                W["BLEND"] = BLEND_ERC_SHARE * w_erc + (1.0 - BLEND_ERC_SHARE) / N
        for name, vec in (cfg.fixed or {}).items():
            W[name] = np.broadcast_to(np.asarray(vec, dtype=float), (P, N)).copy()
        W_dar_raw = W["DAR"].copy() if "DAR" in arms else None
        if cfg.round_units:
            for a in arms:
                # น้ำหนักที่มีกองเป็นศูนย์ตั้งใจ (เช่น VOO ล้วน) ไม่ปัดขั้นต่ำ 1 หน่วย — ปัดแล้วกลายเป็นซื้อทุกกอง ซึ่งไม่ใช่แผนนั้น
                if (W[a] > 0).all():
                    W[a] = round_units_b(W[a], units)
        for a in arms:
            wsum[a] += W[a].mean(axis=0)
        if cfg.debug:
            dbg.append((n, {a: W[a][: cfg.debug].copy() for a in arms}, W_dar_raw[: cfg.debug].copy() if W_dar_raw is not None else None))

        contrib_real += BUDGET_THB / np.exp(cpi_th[:, k - 1] if k > 0 else np.zeros(P))
        LL[:, n + 1, :] = LL[:, n, :] + lr[:, k, :] + delta
        price_next = np.exp(LL[:, n + 1, :])
        fx_next = np.exp(LF[:, k + 1])
        for a in arms:
            net = buy_usd[:, None] * W[a] * (1.0 - FEE)
            sh[a] += net / price_now
            vusd = (sh[a] * price_next).sum(1)
            twr = vusd / (vusd_prev[a] + net.sum(1))
            idx_twr[a] *= twr * (fx_next / fx_now)
            run_max[a] = np.maximum(run_max[a], idx_twr[a])
            max_dd[a] = np.maximum(max_dd[a], 1.0 - idx_twr[a] / run_max[a])
            v_thb = vusd * fx_next
            under[a] += (v_thb < BUDGET_THB * (k + 1))
            vusd_prev[a] = vusd
            if (k + 1) in cfg.horizons:
                horizon_out[a][k + 1] = {"V": v_thb.copy(), "maxdd": max_dd[a].copy(), "under": under[a].copy() / (k + 1)}
        if (k + 1) in cfg.horizons:
            for a in arms:
                horizon_out[a][k + 1]["V_real"] = horizon_out[a][k + 1]["V"] / np.exp(cpi_th[:, k])
                horizon_out[a][k + 1]["contrib_real"] = contrib_real.copy()

    y10_path = np.empty((P, T)); oil_path = np.empty((P, T))
    yc = np.full(P, panel["start_levels"]["y10"]); oc = np.full(P, math.log(panel["start_levels"]["oil"]))
    for t_ in range(T):
        yc = np.clip(yc + 0.0095 * (4.5 - yc) + d_10[:, t_], 0.0, 20.0)
        oc = oc + 0.06 * (math.log(75.0) - oc) + oil[:, t_]
        y10_path[:, t_] = yc; oil_path[:, t_] = np.exp(oc)

    res = {"cfg": cfg, "arms": arms, "H": {}}
    for h in cfg.horizons:
        if h > T:
            continue
        info = {}
        for a in arms:
            ho = horizon_out[a][h]
            info[a] = {"V": ho["V"], "V_real": ho["V_real"], "maxdd": ho["maxdd"], "under": ho["under"],
                       "irr": _irr(ho["V"], h, BUDGET_THB), "contrib_real": ho["contrib_real"]}
        macro = {
            "th_infl_cagr": (np.exp(cpi_th[:, h - 1] / (h / 12.0)) - 1.0),
            "fx_change": np.exp(LF[:, h] - LF[:, 0]) - 1.0,
            "y10_end": y10_path[:, h - 1],
            "oil_max": oil_path[:, :h].max(axis=1),
            "crisis_share": (reg[:, :h] == 2).mean(axis=1),
            "highinfl_share": (reg[:, :h] == 3).mean(axis=1),
            "events": flags[:, :, :h].any(axis=2),
            "equity_cagr": np.exp((LL[:, Hr - 1 + h, :] - LL[:, Hr - 1, :]).mean(axis=1) / (h / 12.0)) - 1.0,
        }
        res["H"][h] = {"arms": info, "macro": macro}
    res["erc_sweeps_max"] = int(np.max(erc_sweeps)) if erc_sweeps else None
    res["mean_weights"] = {a: (wsum[a] / T).tolist() for a in arms}
    res["event_names"] = [e["name"] for e in events]
    if cfg.debug:
        res["dbg"] = dbg
        res["LL"] = LL[: cfg.debug].copy()
        res["LF"] = LF[: cfg.debug].copy()
    return res
