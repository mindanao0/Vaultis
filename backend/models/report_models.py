from datetime import datetime

from pydantic import BaseModel


class MonthlyReportRead(BaseModel):
    id: int
    month: str
    content: str
    sent_at: datetime
    # ``ai`` = LLM เขียนบทสรุป · ``plain`` = ตัวเลขจากโมเดลล้วน (ไม่มีค่าใช้จ่าย)
    # แถวเก่าที่บันทึกไว้ก่อนมีคอลัมน์นี้จะถูกเติมเป็น ``plain`` — ไม่อ้างว่าเป็น ai
    source: str = "plain"

    class Config:
        from_attributes = True
