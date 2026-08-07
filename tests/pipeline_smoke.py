"""สคริปต์ smoke ที่รันด้วยมือ: ETF scores → macro snapshot → Claude AI advice.

**ไม่ใช่ไฟล์เทสต์** และตั้งใจไม่ให้ชื่อขึ้นต้นด้วย ``test_``

* ``get_ai_advice()`` เรียก LLM จริง = **เสียเงินจริงทุกครั้งที่รัน** จึงต้องไม่มีวัน
  ถูก pytest หยิบไปรันเอง
* เดิมไฟล์นี้ชื่อ ``tests/test_pipeline.py`` แต่ฟังก์ชันเดียวในไฟล์ชื่อ ``main()``
  pytest จึงเก็บเทสต์ได้ 0 ตัว — ดูเหมือนมีเทสต์คลุมอยู่ทั้งที่ไม่เคยถูกรันเลย
  (AUDIT_2026-08-06 ข้อ 0-B) ตาข่ายกันพลาด: ``tests/test_offline_collection.py``

รันด้วยมือเมื่อจงใจจะจ่ายค่า LLM::

    python tests/pipeline_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from analysis.ai_advisor import get_ai_advice
from analysis.financial_model import build_etf_scores
from analysis.macro import get_macro_snapshot


def main() -> None:
    etf_scores = build_etf_scores(["VOO", "SCHD"])
    print("=== build_etf_scores(['VOO', 'SCHD']) ===")
    print(etf_scores)

    macro = get_macro_snapshot()
    print("\n=== get_macro_snapshot() ===")
    print(macro)

    advice = get_ai_advice(etf_scores, macro)
    print("\n=== get_ai_advice (first 200 chars) ===")
    print(advice[:200] + ("..." if len(advice) > 200 else ""))

    assert etf_scores is not None, "etf_scores must not be None"
    assert macro is not None, "macro must not be None"
    assert advice is not None, "advice must not be None"
    assert isinstance(etf_scores, list) and len(etf_scores) > 0
    assert isinstance(macro, dict)
    assert isinstance(advice, str) and len(advice.strip()) > 0

    print("\nAssertions passed.")


if __name__ == "__main__":
    main()
