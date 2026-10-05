# -*- coding: utf-8 -*-
"""พิสูจน์ว่าโค้ดเวกเตอร์ใน sim.py ให้ผลเท่ากับฟังก์ชันจริงของโปรเจกต์ (ไม่เชื่อตัวเอง).

ใช้: python verify.py <scratch_dir>   → ออกด้วยรหัส ≠ 0 ถ้าข้อใดไม่ผ่าน
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import sim  # noqa: E402
from analysis import dar_dca  # noqa: E402
from portfolio.risk_weights import erc_weights  # noqa: E402

scratch = Path(sys.argv[1])
panel = pd.read_pickle(scratch / "panel.pkl")
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        fails.append(name)


rng = np.random.default_rng(1)

# 1) floor/cap เวกเตอร์ = ฟังก์ชันจริง
w = rng.dirichlet(np.ones(5) * 0.3, size=2000)
w[:50] = 0.0
w[:50, 0] = 1.0
ref_f = np.array([dar_dca.floor_project(x, 0.04) for x in w])
mine_f = sim.floor_project_b(w, 0.04)
check("floor_project เวกเตอร์ = ของจริง", np.allclose(ref_f, mine_f, atol=1e-12), f"max diff {np.abs(ref_f-mine_f).max():.2e}")
raw = rng.dirichlet(np.ones(5) * 0.4, size=2000)
ref_c = np.array([dar_dca.cap_project(x, 0.3) for x in raw])
mine_c = sim.cap_project_b(raw, 0.3)
check("cap_project เวกเตอร์ = ของจริง", np.allclose(ref_c, mine_c, atol=1e-12), f"max diff {np.abs(ref_c-mine_c).max():.2e}")

# 2) น้ำหนัก DAR จากประวัติจริงวันนี้ = dar_weights() ของโปรเจกต์ (ตัวเลขที่ผู้ใช้เห็นบนหน้าจอ)
me = panel["live_me_df"]
ref_w, sigs = dar_dca.dar_weights(me, siblings=panel["stats"]["proxies_live"])
LL = np.log(me.to_numpy())[None, :, :]
first_valid = np.array([panel["first_valid"][f] for f in sim.FUNDS])
n = LL.shape[1] - 1
valid = (n - first_valid) >= 180
mine = sim.dar_weights_b(LL, n, valid)[0]
check("DAR ณ สิ้นเดือน ก.ย. 2026 (ประวัติจริง) = dar_weights()", np.allclose(ref_w.to_numpy(), mine, atol=1e-12),
      f"project={np.round(ref_w.to_numpy(),4)} mine={np.round(mine,4)}")
print("  กองที่ยังเป็น neutral วันนี้:", [s.ticker for s in sigs if s.neutral_reason])

# 3) หลายเดือนในเส้นทางจำลอง: สร้าง DataFrame ราคาสิ้นเดือนแล้วเรียก dar_weights() จริงทีละเดือน
cfg = sim.Config(world="rev", drift="mid", P=40, T=60, seed=7, debug=6, arms=("DAR", "EQ", "ERC", "PRESET", "VOO"))
res = sim.run_chunk(panel, cfg)
LLd = res["LL"]
Hr = me.shape[0]
worst = 0.0
rounded_worst = 0.0
for (nn, W, Wraw) in res["dbg"][::7]:
    for p in range(6):
        end_ts = pd.Timestamp("2026-09-30") + pd.offsets.MonthEnd(nn - (Hr - 1))
        df = pd.DataFrame(np.exp(LLd[p, : nn + 1, :]), columns=sim.FUNDS,
                          index=pd.date_range(end=end_ts, periods=nn + 1, freq="ME"))
        df = df.where(np.isfinite(df))
        ww, _ = dar_dca.dar_weights(df)
        worst = max(worst, float(np.abs(ww.to_numpy() - Wraw[p]).max()))
        units = dar_dca.round_to_units(ww, 5000.0)
        ru = np.array([units[t] for t in sim.FUNDS]) / 5000.0
        rounded_worst = max(rounded_worst, float(np.abs(ru - W["DAR"][p]).max()))
check("DAR ในเส้นทางจำลอง (หลายเดือน/หลายเส้นทาง) = dar_weights() จริง", worst < 1e-10, f"max diff {worst:.2e}")
check("การปัดหน่วยร้อยบาทเวกเตอร์ = round_to_units() จริง", rounded_worst < 1e-12, f"max diff {rounded_worst:.2e}")

# 4) ERC เวกเตอร์ = erc_weights() จริง (covariance แบบที่พอร์ตนี้เจอ: correlation สูงมาก)
cors = []
for _ in range(300):
    a = rng.normal(size=(5, 5)) * 0.15 + np.array([1, 0.9, 0.95, 0.7, 0.1])[:, None] * rng.normal(size=(1, 5)) * 0.3
    cov = a @ a.T / 5 + np.diag(rng.uniform(0.01, 0.05, 5))
    cors.append(cov)
cors = np.array(cors)
wv, yv, sweeps = sim.erc_batch(cors)
ref = np.array([erc_weights(c) for c in cors])
check("ERC เวกเตอร์ = erc_weights() จริง", np.allclose(ref, wv, atol=1e-7), f"max diff {np.abs(ref-wv).max():.2e}, sweeps={sweeps}")

# 5) บัญชีเงิน: คำนวณซ้ำด้วยลูปธรรมดาสำหรับเส้นทางเดียว (ค่าธรรมเนียม, ค่าเงิน, เวลาซื้อ)
cfg2 = sim.Config(world="rw", drift="mid", P=4, T=60, seed=3, debug=4, arms=("EQ",), round_units=False)
r2 = sim.run_chunk(panel, cfg2)
p = 0
LLp, LFp = r2["LL"][p], r2["LF"][p]
hold = np.zeros(5)
for k in range(60):
    nn = Hr - 1 + k
    buy_usd = 5000.0 / math.exp(LFp[k])
    hold += buy_usd * (1.0 / 5) * (1 - 0.0015) / np.exp(LLp[nn])
v_ref = float((hold * np.exp(LLp[Hr - 1 + 60])).sum() * math.exp(LFp[60]))
check("บัญชีเงิน 1/N ซื้อรายเดือน (ลูปธรรมดา) = ที่ sim คิด", abs(v_ref - r2["H"][60]["arms"]["EQ"]["V"][p]) / v_ref < 1e-9,
      f"ref={v_ref:,.2f} sim={r2['H'][60]['arms']['EQ']['V'][p]:,.2f}")

# IRR ย้อนกลับ: ใส่ IRR ที่ได้กลับเข้าไปต้องได้ V เดิม
irr = r2["H"][60]["arms"]["EQ"]["irr"][p]
m = (1 + irr) ** (1 / 12) - 1
fv = 5000.0 * sum((1 + m) ** (60 - j) for j in range(60))
check("IRR ใส่กลับได้มูลค่าเดิม", abs(fv - v_ref) / v_ref < 1e-6, f"fv={fv:,.2f}")

# 6) ERC แบบรายเดือนที่ sim ใช้ ใกล้เคียง ERC รายวันของจริง (vol 252 วัน × corr 1260 วัน) แค่ไหน — วัดบนข้อมูลจริง
daily = pd.read_pickle(scratch / "daily.pkl")
macro = pd.read_pickle(scratch / "macro.pkl")
from portfolio.risk_weights import estimate_covariance  # noqa: E402

fx = macro["usdthb"]
spl = {}
from analysis.proxy_history import splice_with_proxy  # noqa: E402

px = daily[["VOO", "SCHD", "QQQM", "XLV", "GLDM", "QQQ", "GLD"]].copy()
px2, _ = splice_with_proxy(px, ["VOO", "SCHD", "QQQM", "XLV", "GLDM"])
px2 = px2.loc["2013-01-01":]
thb = px2.mul(fx.reindex(px2.index).ffill(limit=3), axis=0).dropna()
diffs = []
iu = np.triu_indices(5)
S_all, S_days = panel["S_all"], panel["S_days"]
for end in pd.date_range("2018-12-31", "2026-08-31", freq="3ME"):
    d = thb.loc[:end]
    if len(d) < 1300:
        continue
    w_d = erc_weights(estimate_covariance(d)[0])
    rows = S_all.loc[:end]
    S60, d60 = rows.tail(60).to_numpy()[None], S_days.loc[rows.tail(60).index].to_numpy()[None]
    S12, d12 = rows.tail(12).to_numpy()[None], S_days.loc[rows.tail(12).index].to_numpy()[None]
    w_m = erc_weights(sim.erc_cov_from_S(S12, d12, S60, d60, iu)[0])
    diffs.append(np.abs(w_d - w_m).max())
print(f"  ข้อมูล: ERC จาก S รายเดือน (ตัวประมาณที่ sim ใช้) vs ERC รายวันจริงของโปรเจกต์ — ต่างกันสูงสุด {max(diffs)*100:.2f} จุด% เฉลี่ย {np.mean(diffs)*100:.2f} จุด% ({len(diffs)} จุดเวลา)")
check("ERC ตัวประมาณของ sim ใกล้ ERC จริง (ต่างไม่เกิน 3 จุด%)", max(diffs) < 0.03, f"{max(diffs):.4f}")

print("\nสรุป:", "ทุกข้อผ่าน" if not fails else f"ไม่ผ่าน {fails}")
sys.exit(1 if fails else 0)
