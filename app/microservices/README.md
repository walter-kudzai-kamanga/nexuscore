# Default Microservice Templates

Use `app/templates/microservice_template.py` with these environment values:

- `SERVICE_NAME=auth-service`
- `SERVICE_NAME=payments-service`
- `SERVICE_NAME=transactions-service`
- `SERVICE_NAME=notification-service`
- `SERVICE_NAME=user-service`

Each service exposes:

- `/health` endpoint
- `/kafka-event` event payload endpoint
- auto-registration on startup
- heartbeat emission with API key auth
- self-healing metadata protocol `self-healing-v1`

