# NexusCore Testing Guide

This document provides practical local testing flows for NexusCore.

## 1) Bring up test environment

Use the full stack for realistic tests:

```bash
docker compose up --build
```

If your machine uses Compose v1:

```bash
docker-compose up --build
```

## 2) Baseline health checks

```bash
curl -s http://127.0.0.1:8000/api/dashboard/public/state | python3 -m json.tool
curl -s http://127.0.0.1:8000/api/platform/kafka/metrics
```

Open dashboard:

- `http://127.0.0.1:8000/`

## 3) Login and cookie/CSRF setup

```bash
curl -i -c cookies.txt -X POST http://127.0.0.1:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"replace-admin-password"}'
```

Extract CSRF value:

```bash
CSRF=$(awk '$6=="nexus_csrf_token"{print $7}' cookies.txt | tail -n 1)
echo "$CSRF"
```

## 4) Service monitoring + healing test

Register and send heartbeats with service API key from `.secrets/nexus_service_api_key.txt`.

```bash
SERVICE_KEY=$(cat .secrets/nexus_service_api_key.txt)

curl -s -X POST http://127.0.0.1:8000/api/services/register \
  -H "x-api-key: $SERVICE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"service":"demo-worker","status":"online","cpu":20,"memory":22,"latency":15,"error_rate":0.1,"metadata":{"tenant_id":"default"}}'
```

Trigger anomaly:

```bash
curl -s -X POST http://127.0.0.1:8000/api/services/heartbeat \
  -H "x-api-key: $SERVICE_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"service":"demo-worker","status":"online","cpu":97,"memory":95,"latency":900,"error_rate":18,"metadata":{"tenant_id":"default"}}'
```

Verify healing/audit:

```bash
curl -s -b cookies.txt http://127.0.0.1:8000/api/healing/history
curl -s -b cookies.txt http://127.0.0.1:8000/api/alerts
```

## 5) SaaS control plane test

Create tenant:

```bash
curl -s -b cookies.txt -X POST http://127.0.0.1:8000/api/saas/tenants \
  -H "x-csrf-token: $CSRF" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"acme","name":"Acme Corp","plan":"starter"}'
```

Register connector:

```bash
curl -s -b cookies.txt -X POST http://127.0.0.1:8000/api/saas/connectors/register \
  -H "x-csrf-token: $CSRF" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"acme","name":"orders-api","connector_type":"webhook","config":{"endpoint":"https://httpbin.org/post","method":"POST"}}'
```

Issue tenant token:

```bash
curl -s -b cookies.txt -X POST http://127.0.0.1:8000/api/saas/tenant/token \
  -H "x-csrf-token: $CSRF" \
  -H 'Content-Type: application/json' \
  -d '{"tenant_id":"acme","scopes":["connector:execute","transactions:heal"]}'
```

## 6) Transaction healing worker test

Queue a generic healing workflow (tenant auth):

```bash
TENANT_TOKEN="<tenant_access_token_from_previous_step>"

curl -s -X POST http://127.0.0.1:8000/api/saas/tenant/transactions/heal \
  -H "Authorization: Bearer $TENANT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"workflow_type":"failed_transaction_replay","reference_id":"txn-123","payload":{"idempotency_key":"abc-123"}}'
```

Wait a few seconds, then check compliance/billing:

```bash
curl -s -b cookies.txt http://127.0.0.1:8000/api/saas/compliance/report/acme
curl -s -b cookies.txt http://127.0.0.1:8000/api/saas/billing/acme
```

## 7) Evidence chain test

```bash
curl -s -b cookies.txt "http://127.0.0.1:8000/api/saas/compliance/evidence/acme?limit=100"
```

Confirm `"chain_valid": true`.

