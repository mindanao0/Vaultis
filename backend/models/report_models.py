from datetime import datetime

from pydantic import BaseModel


class MonthlyReportRead(BaseModel):
    id: int
    month: str
    content: str
    sent_at: datetime

    class Config:
        from_attributes = True
