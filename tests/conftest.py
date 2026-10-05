# -*- coding: utf-8 -*-
"""Fixture กลางของชุดเทสต์."""

import importlib
import shutil
import sys
from pathlib import Path

import pytest

from backend.services.cache_service import shared_cache
from utils.cache import clear_all_caches

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# ไฟล์ข้อมูลจริงของผู้ใช้ที่ชุดเทสต์ต้องแตะไม่ได้
#
# ที่มา (AUDIT_ROUND2_2026-08-07 ข้อ HIGH): รอบ 0-A ปิดไปแล้วเฉพาะฐาน SQLite แต่คำสั่ง
# รันเทสต์จริงคือ `docker compose --profile dev run --rm -v "$PWD:/app" tests` —
# repo ถูก mount ทับ /app ⇒ ค่าดีฟอลต์ของ ALERTS_PATH / TRANSACTIONS_FILE / CONFIG_PATH
# คือไฟล์จริงบน host  โพรบตัวหนึ่งเรียก `_save_alerts()` / `delete_transaction()` โดยไม่
# stub path แล้วคลัง alert ของผู้ใช้ถูกล้างเป็น {"alerts": []} จริง ๆ (ไฟล์ถูก gitignore
# จึงไม่มีสำเนาใน git ให้กู้)
#
# ตาข่ายสองชั้นที่ต้องมีทั้งคู่ — ชั้นนี้คือชั้นที่ทำงานแม้ "ลืมตั้ง env":
#   1. docker-compose.yml service `tests` ตั้ง VAULTIS_LEDGER_PATH / VAULTIS_ALERTS_PATH
#   2. fixture `_isolate_user_data_files` ด้านล่าง — ย้าย path ที่ยังชี้ไฟล์จริงเข้าแซนด์บ็อกซ์
#      ต่อทุกเทสต์ แล้วตรวจซ้ำตอนจบว่าไม่มีใครชี้กลับไป
# เทสต์ที่ตรวจตาข่ายนี้: tests/test_data_file_isolation.py
#
# หมายเหตุ "ย้าย" ไม่ใช่ "assert แล้วล้มทั้งชุด": การรัน pytest นอก Docker (ไม่มี env)
# ต้องยังใช้งานได้ตามปกติ — fail-closed ต้องปิดเฉพาะเส้นทางที่เชื่อถือไม่ได้ ไม่ใช่ปิดทั้งแอป
# ส่วนที่ "ล้มดัง" คือชั้น compose ใน tests/test_data_file_isolation.py ซึ่งฟ้องตรง ๆ
# ว่าไฟล์ตั้งค่าขาดอะไรไป
# ---------------------------------------------------------------------------
USER_DATA_FILES: tuple[dict, ...] = (
    {
        # โฟลเดอร์ข้อมูลของ simulation (ราคาที่ดึงสด + manifest + ผลล่าสุด) — **สร้างใหม่ได้** (ดึงใหม่) จึงไม่นับเป็นของที่ทดแทนไม่ได้
        # แต่เทสต์ห้ามเขียนทับของจริงที่ scheduler ในคอนเทนเนอร์กำลังอ่านอยู่
        "label": "ข้อมูล simulation",
        "module": "simulation.data",
        "attr": "DATA_DIR",
        "real": REPO_ROOT / "simulation" / "data",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        "label": "คลัง price alert",
        "module": "alerts.price_alert",
        "attr": "ALERTS_PATH",
        "real": REPO_ROOT / "alerts" / "data" / "price_alerts.json",
        # ไม่คัดลอกของจริงไปแซนด์บ็อกซ์: "ไม่มีไฟล์" = ยังไม่เคยตั้ง alert ซึ่งเป็นสถานะ
        # ที่โค้ดรองรับอยู่แล้ว (fresh clone / GitHub Actions ก็เจอแบบนี้)
        "seed": False,
        # DATA_DIR ของ tracker ต้องเดินตามไฟล์ — ที่นี่ไม่มีตัวคู่
        "mirror_parent": (),
        "reset": {},
    },
    {
        "label": "สมุดบัญชี (ledger)",
        "module": "portfolio.tracker",
        "attr": "TRANSACTIONS_FILE",
        "real": REPO_ROOT / "portfolio" / "data" / "transactions.csv",
        "seed": False,
        # ไม่ย้าย DATA_DIR ตาม = _ensure_storage() ยัง mkdir/เขียนที่โฟลเดอร์จริง
        "mirror_parent": ("DATA_DIR",),
        "reset": {},
    },
    {
        "label": "scheduler state",
        "module": "main",
        "attr": "SCHEDULER_STATE_PATH",
        "real": REPO_ROOT / ".scheduler_state.json",
        "seed": False,
        "mirror_parent": (),
        # จำในหน่วยความจำว่าเดือนไหนส่งแล้ว — ต้องเริ่มว่างทุกเทสต์
        "reset": {"_monthly_plan_sent_in_process": set()},
    },
    {
        # พอร์ตทดลอง DAR-DCA — สมุดแยกจากพอร์ตหลัก, gitignored ⇒ หายแล้วกู้ไม่ได้เหมือนกัน
        "label": "สมุด DAR-DCA",
        "module": "portfolio.dar_ledger",
        "attr": "DAR_LEDGER_PATH",
        "real": REPO_ROOT / "portfolio" / "data" / "dar_transactions.csv",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        "label": "สถานะงานแผน DAR-DCA",
        "module": "jobs.dar_monthly",
        "attr": "DAR_STATE_PATH",
        "real": REPO_ROOT / ".dar_scheduler_state.json",
        "seed": False,
        "mirror_parent": (),
        "reset": {"_dar_sent_in_process": set()},
    },
    {
        # พอร์ตทดลอง SELECT-DCA ("โมเดลเลือกกองเอง") — สมุดแยกจากพอร์ตหลักและพอร์ต DAR, gitignored ⇒ หายแล้วกู้ไม่ได้
        "label": "สมุด SELECT-DCA",
        "module": "portfolio.select_ledger",
        "attr": "SELECT_LEDGER_PATH",
        "real": REPO_ROOT / "portfolio" / "data" / "select_transactions.csv",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        "label": "สถานะงานแผน SELECT-DCA",
        "module": "jobs.select_monthly",
        "attr": "SELECT_STATE_PATH",
        "real": REPO_ROOT / ".select_scheduler_state.json",
        "seed": False,
        "mirror_parent": (),
        "reset": {"_select_sent_in_process": set()},
    },
    {
        # พอร์ตกระดาษ STOCK-DCA (เลือกหุ้นรายตัว) — forward test ที่กฎห้ามย้อนบันทึก/ลบทิ้ง ⇒ หายแล้วกู้ไม่ได้
        "label": "สมุด STOCK-DCA",
        "module": "portfolio.stock_ledger",
        "attr": "STOCK_LEDGER_PATH",
        "real": REPO_ROOT / "portfolio" / "data" / "stock_transactions.csv",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        # สมุดคำทำนาย PREDICT — กติกาล็อกห้ามแก้/ลบ/ย้อนบันทึก ⇒ หายแล้วกู้ไม่ได้ (หลักฐาน forward test ทั้งก้อน)
        "label": "สมุดคำทำนาย PREDICT",
        "module": "portfolio.predict_ledger",
        "attr": "PREDICT_LEDGER_PATH",
        "real": REPO_ROOT / "portfolio" / "data" / "predict_log.csv",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        # สมุดคำทำนาย TRADE (หุ้นรายตัว รายวัน) — บัญชีกระดาษเล่นซ้ำจากสมุดนี้ ⇒ หายแล้วกู้ไม่ได้ (หลักฐาน forward test ทั้งก้อน)
        "label": "สมุดคำทำนาย TRADE",
        "module": "portfolio.trade_ledger",
        "attr": "TRADE_LEDGER_PATH",
        "real": REPO_ROOT / "portfolio" / "data" / "trade_log.csv",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        # ที่เก็บสแนปช็อตของสมุด — เทสต์ห้ามเขียนลงที่จริง (ปน/ตัดสแนปช็อตหลักฐานของจริง)
        "label": "ที่สำรองสมุด",
        "module": "jobs.backup_ledgers",
        "attr": "BACKUP_DIR",
        "real": REPO_ROOT / ".docker-data" / "backups",
        "seed": False,
        "mirror_parent": (),
        "reset": {},
    },
    {
        "label": "config.json",
        "module": "utils.config",
        "attr": "CONFIG_PATH",
        "real": REPO_ROOT / "config.json",
        # อันนี้ **ต้อง** คัดลอกของจริงไป ไม่งั้น load_config() จะตกไปใช้ค่า default
        # ทั้งชุด = เปลี่ยนค่าที่เทสต์อื่นอ่าน (tickers/งบ DCA) แบบเงียบ ๆ
        "seed": True,
        "mirror_parent": (),
        # แคชของ load_config() ผูกกับ mtime ของไฟล์ — เปลี่ยน path แล้วต้องล้าง
        "reset": {"_cache": None},
    },
)


# ไฟล์ที่ถูก gitignore = ไม่มีสำเนาใน git ให้กู้ถ้าหาย (config.json ไม่อยู่ในชุดนี้เพราะ
# track ใน git และคน/เอเจนต์แก้ระหว่างวันได้ตามปกติ — ใส่มาจะกลายเป็นสัญญาณเท็จ)
_IRREPLACEABLE = tuple(
    spec for spec in USER_DATA_FILES if spec["label"] not in ("config.json", "ข้อมูล simulation")
)


def _fingerprint(path: Path) -> tuple[int, int] | None:
    """``(ขนาด, mtime_ns)`` หรือ ``None`` เมื่อไม่มีไฟล์ — "ไฟล์หายไป" ต้องจับได้ด้วย."""
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_size, stat.st_mtime_ns)


@pytest.fixture(scope="session", autouse=True)
def _irreplaceable_user_files_must_survive_the_suite():
    """ตาข่ายชั้นนอกสุด: จับการเขียนที่ **ไม่ได้ผ่าน** ตัวแปร path ของโมดูลด้วย.

    เช่นเทสต์/โพรบที่เปิดไฟล์ด้วย path ตรง ๆ — การย้าย path ระดับโมดูลไม่ช่วยอะไรเลย
    ในกรณีนั้น ตัวนี้เทียบลายนิ้วมือก่อน-หลังทั้งเซสชันแทน
    """
    before = {spec["label"]: _fingerprint(spec["real"]) for spec in _IRREPLACEABLE}
    yield
    changed = [
        f"{spec['label']}: {spec['real']} (ก่อน={before[spec['label']]} "
        f"หลัง={_fingerprint(spec['real'])})"
        for spec in _IRREPLACEABLE
        if _fingerprint(spec["real"]) != before[spec["label"]]
    ]
    assert not changed, (
        "ไฟล์ข้อมูลจริงของผู้ใช้เปลี่ยนไประหว่างรันชุดเทสต์ (ไฟล์เหล่านี้ถูก gitignore "
        "จึงไม่มีสำเนาใน git ให้กู้):\n"
        + "\n".join(f"  - {line}" for line in changed)
        + "\nถ้าไม่ใช่ฝีมือชุดเทสต์ ให้ดูโปรเซสอื่นที่เขียนโฟลเดอร์เดียวกันอยู่ "
        "(scheduler ในคอนเทนเนอร์ / เอเจนต์อีกตัวบน working tree เดียวกัน)"
    )


def redirect_user_data_paths(monkeypatch, sandbox: Path) -> dict[str, Path]:
    """ย้าย path ที่ยัง **ชี้ไฟล์จริงของผู้ใช้** ไปไว้ใต้ ``sandbox``; คืน map ที่ย้ายจริง.

    path ที่ถูกตั้งไว้ที่อื่นอยู่แล้ว (ผ่าน env หรือเทสต์ stub เอง) ไม่ถูกแตะ
    """
    sandbox.mkdir(parents=True, exist_ok=True)
    moved: dict[str, Path] = {}
    for spec in USER_DATA_FILES:
        module = importlib.import_module(spec["module"])
        current = Path(getattr(module, spec["attr"])).expanduser()
        real: Path = spec["real"]
        if current.resolve() != real.resolve():
            continue
        target = sandbox / real.name
        if spec["seed"] and real.exists():
            # copyfile ไม่ก็อป mtime/permission ของไฟล์จริงมาด้วย — อ่านอย่างเดียว
            shutil.copyfile(real, target)
        monkeypatch.setattr(module, spec["attr"], target)
        for attr in spec["mirror_parent"]:
            monkeypatch.setattr(module, attr, target.parent)
        for attr, value in spec["reset"].items():
            monkeypatch.setattr(module, attr, value)
        moved[spec["label"]] = target
    return moved


def assert_user_data_paths_are_isolated() -> None:
    """ฟ้องถ้าโมดูลไหนกำลังชี้ไฟล์ข้อมูลจริงของผู้ใช้อยู่ (ตรวจเฉพาะโมดูลที่ถูก import แล้ว)."""
    offenders = [
        f"{spec['module']}.{spec['attr']} = {spec['real']} ({spec['label']})"
        for spec in USER_DATA_FILES
        if (module := sys.modules.get(spec["module"])) is not None
        and Path(getattr(module, spec["attr"])).expanduser().resolve() == spec["real"].resolve()
    ]
    assert not offenders, (
        "เทสต์ชี้ path กลับไปที่ไฟล์ข้อมูลจริงของผู้ใช้ — การเรียกฟังก์ชันเซฟจะเขียนทับของจริงทันที:\n"
        + "\n".join(f"  - {line}" for line in offenders)
        + "\nให้ monkeypatch ไปที่ tmp_path เสมอ (ดู tests/test_data_file_isolation.py)"
    )


@pytest.fixture(autouse=True)
def _isolate_user_data_files(tmp_path_factory, monkeypatch):
    """กันทุกเทสต์ (และโพรบที่รันในคอนเทนเนอร์เทสต์) เขียนทับไฟล์ข้อมูลจริงของผู้ใช้.

    ทำงานแม้ลืมตั้ง ``VAULTIS_LEDGER_PATH`` / ``VAULTIS_ALERTS_PATH`` เพราะดูจาก
    **ค่าที่โมดูลถืออยู่จริง** ไม่ใช่จาก env  แล้วตรวจซ้ำหลังเทสต์จบเผื่อมีใคร setattr
    กลับไปที่ไฟล์จริงระหว่างทาง (ตอนนั้น monkeypatch ยังไม่ถูก undo — ลำดับ teardown
    ทำให้ fixture นี้ได้เห็นสถานะที่เทสต์ทิ้งไว้จริง ๆ)
    """
    redirect_user_data_paths(monkeypatch, Path(tmp_path_factory.mktemp("vaultis-user-data")))
    yield
    assert_user_data_paths_are_isolated()


import simulation.data as _sim_data_module

_REAL_FETCH_RAW = _sim_data_module.fetch_raw  # ตัวจริงก่อนถูกปิดด้วย autouse — ไว้เทสต์ตรรกะของมันเองด้วยข้อมูลสังเคราะห์


@pytest.fixture
def real_fetch_raw():
    """``simulation.data.fetch_raw`` ตัวจริง (ไม่ถูกปิด) — ใช้กับเทสต์ที่สตับชั้นล่าง (ราคา/FRED/ปันผล) เองทั้งหมด."""
    return _REAL_FETCH_RAW


@pytest.fixture(autouse=True)
def _no_live_erc_price_fetch(request, monkeypatch):
    """สัดส่วนฐานแบบ ERC (ค่าเริ่มต้นตั้งแต่ 2026-09-30) ดึงราคาจริงทุกครั้งที่คำนวณ.

    เทสต์ที่ไม่ได้ติด ``pytest.mark.network`` ห้ามไปถึงการดึงนั้น — ตอนเปลี่ยนเป็นค่าเริ่มต้น
    เทสต์ของสูตร preset ที่ไม่ได้ระบุ ``weighting_method`` ได้น้ำหนัก ERC จากตลาดจริงไปเทียบ
    และสองไฟล์ที่เรียก ``resolve_target_weights()`` ระดับโมดูลยิงเน็ตตั้งแต่ตอน collect
    ⇒ โยน AssertionError (ไม่ใช่ ValueError ที่ targets.py แปลงเป็น "ดึงราคาไม่ได้" แล้วกลืน)
    เทสต์ที่ต้องการ ERC ให้สตับ ``portfolio.risk_weights.compute_erc_weights`` เอง
    """
    if request.node.get_closest_marker("network"):
        yield
        return
    import portfolio.risk_weights as risk_weights

    def _blocked(tickers, sector_cap=False):
        raise AssertionError(
            f"เทสต์นี้ไปถึงการดึงราคาจริงของ ERC ({', '.join(tickers)}) — สตับ "
            "portfolio.risk_weights.compute_erc_weights หรือตั้ง weighting_method = preset"
        )

    monkeypatch.setattr(risk_weights, "compute_erc_weights", _blocked)

    # ข้อมูลสดของ simulation (ราคา + FRED) ก็เป็น network — ตัวตั้งเวลารันงานดึงข้อมูลตอนเริ่มโปรเซส
    # เทสต์ที่ไม่ได้ติด mark network ห้ามไปถึงการดึงนั้นเด็ดขาด (สตับ ``simulation.data`` เองถ้าต้องการข้อมูล)
    import simulation.data as sim_data

    def _sim_blocked(*_a, **_k):
        raise AssertionError(
            "เทสต์นี้ไปถึงการดึงข้อมูลสดของ simulation — ใช้ข้อมูลสังเคราะห์ (ดู tests/test_simulation_engine.py) "
            "หรือสตับ simulation.data.fetch_raw"
        )

    monkeypatch.setattr(sim_data, "fetch_raw", _sim_blocked)
    monkeypatch.setattr(sim_data, "_fetch_fred", _sim_blocked)

    # โหมดพอร์ตกระดาษ (STOCK / PREDICT) ดึงราคาเองทีละกองผ่าน yfinance — ตัวตั้งเวลารัน PREDICT ตอนเริ่มโปรเซสด้วย
    # ปล่อยให้เทสต์ส่ง ``fetch=`` ของตัวเองเข้าไปได้ แต่ **ห้ามใช้ตัวดึงจริงเป็นค่าเริ่มต้น** ในเทสต์ที่ไม่ติด network
    import analysis.predict_lab as predict_lab_mod
    import analysis.stock_pick as stock_pick_mod

    def _guard(real, label):
        def _wrapped(*args, fetch=None, **kwargs):
            if fetch is None:
                raise AssertionError(
                    f"เทสต์นี้ไปถึงการดึงราคาจริงของ {label} — ส่ง fetch= ของเทสต์เอง หรือสตับ fetch_prices"
                )
            return real(*args, fetch=fetch, **kwargs)

        return _wrapped

    monkeypatch.setattr(predict_lab_mod, "fetch_prices", _guard(predict_lab_mod.fetch_prices, "PREDICT"))
    monkeypatch.setattr(stock_pick_mod, "fetch_prices", _guard(stock_pick_mod.fetch_prices, "STOCK-DCA"))

    import analysis.trade_lab as trade_lab_mod

    monkeypatch.setattr(trade_lab_mod, "fetch_ohlc", _guard(trade_lab_mod.fetch_ohlc, "TRADE"))

    # ข้อมูลเซกเตอร์ของกอง (funds_data) ก็เป็น network เหมือนกัน — แผนรายเดือนเรียกมันเพื่อเตือน
    # ความกระจุกตัว ``_fund_data`` มีสัญญาว่า "ไม่ throw คืนเหตุผลแทน" จึงคืนเหตุผลตามสัญญา
    import portfolio.lookthrough as lookthrough

    monkeypatch.setattr(
        lookthrough, "_fund_data", lambda symbol: (None, None, "ออฟไลน์ในชุดเทสต์ — สตับ _fund_data เอง")
    )
    yield


@pytest.fixture
def fake_erc(monkeypatch):
    """ERC แบบออฟไลน์สำหรับเทสต์ที่แค่ต้องให้เส้นทางจริง (ค่าเริ่มต้น ERC) เดินได้.

    น้ำหนักเท่ากันทุกกอง + meta ว่าง — ไม่ใช่ตัวเลขของตลาดจริง เทสต์ที่ตรวจตัวเลข ERC
    ต้องสตับเอง (ดู tests/test_erc_weights.py) · คืนลิสต์ tickers ที่ถูกขอคำนวณ
    """
    import portfolio.risk_weights as risk_weights

    calls: list[tuple[str, ...]] = []

    def _equal(tickers, sector_cap=False):
        calls.append(tuple(tickers))
        n = len(tickers)
        return {
            "weights": {t: 1.0 / n for t in tickers},
            "risk_share": {t: 1.0 / n for t in tickers},
            "meta": {},
        }

    monkeypatch.setattr(risk_weights, "compute_erc_weights", _equal)
    return calls


@pytest.fixture(autouse=True)
def _isolate_ttl_caches():
    """ล้าง TTL cache ทุกตัวก่อน-หลังทุกเทสต์ — กันผลลัพธ์รั่วข้ามเคส.

    ต้องล้าง ``backend.services.cache_service.shared_cache`` ด้วย เพราะเป็น global
    ระดับ module (etf_service / market_analysis_service ใช้ร่วมกัน) ถ้าล้างแค่
    ``utils.cache`` ราคาที่เคสหนึ่ง stub ไว้จะค้างไปโผล่ในไฟล์เทสต์อื่น
    """
    clear_all_caches()
    shared_cache.clear()
    yield
    clear_all_caches()
    shared_cache.clear()
