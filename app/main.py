# app/main.py
from typing import List, Optional
import os
import time
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import models, schemas, database, events

# config / env
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/payment_db")
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@rabbitmq/")

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("payment-service")

app = FastAPI(title="Payment Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup: initialize DB and start the consumer that listens to enrollment events
@app.on_event("startup")
def startup():
    logger.info("Initializing DB and starting enrollment-event consumer...")
    database.init_db(DATABASE_URL)
    events.start_consumer(DATABASE_URL, RABBITMQ_URL, os.getenv("PAYMENT_QUEUE", "payment_queue"))
    logger.info("Startup complete.")

def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Root and health endpoints
@app.get("/")
def root():
    return {"service": "Payment Service", "status": "running", "endpoints": ["/payments", "/docs", "/openapi.json"]}

@app.get("/health")
def health():
    try:
        db = database.SessionLocal()
        db.execute("SELECT 1")
        db.close()
    except Exception as e:
        logger.exception("Health check failed: %s", e)
        raise HTTPException(status_code=503, detail="Database unreachable")
    return {"status": "ok"}

# Create a payment (simulate processing asynchronously)
@app.post("/payments", response_model=schemas.PaymentOut, status_code=201)
def initiate_payment(payment_in: schemas.PaymentCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    payment = models.Payment(
        student_id=payment_in.student_id,
        enrollment_id=payment_in.enrollment_id,
        amount=payment_in.amount,
        status="PENDING"
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    # simulate calling external gateway asynchronously
    background_tasks.add_task(
        events.process_payment_and_publish,
        os.getenv("RABBITMQ_URL", RABBITMQ_URL),
        payment.id,
        payment.student_id,
        payment.enrollment_id,
        payment.amount
    )

    logger.info("Created payment id=%s student=%s enrollment=%s status=PENDING", payment.id, payment.student_id, payment.enrollment_id)
    return schemas.PaymentOut.from_orm(payment)

# Get payment by id
@app.get("/payments/{payment_id}", response_model=schemas.PaymentOut)
def get_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return schemas.PaymentOut.from_orm(payment)

# Refund a payment (mark REFUNDED and publish event)
@app.post("/payments/refund/{payment_id}", response_model=schemas.PaymentOut)
def refund_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment.status = "REFUNDED"
    db.commit()
    db.refresh(payment)

    event = {
        "type": "PaymentRefunded",
        "payload": {"payment_id": payment.id, "enrollment_id": payment.enrollment_id, "student_id": payment.student_id}
    }
    events.publish_event(os.getenv("RABBITMQ_URL", RABBITMQ_URL), "payment.events.refund", event)
    logger.info("Refunded payment id=%s", payment_id)
    return schemas.PaymentOut.from_orm(payment)

# Admin approve: mark payment SUCCESS and publish PaymentConfirmed event
@app.post("/payments/{payment_id}/approve", response_model=schemas.PaymentOut)
def approve_payment(payment_id: int, db: Session = Depends(get_db)):
    payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment.status = "SUCCESS"
    payment.transaction_ref = f"tx-manual-{payment_id}-{int(time.time())}"
    db.commit()
    db.refresh(payment)

    event = {
        "type": "PaymentConfirmed",
        "payload": {"payment_id": payment.id, "enrollment_id": payment.enrollment_id, "student_id": payment.student_id}
    }
    events.publish_event(os.getenv("RABBITMQ_URL", RABBITMQ_URL), "payment.events.confirmed", event)
    logger.info("Approved payment id=%s and published PaymentConfirmed", payment_id)
    return schemas.PaymentOut.from_orm(payment)

# List payments with optional filters (status, student_id)
@app.get("/payments", response_model=List[schemas.PaymentOut])
def list_payments(status: Optional[str] = Query(None), student_id: Optional[str] = Query(None), db: Session = Depends(get_db)):
    q = db.query(models.Payment)
    if status:
        q = q.filter(models.Payment.status == status.upper())
    if student_id:
        q = q.filter(models.Payment.student_id == student_id)
    results = q.order_by(models.Payment.created_at.desc()).all()
    return [schemas.PaymentOut.from_orm(p) for p in results]