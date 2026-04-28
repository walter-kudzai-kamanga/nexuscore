# Security and Migration Setup

## 1) Secrets

Copy the example secrets and provide real values:

```bash
mkdir -p .secrets
cp .secrets.example/* .secrets/
```

Required files:

- `.secrets/nexus_secret_key.txt`
- `.secrets/nexus_service_api_key.txt`
- `.secrets/nexus_bootstrap_users_json.json`

## 2) Run DB Migrations

Install dependencies and run Alembic:

```bash
pip install -r requirements.txt
alembic upgrade head
```

The migration uses `DATABASE_URL` if set, otherwise `alembic.ini` fallback.

Automatic startup migrations are available with safe gating:

- `NEXUS_AUTO_MIGRATE=true`
- `NEXUS_AUTO_MIGRATE_WITH_LOCK=true` (Postgres advisory lock)
- `NEXUS_AUTO_MIGRATE_ALLOW_SQLITE=true` (only for local/dev sqlite)

## 3) Start Stack

```bash
docker compose up --build
```

## 4) Auth Flow Notes

- `POST /api/auth/login` sets HttpOnly cookies.
- Protected API routes now accept either bearer token or cookie-backed auth.
- `GET /api/events/stream` uses cookie/session auth (no query token).
- Cookie-authenticated mutating endpoints require CSRF header:
  - read `nexus_csrf_token` cookie value
  - send as `x-csrf-token` header

