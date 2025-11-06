# Payment Service (University Management System)

This repository contains a starter **Payment Service** implemented with **FastAPI** that demonstrates:
- REST endpoints to initiate payments and query status
- Persistence with PostgreSQL (SQLAlchemy)
- Event consumer for `RegistrationPendingPayment` events and publishers for `PaymentConfirmed` / `PaymentFailed`
- Simple simulated payment processing logic

### Repo Structure

```
/app
  ├─ main.py
  ├─ models.py
  ├─ schemas.py
  ├─ database.py
  └─ events.py
Dockerfile
requirements.txt
/manifests
  ├─ deployment.yaml
  ├─ configmap.yaml
  └─ hpa.yaml
README.md
.gitignore
payment_service.drawio
```

### Build and Push docker image

`docker build . -t dtummidibits/ums-payment-service:1.0`

`docker push dtummidibits/ums-payment-service:1.0`

- You can run this alongside the Enrollment service & RabbitMQ/Postgres using docker-compose or in Kubernetes.
- Payment service looks for all `PENDING` transactions and allow admins to approve them manually to make them `SUCCESS`