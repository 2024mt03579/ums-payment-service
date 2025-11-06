import pika
import json
import threading
import time
import os
import traceback
from app import models, database
from sqlalchemy.orm import Session

EXCHANGE = "ums_events"

def publish_event(rabbitmq_url: str, routing_key: str, event: dict):
    try:
        params = pika.URLParameters(rabbitmq_url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)
        body = json.dumps(event)
        channel.basic_publish(exchange=EXCHANGE, routing_key=routing_key, body=body)
        connection.close()
    except Exception as e:
        print("Error publishing event:", e)
        traceback.print_exc()

def process_payment_and_publish(rabbitmq_url: str, payment_id: int, student_id: str, enrollment_id: int, amount: float):
    """
    Simulate contacting a payment gateway and publish PaymentConfirmed/Failed.
    Even amounts succeed in this demo; odd amounts fail.
    """
    try:
        time.sleep(1)
        success = int(amount) % 2 == 0

        database.init_db(os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/payment_db"))
        db = database.SessionLocal()
        try:
            payment = db.query(models.Payment).filter(models.Payment.id == payment_id).first()
            if not payment:
                print("Payment not found in process_payment_and_publish:", payment_id)
                return

            if success:
                payment.status = "SUCCESS"
                payment.transaction_ref = f"tx-{payment_id}-{int(time.time())}"
                db.commit()
                db.refresh(payment)
                event = {"type": "PaymentConfirmed", "payload": {"payment_id": payment.id, "enrollment_id": enrollment_id, "student_id": student_id}}
                publish_event(rabbitmq_url, "payment.events.confirmed", event)
                print(f"Payment {payment_id} SUCCESS, published PaymentConfirmed.")
            else:
                payment.status = "FAILED"
                db.commit()
                db.refresh(payment)
                event = {"type": "PaymentFailed", "payload": {"payment_id": payment.id, "enrollment_id": enrollment_id, "student_id": student_id}}
                publish_event(rabbitmq_url, "payment.events.failed", event)
                print(f"Payment {payment_id} FAILED, published PaymentFailed.")
        finally:
            db.close()
    except Exception as e:
        print("Error in process_payment_and_publish:", e)
        traceback.print_exc()

def _process_registration_event(body: dict, db: Session):
    """
    Called when Enrollment publishes RegistrationPendingPayment.
    Creates a PENDING payment record and DOES NOT auto-process it.
    Admin must call /payments/{id}/approve to confirm.
    """
    payload = body.get("payload", {})
    enrollment_id = payload.get("enrollment_id")
    student_id = payload.get("student_id")
    amount = payload.get("amount", 0.0)

    payment = models.Payment(student_id=student_id, enrollment_id=enrollment_id, amount=amount, status="PENDING")
    db.add(payment)
    db.commit()
    db.refresh(payment)

    print(f"Created PENDING payment id={payment.id} for enrollment={enrollment_id} student={student_id}")


def _consumer_runloop(database_url: str, rabbitmq_url: str, queue_name: str = ""):
    """
    Persistent consumer loop: connects, declares exchange & queue, binds and consumes.
    Reconnects on errors with backoff.
    """
    database.init_db(database_url)

    while True:
        conn = None
        try:
            params = pika.URLParameters(rabbitmq_url)
            conn = pika.BlockingConnection(params)
            ch = conn.channel()
            ch.exchange_declare(exchange=EXCHANGE, exchange_type="topic", durable=True)

            if queue_name:
                q = ch.queue_declare(queue=queue_name, durable=False, exclusive=False)
                actual_queue = queue_name
            else:
                q = ch.queue_declare(queue="", exclusive=True)
                actual_queue = q.method.queue

            ch.queue_bind(exchange=EXCHANGE, queue=actual_queue, routing_key="enrollment.events.#")
            print(f"Payment consumer bound queue={actual_queue} to {EXCHANGE} with key=enrollment.events.#")

            def callback(ch, method, properties, body):
                try:
                    payload = json.loads(body)
                    print("Payment consumer received message:", payload)
                    db = database.SessionLocal()
                    try:
                        _process_registration_event(payload, db)
                    finally:
                        db.close()
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    print("Message processed and acked:", method.delivery_tag)
                except Exception as exc:
                    print("Error processing message:", exc)
                    traceback.print_exc()
                    try:
                        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                    except Exception:
                        pass

            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(queue=actual_queue, on_message_callback=callback, auto_ack=False)
            print("Payment consumer starting to consume on queue:", actual_queue)
            ch.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            print("AMQP connection error in consumer:", e)
        except Exception as e:
            print("Unexpected exception in consumer loop:", e)
            traceback.print_exc()
        finally:
            try:
                if conn and conn.is_open:
                    conn.close()
            except Exception:
                pass

        print("Payment consumer will reconnect after backoff...")
        time.sleep(3)

_consumer = None
def start_consumer(database_url: str, rabbitmq_url: str, queue_name: str = ""):
    global _consumer
    if _consumer is None:
        _consumer = threading.Thread(target=_consumer_runloop, args=(database_url, rabbitmq_url, queue_name), daemon=True)
        _consumer.start()