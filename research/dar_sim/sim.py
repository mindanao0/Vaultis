# -*- coding: utf-8 -*-
"""Simulation อนาคต 5–20 ปีของ DCA 5 กอง: DAR vs 1/N vs ERC vs สัดส่วนเดิม 35/25/20/10/10 vs VOO ล้วน.

โครงสร้าง (รายเดือน; เวกเตอร์ข้ามเส้นทาง ไม่วนทีละเส้น):

  1) regime ตลาด 4 แบบ (calm/stress/crisis/highinfl) เปลี่ยนสถานะด้วยเมทริกซ์ที่ **นับจากข้อมูลจริง 1990–2026**
  2) ทุกเดือนสุ่ม "เดือนจริงในอดีต" จากกอง regime นั้น (bootstrap ทั้งเวกเตอร์: 5 กอง + ค่าเงิน + เงินเฟ้อ +
     ดอกเบี้ย + น้ำมัน พร้อมกัน → ความสัมพันธ์ข้ามตัวแปรและหางหนาไม่ได้ถูกสมมติว่าเป็นปกติ)
  3) ลบค่าเฉลี่ยในอดีตออก แล้วใส่ผลตอบแทนคาดหวังข้างหน้า (ข้อสมมติ — ดู DRIFT_SETS) + ความไม่แน่นอนของพารามิเตอร์ต่อเส้นทาง
  4) เหตุการณ์ใหญ่แบบ Poisson (สงคราม/ฟองสบู่/วิกฤตบาท/เงินเฟ้อยาว/ดอลลาร์อ่อน/บูมผลิตภาพ/ช็อกดอกเบี้ย) ซ้อนทับ
  5) "โลกของส่วนต่างระหว่างกอง" 4 แบบ (สุ่มเดิน / ย้อนกลับ / โมเมนตัม / พรีเมียมถาวร) — นี่คือสิ่งที่ตัดสินว่าสูตร
     DAR มีโอกาสชนะหรือแพ้ จึงต้องรายงานแยกโลก ไม่เฉลี่ยรวมเป็นตัวเลขเดียว

ประวัติจริง ~15 ปี (ราคาสิ้นเดือนที่สูตรสดเห็นจริง) ถูกต่อหน้าเส้นทางจำลอง ⇒ สัญญาณ DAR เดือนแรกคือสัญญาณจริงวันนี้
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

N = 5
FUNDS = ["VOO", "SCHD", "QQQM", "XLV", "GLDM"]
from analysis import dar_dca as _dar  # noqa: E402  ค่าคงที่ของสูตรอ่านจากของจริง ไม่คัดลอก (เลขซ้ำ = เพี้ยนเงียบ ๆ)
from portfolio.fees import DIME_FEE_RATE as FEE  # noqa: E402

BUDGET_THB = float(_dar.MONTHLY_BUDGET_THB)
UNITS = int(BUDGET_THB // _dar.ALLOCATION_UNIT_THB)
HORIZONS = (60, 120, 180, 240)
T_MAX = 240

AMP_FROM, AMP_TO, DRIFT_COEF, SD_MIN = _dar.AMP_FROM, _dar.AMP_TO, _dar.DRIFT_COEF, _dar.SD_MIN
FLOOR_FRAC, CAP_MULT, HISTORY_MONTHS = _dar.FLOOR_FRAC, _dar.CAP_MULT, _dar.HISTORY_MONTHS
assert list(_dar.TICKERS) == FUNDS, "รายชื่อกอง DAR เปลี่ยนไป — sim ต้องปรับตาม"

PRESET = np.array([0.35, 0.25, 0.20, 0.10, 0.10])
ARMS = ("DAR", "EQ", "ERC", "PRESET", "VOO")

# ---------------------------------------------------------------- ข้อสมมติ (ประกาศชัด ไม่ใช่ข้อมูล)
# ผลตอบแทนคาดหวังต่อปี (CAGR, USD, รวมปันผล) — ไม่มีใครรู้ค่านี้ จึงรันหลายชุด
DRIFT_SETS = {
    "hist": None,  # ไม่ลบค่าเฉลี่ยในอดีต (2004–2026 ดีผิดปกติ: VOO ~11% QQQM ~15%) — เป็นขอบบนที่ไม่ควรเชื่อ
    "mid": dict(VOO=0.07, SCHD=0.07, QQQM=0.08, XLV=0.07, GLDM=0.04),
    "low": dict(VOO=0.04, SCHD=0.05, QQQM=0.045, XLV=0.05, GLDM=0.03),
}
# โลกของส่วนต่างระหว่างกอง: kappa>0 = กองที่ชนะ 5 ปีมักถอยกลับ, kappa<0 = ชนะต่อ; dsd = ส่วนต่างพรีเมียมถาวรต่อกอง (ต่อปี)
WORLDS = {
    "rw": dict(kappa=0.0, dsd=0.01),
    "rev": dict(kappa=0.3, dsd=0.01),
    "mom": dict(kappa=-0.3, dsd=0.01),
    "prem": dict(kappa=0.0, dsd=0.03),
    # สุ่มบล็อกจากประวัติจริง 2004–2026 ตามลำดับเวลา — ไม่ลบค่าเฉลี่ย ไม่มีเหตุการณ์ที่สมมติ ไม่มี regime (PREREG_NEW)
    "boot": dict(kappa=0.0, dsd=0.0),
}
BOOT_BLOCK = 12
VOL_SCALE_SD = 0.20       # ความผันผวนของแต่ละเส้นทางคูณ lognormal(0, 0.2)
COMMON_DRIFT_SD = 0.015   # ผลตอบแทนระดับตลาดสูง/ต่ำกว่าที่คาด ±1.5%/ปี ต่อเส้นทาง
FX_DRIFT_SD = 0.01        # แนวโน้มค่าเงินบาทต่อเส้นทาง ±1%/ปี
FX_REVERT = 0.006         # แรงดึงกลับของ log USDTHB ต่อเดือน (half-life ~14 ปี) — ปรับให้ช่วง 5/10/20 ปีใกล้ข้อมูลจริง 1981–2026 (ดู calib_fx.py)

# เหตุการณ์ใหญ่: (ชื่อ, ครั้ง/ปี, ระยะ(เดือน), ผลรวม log-return ต่อกอง VOO,SCHD,QQQM,XLV,GLDM, fx(บาทอ่อน=+), เงินเฟ้อเพิ่ม pp/ปี, น้ำมัน(log), 10y pp)
# ตัวที่ "วัด" จากข้อมูลจริงถูกแทนค่าตอนรัน (ดู build_events) ตัวที่ไม่มีข้อมูลเขียนว่า ASSUMED
EVENT_NAMES = ["war_supply", "growth_bust", "stagflation", "usd_debase", "baht_crisis", "baht_surge", "productivity", "rate_shock"]


def build_events(meas: dict) -> list[dict]:
    d = meas["dotcom"]
    spy = d["VOO"]
    return [
        dict(name="war_supply", rate=0.10, months=6,
             shock=[-0.25, -0.18, -0.32, -0.15, 0.18], fx=0.06, infl=4.0, oil=0.50, y10=0.5,
             note="ASSUMED: ความขัดแย้งมหาอำนาจ/ช็อกพลังงาน ~1 ครั้ง/ทศวรรษ ขนาดอิงปี 1990, 2008(น้ำมัน), 2022"),
        dict(name="growth_bust", rate=0.05, months=int(d["months"]),
             shock=[spy, spy + 0.10, d["QQQM"], d["XLV"], 0.10], fx=0.0, infl=-0.5, oil=-0.2, y10=-1.5,
             note="MEASURED SPY/QQQ/XLV 2000-03→2002-10 (ผลตอบแทนรวมจริง) · SCHD=SPY+10% และทอง +10% เป็น ASSUMED"),
        dict(name="stagflation", rate=0.03, months=72,
             shock=[0.05, 0.15, -0.05, 0.15, 0.90], fx=0.15, infl=5.0, oil=0.60, y10=3.0,
             note="ASSUMED: เงินเฟ้อสูงยาวแบบทศวรรษ 1970 (หุ้นแทบไม่โตในราคาปัจจุบัน, ทองพุ่ง) — ไม่มีข้อมูลกองในช่วงนั้นให้วัด"),
        dict(name="usd_debase", rate=0.04, months=36,
             shock=[0.0, 0.0, 0.0, 0.0, 0.45], fx=-0.15, infl=1.0, oil=0.1, y10=1.0,
             note="ASSUMED: ดอลลาร์อ่อนต่อเนื่อง/ลดความเชื่อมั่นสกุลเงินสำรอง: ทองขึ้น บาทแข็ง (กอง USD ในสกุลบาทเสียเปรียบ)"),
        dict(name="baht_crisis", rate=0.02, months=6,
             shock=[0, 0, 0, 0, 0], fx=meas["fx_up_6m"] * 0.5, infl=3.0, oil=0.0, y10=0.0,
             note=f"MEASURED ขนาดจากปี 1997 (บาทอ่อนสูงสุดใน 6 เดือน {meas['fx_up_6m']:.2f} log สิ้น {meas['fx_up_6m_end']}) "
                  "ใช้ครึ่งเดียวเป็นค่ากลาง · อัตราเกิด ASSUMED"),
        dict(name="baht_surge", rate=0.03, months=24,
             shock=[0, 0, 0, 0, 0], fx=-0.12, infl=-0.5, oil=0.0, y10=0.0,
             note="ASSUMED ขนาด: บาทแข็งจากเงินทุนไหลเข้า (วัดจริง 24 เดือนแรงสุด = " f"{meas['fx_down_24m']:.2f} แต่เป็นการคืนตัวหลังวิกฤต 1997)"),
        dict(name="productivity", rate=0.08, months=60,
             shock=[0.12, 0.05, 0.30, 0.10, -0.05], fx=0.0, infl=0.0, oil=0.0, y10=0.5,
             note="ASSUMED: ช่วงบูมจากผลิตภาพ/เทคโนโลยี (ด้านบวก — กันไม่ให้โมเดลมีแต่ข่าวร้าย)"),
        dict(name="rate_shock", rate=0.04, months=9,
             shock=[-0.15, -0.10, -0.22, -0.10, -0.08], fx=0.0, infl=0.0, oil=0.0, y10=1.5,
             note="ASSUMED: ช็อกดอกเบี้ยระยะยาว/พรีเมียมหนี้รัฐ (10y วันนี้ 5.29%) ขนาดอิงปี 2022"),
    ]


# ---------------------------------------------------------------- สูตร DAR แบบเวกเตอร์
def floor_project_b(w: np.ndarray, floor: float) -> np.ndarray:
    """เวอร์ชันเวกเตอร์ของ analysis.dar_dca.floor_project (water-filling)."""
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
    """เวอร์ชันเวกเตอร์ของ analysis.dar_dca.cap_project."""
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
    """น้ำหนัก DAR ของทุกเส้นทาง ณ สิ้นเดือน index n (log ระดับราคา total return).

    ``valid`` (N,) = กองที่มีประวัติ ≥ 181 สิ้นเดือน (เท่ากันทุกเส้นทาง เพราะประวัติจริงเหมือนกัน)
    """
    P = LL.shape[0]
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


def round_units_b(w: np.ndarray) -> np.ndarray:
    """largest-remainder เป็นหน่วยร้อยบาท (เหมือน round_to_units) → สัดส่วนหลังปัด."""
    raw = w * UNITS
    base = np.maximum(np.floor(raw), 1.0)
    if (base.sum(1) > UNITS).any():  # กรณีนี้เกิดไม่ได้เมื่อทุกกอง ≥ 0.2/N=2 หน่วย; ถ้าเกิดต้องรู้
        raise AssertionError("การยกขั้นต่ำ 1 หน่วยทำให้เกินงบ")
    rem = (UNITS - base.sum(1)).astype(int)
    frac = raw - base
    order = np.argsort(-frac, axis=1, kind="stable")
    rank = np.empty_like(order)
    np.put_along_axis(rank, order, np.arange(N)[None, :].repeat(w.shape[0], 0), axis=1)
    base = base + (rank < rem[:, None])
    return base / UNITS


def cf_weights_b(V: np.ndarray, m: np.ndarray, t: np.ndarray) -> np.ndarray:
    """แจกเงินใหม่ m เข้ากองที่ต่ำกว่าเป้า t — คณิตเดียวกับ portfolio.cashflow_rebalance (ไม่ขาย).

    มูลค่าเป้าหลังเติม = t × (มูลค่าพอร์ต + m) · ขาด = max(0, เป้า − ปัจจุบัน) · แจกตามสัดส่วนที่ขาด
    ถ้าขาดรวมน้อยกว่า m ส่วนที่เหลือแจกตามเป้า (พอร์ตชิดเป้าแล้ว = DCA ปกติ)."""
    tot = V.sum(1)
    target = t * (tot + m)[:, None]
    s = np.maximum(target - V, 0.0)
    S = s.sum(1, keepdims=True)
    mm = m[:, None]
    return np.where(S >= mm, s / np.maximum(S, 1e-300), (s + (mm - S) * t) / mm)


def erc_batch(cov: np.ndarray, y0: np.ndarray | None = None, max_sweeps: int = 400, tol: float = 1e-10):
    """ERC ของ covariance หลายก้อนพร้อมกัน — อัลกอริทึมเดียวกับ portfolio.risk_weights.erc_weights (พิกัดละตัว)."""
    P = cov.shape[0]
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


def erc_cov_from_S(S12: np.ndarray, d12: np.ndarray, S60: np.ndarray, d60: np.ndarray, iu) -> np.ndarray:
    """covariance รายปีแบบเดียวกับ portfolio.risk_weights.estimate_covariance: vol จาก 12 เดือนล่าสุด (≈252 วัน)
    × correlation จาก 60 เดือนล่าสุด (≈1260 วัน) จากผลตอบแทนรายวัน **เป็นบาท** — รับ S = Σ r rᵀ ของแต่ละเดือน."""
    P = S12.shape[0]
    tot12 = S12.sum(1) / d12.sum(1, keepdims=True)         # (P,15)
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
    kappa: float | None = None    # ทับค่า kappa ของโลก (ไว้กวาดหาจุดคุ้มทุน)
    arms: tuple = ARMS
    horizons: tuple = HORIZONS
    fixed: dict | None = None     # {ชื่อ: น้ำหนักคงที่ (N,)} — ไว้วิเคราะห์ headroom
    events_only: tuple | None = None   # วินิจฉัย: เปิดเฉพาะเหตุการณ์ที่ระบุชื่อ
    debug: int = 0                # เก็บ LL/น้ำหนักของ n เส้นทางแรกไว้ตรวจเทียบ


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


def run_chunk(panel: dict, cfg: Config) -> dict:
    rng = np.random.default_rng(cfg.seed)
    P, T = cfg.P, cfg.T
    cols = panel["cols"]  # FUNDS + fx us_infl d_ffr d_10y oil
    D = len(cols)
    pools = panel["pools"]
    K = len(pools)
    sizes = np.array([len(p) for p in pools])
    padded = np.zeros((K, sizes.max(), D))
    for k, p in enumerate(pools):
        padded[k, : len(p)] = p
    trans = panel["trans"]
    pi = _stationary(trans)
    # ค่าเฉลี่ยที่ regime-stationary จะให้ (ไว้ลบออกแล้วใส่ผลตอบแทนคาดหวังแทน)
    mu_sim = sum(pi[k] * pools[k].mean(axis=0) for k in range(K))

    boot = cfg.world == "boot"
    drift_name = "hist" if boot else cfg.drift
    event_mult = 0.0 if boot else cfg.event_mult
    nS = len(panel["pool_S"][0][0])
    if boot:
        # สุ่มบล็อก BOOT_BLOCK เดือนจากประวัติจริงตามลำดับเวลา (ไม่พึ่ง regime/เหตุการณ์/การลบค่าเฉลี่ย)
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
        # ---- regime ต่อเดือน
        cum = np.cumsum(trans, axis=1)
        reg = np.empty((P, T), dtype=np.int8)
        cur = np.full(P, panel["start_regime"], dtype=np.int64)
        u_reg = rng.random((P, T))
        for t in range(T):
            # เดือนแรก = สถานะปัจจุบันของตลาด (จากข้อมูลล่าสุด) ไม่ใช่การสุ่ม
            if t > 0:
                cur = (u_reg[:, t, None] > cum[cur]).sum(1)
            reg[:, t] = cur
        jj = (rng.random((P, T)) * sizes[reg]).astype(np.int64)
        X = padded[reg, jj]  # (P,T,D)
        pS = np.zeros((K, sizes.max(), nS)); pDays = np.zeros((K, sizes.max()))
        for k in range(K):
            pS[k, : sizes[k]] = panel["pool_S"][k]; pDays[k, : sizes[k]] = panel["pool_days"][k]
        S_draw = pS[reg, jj]       # (P,T,15) ความแปรปรวนร่วมรายวันที่เกิดจริงของเดือนที่สุ่มได้ (บาท)
        days_draw = pDays[reg, jj]  # (P,T)

    # ---- ลบค่าเฉลี่ยในอดีต + ความผันผวนต่อเส้นทาง + ผลตอบแทนคาดหวัง
    volk = np.ones((P, 1, 1)) if boot else np.exp(rng.normal(0.0, VOL_SCALE_SD, size=(P, 1, 1)))
    dev = (X - mu_sim) * volk
    world = dict(WORLDS[cfg.world])
    if cfg.kappa is not None:
        world["kappa"] = cfg.kappa
    fund_ix = np.arange(N)
    lr = dev[:, :, :N].copy()
    if DRIFT_SETS[drift_name] is None:
        lr += mu_sim[:N] * 1.0
        drift_m = np.zeros((P, 1, N))
    else:
        g = np.array([DRIFT_SETS[drift_name][f] for f in FUNDS])
        common = rng.normal(0.0, COMMON_DRIFT_SD, size=(P, 1, 1))
        persistent = rng.normal(0.0, world["dsd"], size=(P, 1, N))
        drift_m = (np.log1p(g)[None, None, :] + common + persistent) / 12.0
        lr += drift_m
    fx = dev[:, :, N] + (0.0 if boot else rng.normal(0.0, FX_DRIFT_SD, size=(P, 1)) / 12.0)
    us_m = dev[:, :, N + 1] + mu_sim[N + 1]
    d_ffr = dev[:, :, N + 2]
    d_10 = dev[:, :, N + 3]
    oil = dev[:, :, N + 4]

    # ---- เหตุการณ์ใหญ่ (มีช่วงอบอุ่น: เหตุการณ์ที่เริ่มก่อนเดือนแรกยังอยู่ต่อเข้ามาได้ และเหตุการณ์ใกล้ปลายขอบฟ้า
    #      ไม่ถูกตัดทิ้ง — ไม่งั้นการหักค่าคาดหวังออกจะหักเกินจริง)
    events = build_events(panel["events_measured"])
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
            ind = _windowed(mag, d)[:, dmax:]           # ขนาดรวมของเหตุการณ์ที่กำลังดำเนินอยู่ ณ เดือน t
            active = _windowed(start.astype(np.float64), d)[:, dmax:] > 0
            flags[:, e, :] = active
            ind_store[e] = ind
            tot = ind / d                                # กระจายผลรวมเท่า ๆ กันตลอดระยะ
            lr += tot[:, :, None] * np.array(ev["shock"])[None, None, :]
            fx += tot * ev["fx"]
            # เหตุการณ์เพิ่ม "ความเสี่ยง/หาง" แต่ไม่เลื่อนค่าเฉลี่ยที่ตั้งไว้: หักผลคาดหวังต่อเดือนของเหตุการณ์นี้ออก
            # (ค่าคาดหวัง = อัตราเกิด/12 × ผลรวม × E[ขนาด]; ขนาด ~ lognormal(−0.03, 0.25) → E = exp(−0.03+0.25²/2))
            e_month = ev["rate"] * event_mult / 12.0 * math.exp(-0.03 + 0.25 ** 2 / 2)
            lr -= (e_month * np.array(ev["shock"]))[None, None, :]
            fx -= e_month * ev["fx"]
            us_m += ind * (ev["infl"] / 100.0 / 12.0)
            oil += tot * ev["oil"]
            d_10 += tot * ev["y10"]

    # ---- เงินเฟ้อไทย = a + b×สหรัฐ + สัญญาณรบกวน (ความมั่นใจต่ำ: R²=0.45 รายปี) + ผลเหตุการณ์ฝั่งบาทอ่อน
    th = panel["th_fit"]
    th_m = th["a"] / 12.0 + th["b"] * us_m + rng.normal(0.0, th["resid_sd_annual"] / math.sqrt(12), size=(P, T))
    for e, ev in enumerate(events):
        if ev["name"] in ("baht_crisis", "baht_surge") and e in ind_store:
            th_m += ind_store[e] * (ev["infl"] / 100.0 / 12.0)
    cpi_th = np.cumsum(th_m, axis=1)  # log ดัชนีเงินเฟ้อไทย ณ สิ้นเดือน t

    # ---- ประวัติจริง + วงจรรายเดือน
    live = panel["live_me_logs"]            # (Hr, N) log ระดับราคา; NaN ก่อนลิสต์
    Hr = live.shape[0]
    first_valid = np.array([panel["first_valid"][f] for f in FUNDS])
    LL = np.empty((P, Hr + T, N))
    LL[:, :Hr, :] = live[None, :, :]
    thb_hist = panel["thb_hist"]            # (60, N) ผลตอบแทน log เป็นบาทจริง 60 เดือนล่าสุด
    RS = np.empty((P, 60 + T, nS))
    RS[:, :60, :] = panel["live_S"][None, :, :]
    RS[:, 60:, :] = S_draw * (volk[:, :, 0:1] ** 2)   # ความผันผวนของเส้นทางคูณเข้าไปด้วย (ให้ ERC เห็นโลกเดียวกับที่สุ่ม)
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
    sh = {a: np.zeros((P, N)) for a in arms}            # จำนวนหน่วย (USD ที่ซื้อ / ราคา)
    idx_twr = {a: np.ones(P) for a in arms}
    run_max = {a: np.ones(P) for a in arms}
    max_dd = {a: np.zeros(P) for a in arms}
    under = {a: np.zeros(P) for a in arms}
    vusd_prev = {a: np.zeros(P) for a in arms}
    horizon_out = {a: {h: {} for h in cfg.horizons if h <= T} for a in arms}
    contrib_real = np.zeros(P)  # Σ 5000/CPI_j
    ercy = None
    erc_sweeps = []
    dbg = []
    wsum_dar = np.zeros(N)
    valid_mask_cache = {}

    for k in range(T):
        n = Hr - 1 + k
        valid = (n - first_valid) >= (HISTORY_MONTHS - 1)

        # โลกที่มีผลป้อนกลับ: ส่วนต่าง 5 ปีย้อนกลับ/ต่อเนื่อง (ใช้ประวัติจริงในช่วง 5 ปีแรกด้วย)
        if world["kappa"] != 0.0:
            rel = LL[:, n, :] - LL[:, n - 60, :]
            rel = rel - rel.mean(axis=1, keepdims=True)
            delta = -world["kappa"] * rel / 60.0
        else:
            delta = 0.0

        # ---- ราคา/เงินเดือนนี้ (ใช้ทั้งซื้อและคำนวณ cashflow rebalance)
        fx_now = np.exp(LF[:, k])
        buy_usd = BUDGET_THB / fx_now
        price_now = np.exp(LL[:, n, :])

        # ---- น้ำหนักของแต่ละกลยุทธ์ ณ เดือนนี้
        W = {}
        if "DAR" in arms:
            W["DAR"] = dar_weights_b(LL, n, valid)
        if "EQ" in arms:
            W["EQ"] = np.full((P, N), 1.0 / N)
        if "PRESET" in arms:
            W["PRESET"] = np.broadcast_to(PRESET, (P, N)).copy()
        if "VOO" in arms:
            v = np.zeros((P, N)); v[:, 0] = 1.0
            W["VOO"] = v
        if any(a in arms for a in ("ERC", "CF_ERC", "BLEND")):
            cov = erc_cov_from_S(RS[:, k + 12: k + 60, :][:, -12:, :], RD[:, k + 48: k + 60],
                                 RS[:, k: k + 60, :], RD[:, k: k + 60], iu)
            w_erc, ercy, sw = erc_batch(cov, ercy)
            erc_sweeps.append(sw)
            if "ERC" in arms:
                W["ERC"] = w_erc
            if "BLEND" in arms:
                W["BLEND"] = 0.5 * w_erc + 0.5 / N
            if "CF_ERC" in arms:
                W["CF_ERC"] = floor_project_b(cf_weights_b(sh["CF_ERC"] * price_now, buy_usd, w_erc), FLOOR_FRAC / N)
        if "CF_EQ" in arms:
            W["CF_EQ"] = floor_project_b(
                cf_weights_b(sh["CF_EQ"] * price_now, buy_usd, np.full((P, N), 1.0 / N)), FLOOR_FRAC / N)
        for name, vec in (cfg.fixed or {}).items():
            W[name] = np.broadcast_to(np.asarray(vec, dtype=float), (P, N)).copy()
        W_dar_raw = W["DAR"].copy() if "DAR" in arms else None
        if cfg.round_units:
            for a in arms:
                if a != "VOO":
                    W[a] = round_units_b(W[a])
        if "DAR" in arms:
            wsum_dar += W["DAR"].mean(axis=0)
        if cfg.debug:
            dbg.append((n, {a: W[a][: cfg.debug].copy() for a in arms}, W_dar_raw[: cfg.debug].copy() if W_dar_raw is not None else None))

        # ---- ซื้อ: เงินบาท → USD ที่ fx สิ้นเดือนก่อน หักค่าธรรมเนียม ซื้อที่ราคาปิดสิ้นเดือนก่อน
        contrib_real += BUDGET_THB / np.exp(cpi_th[:, k - 1] if k > 0 else np.zeros(P))
        # ---- ผลตอบแทนเดือนนี้
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
                horizon_out[a][k + 1] = {
                    "V": v_thb.copy(), "maxdd": max_dd[a].copy(),
                    "under": under[a].copy() / (k + 1),
                }
        if (k + 1) in cfg.horizons:
            for a in arms:
                horizon_out[a][k + 1]["V_real"] = horizon_out[a][k + 1]["V"] / np.exp(cpi_th[:, k])
                horizon_out[a][k + 1]["contrib_real"] = contrib_real.copy()

    # ---- ดอกเบี้ย 10 ปี และน้ำมัน: ดึงกลับสู่ระดับยาว (ข้อสมมติ: 10y→4.5%, WTI→$75; half-life ~6 ปี/~3 ปี)
    #      ใช้อธิบาย/แบ่งกลุ่มผลเท่านั้น — ไม่เข้ากลยุทธ์ใด
    y10_path = np.empty((P, T)); oil_path = np.empty((P, T))
    yc = np.full(P, panel["start_levels"]["y10"]); oc = np.full(P, math.log(panel["start_levels"]["oil"]))
    for t_ in range(T):
        yc = np.clip(yc + 0.0095 * (4.5 - yc) + d_10[:, t_], 0.0, 20.0)
        oc = oc + 0.06 * (math.log(75.0) - oc) + oil[:, t_]
        y10_path[:, t_] = yc; oil_path[:, t_] = np.exp(oc)

    # ---- ตัวชี้วัดต่อเส้นทาง/ขอบฟ้า
    res = {"cfg": cfg, "arms": arms, "H": {}}
    for h in cfg.horizons:
        if h > T:
            continue
        info = {}
        for a in arms:
            ho = horizon_out[a][h]
            info[a] = {
                "V": ho["V"], "V_real": ho["V_real"], "maxdd": ho["maxdd"], "under": ho["under"],
                "irr": _irr(ho["V"], h), "contrib_real": ho["contrib_real"],
            }
        # บริบทเศรษฐกิจของเส้นทางในขอบฟ้านี้
        macro = {
            "th_infl_cagr": (np.exp(cpi_th[:, h - 1] / (h / 12.0)) - 1.0),
            "fx_change": np.exp(LF[:, h] - LF[:, 0]) - 1.0,
            "y10_end": y10_path[:, h - 1],
            "oil_max": oil_path[:, :h].max(axis=1),
            "crisis_share": (reg[:, :h] == 2).mean(axis=1),
            "highinfl_share": (reg[:, :h] == 3).mean(axis=1),
            "events": flags[:, :, :h].any(axis=2),
            "equity_cagr": np.exp((LL[:, Hr - 1 + h, :4] - LL[:, Hr - 1, :4]).mean(axis=1) / (h / 12.0)) - 1.0,
        }
        res["H"][h] = {"arms": info, "macro": macro}
    res["erc_sweeps_mean"] = float(np.mean(erc_sweeps)) if erc_sweeps else None
    res["erc_sweeps_max"] = int(np.max(erc_sweeps)) if erc_sweeps else None
    res["dar_mean_weights"] = (wsum_dar / T).tolist() if "DAR" in arms else None
    res["event_names"] = [e["name"] for e in events]
    if cfg.debug:
        res["dbg"] = dbg
        res["LL"] = LL[: cfg.debug].copy()
        res["LF"] = LF[: cfg.debug].copy()
    return res


def _irr(V: np.ndarray, h: int) -> np.ndarray:
    """IRR รายปี (บาท) จากเงินลง 5,000 ต้นเดือนทุกเดือน h เดือน ได้มูลค่า V ปลายเดือนที่ h (bisection).

    มูลค่าอนาคตของเงินลง = B·Σ_{j<h}(1+r)^{h-j} = B·(1+r)·((1+r)^h − 1)/r  (r→0: B·h)."""
    lo = np.full(V.shape, -0.05)
    hi = np.full(V.shape, 0.10)
    for _ in range(60):
        mid = (lo + hi) / 2.0
        small = np.abs(mid) < 1e-12
        g = 1.0 + mid
        fv = BUDGET_THB * np.where(small, float(h), g * (g ** h - 1.0) / np.where(small, 1.0, mid))
        too_low = fv < V
        lo = np.where(too_low, mid, lo)
        hi = np.where(too_low, hi, mid)
    return (1.0 + (lo + hi) / 2.0) ** 12 - 1.0
