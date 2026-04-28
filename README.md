# NexusCore

NexusCore is a self-healing microservice reliability platform with:

- service heartbeat and health monitoring
- anomaly detection and guarded autonomous healing
- Kafka event pipeline for control and audit signals
- tenant-aware SaaS control plane (connectors, policies, billing, compliance)
- live dashboard with SSE updates

This guide shows how to run the full stack locally and in development.

## 1) Prerequisites

- Linux/macOS with Python 3.11+ (3.12 works)
- Docker Engine
- Docker Compose support:
  - preferred: `docker compose` (v2 plugin)
  - fallback: `docker-compose` (v1 binary)

### If Compose is missing

Ubuntu:

```bash
sudo apt update
sudo apt install docker-compose-v2
```

Fallback:

```bash
sudo apt install docker-compose
```

## 2) Clone and prepare environment

From project root:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## 3) Configure secrets

Create local secret files:

```bash
mkdir -p .secrets
cp .secrets.example/* .secrets/
```

Update these files with real values:

- `.secrets/nexus_secret_key.txt`
- `.secrets/nexus_service_api_key.txt`
- `.secrets/nexus_bootstrap_users_json.json`

## 4) Run the full stack (recommended)

### Compose v2

```bash
docker compose up --build
```

### Compose v1 fallback

```bash
docker-compose up --build
```

This starts:

- Zookeeper
- Kafka
- PostgreSQL
- NexusCore API

Default app URL: `http://127.0.0.1:8000`

## 5) Run API without Docker (advanced/dev)

If you run FastAPI directly, you must provide required secrets and Kafka/Postgres connectivity.

```bash
export NEXUS_SECRET_KEY_FILE="$(pwd)/.secrets/nexus_secret_key.txt"
export NEXUS_SERVICE_API_KEY_FILE="$(pwd)/.secrets/nexus_service_api_key.txt"
export NEXUS_BOOTSTRAP_USERS_JSON_FILE="$(pwd)/.secrets/nexus_bootstrap_users_json.json"
export NEXUS_COOKIE_SECURE=false
export KAFKA_BOOTSTRAP_SERVERS=localhost:9092
export DATABASE_URL=postgresql+psycopg2://nexus:nexus@localhost:5432/nexuscore
uvicorn app.main:app --reload --port 2000
```

If startup fails with Kafka bootstrap errors, start Kafka first via Docker.

## 6) Core endpoints

- Dashboard page: `GET /`
- Public dashboard snapshot: `GET /api/dashboard/public/state`
- Public dashboard SSE: `GET /api/events/public/stream`
- Service heartbeat ingest: `POST /api/services/heartbeat` (API key protected)
- SaaS control plane: `/api/saas/*`

## 7) Authentication model

Admin/operator/developer app auth:

- `POST /api/auth/login`
- sets HttpOnly auth cookies
- mutating cookie-authenticated routes require CSRF header:
  - read `nexus_csrf_token` cookie
  - send value as `x-csrf-token`

Tenant runtime auth:

- tenant API key boundary (`x-tenant-id`, `x-tenant-api-key`) or
- tenant JWT boundary (`tenant_access` tokens)

## 8) Automatic migrations and startup behavior

Startup migration behavior is env-controlled:

- `NEXUS_AUTO_MIGRATE=true`
- `NEXUS_AUTO_MIGRATE_WITH_LOCK=true` (Postgres advisory lock safety)
- `NEXUS_AUTO_MIGRATE_ALLOW_SQLITE=true` (dev sqlite only)

Compose already enables auto-migrate for the containerized stack.

