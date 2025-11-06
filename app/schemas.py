from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class PaymentCreate(BaseModel):
    student_id: str
    enrollment_id: int
    amount: float

class PaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    student_id: str
    enrollment_id: int
    amount: float
    status: str
    transaction_ref: Optional[str] = None
    created_at: Optional[datetime] = None