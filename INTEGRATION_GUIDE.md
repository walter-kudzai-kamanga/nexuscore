# NexusCore Integration Guide (Any Software)

This guide explains how to connect NexusCore to external systems so it can
monitor, detect anomalies, and run self-healing actions with minimal manual intervention.

The platform is domain-agnostic (banking, e-commerce, logistics, SaaS, gaming, etc.).

## 1) Integration patterns

Use one or more of these:

- **Heartbeat push**: external software posts periodic health metrics.
- **Connector runtime**: NexusCore executes outbound actions into your systems.
- **Tenant workflow API**: software queues healing workflows directly.
- **Kafka event integration**: software publishes/subscribes to shared topics.

## 2) Tenant onboarding

1. Login as admin/operator.
2. Create tenant in control plane:
   - `POST /api/saas/tenants`
3. Store returned tenant API key in your secret manager.
4. (Optional) issue tenant runtime JWT:
   - `POST /api/saas/tenant/token`

## 3) Register connectors (outbound healing targets)

Register one connector per target system interface:

- REST service endpoint
- Webhook endpoint
- gRPC target

Endpoint:

- `POST /api/saas/connectors/register`

Suggested naming convention:

- `<domain>-<system>-<operation>`
- e.g. `orders-core-replay`, `inventory-sync-reconcile`

## 4) Push monitoring data from your software

Each service should emit heartbeat with tenant metadata:

- `POST /api/services/heartbeat`
- header: `x-api-key: <global_service_api_key>`
- payload includes:
  - `service`
  - `cpu`, `memory`, `latency`, `error_rate`
  - `metadata.tenant_id`

Example payload:

```json
{
  "service": "order-processor",
  "status": "online",
  "cpu": 34,
  "memory": 62,
  "latency": 180,
  "error_rate": 1.2,
  "metadata": {
    "tenant_id": "acme",
    "env": "prod",
    "component": "order_pipeline"
  }
}
```

## 5) Define guarded healing policy

Use tenant policy DSL:

- `POST /api/saas/policies/upsert`

Include:

- autonomy mode (`manual`, `guarded`, `auto`)
- denied actions by severity
- actions requiring approval
- action rate limits

This ensures safety before automatic execution.

## 6) Execute runtime actions from your platform

Two auth modes:

- Tenant API key headers:
  - `x-tenant-id`
  - `x-tenant-api-key`
- Tenant JWT:
  - `Authorization: Bearer <tenant_access_token>`

Runtime execution endpoint:

- `POST /api/saas/tenant/runtime/execute`

Use this to trigger operational actions in external systems via registered connectors.

## 7) Queue generic healing workflows

Queue workflows from external software:

- `POST /api/saas/tenant/transactions/heal`

Fields:

- `workflow_type` (custom/generic identifier)
- `reference_id` (job/transaction/task ID)
- `payload` (idempotency key, context, remediation params)

NexusCore worker auto-runs queued tasks and records results.

## 8) Observe outcomes and compliance

- Live dashboard:
  - `GET /api/dashboard/public/state`
  - `GET /api/events/public/stream`
- Compliance report:
  - `GET /api/saas/compliance/report/{tenant_id}`
- Evidence export:
  - `GET /api/saas/compliance/evidence/{tenant_id}`
- Billing/usage:
  - `GET /api/saas/billing/{tenant_id}`

## 9) Recommended integration architecture

- **Per service**:
  - heartbeat emitter module (5-15s interval)
  - retry with jitter and idempotency key
- **Per domain**:
  - connector definitions for rollback/replay/reconcile actions
- **Per tenant**:
  - dedicated API key/JWT scope
  - tenant-specific policies and quotas
- **Security**:
  - store all keys/tokens in external secret manager
  - rotate tenant API keys periodically

## 10) Production hardening checklist

- enable TLS and set `NEXUS_COOKIE_SECURE=true`
- isolate tenant traffic and credentials
- define connector timeout/retry/backoff budgets
- set strict policy guardrails before enabling `auto`
- monitor lag, failed workflows, and evidence chain validity

