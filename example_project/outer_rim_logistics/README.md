# Outer Rim Logistics

Outer Rim Logistics is a Star Wars-inspired sample project that showcases
GeneralManager features across crew operations, supply chains, and mission
readiness analytics.

## Development (devcontainer)

The devcontainer uses SQLite plus the in-memory DevSearch backend.

```bash
python manage.py migrate
python manage.py seed_outer_rim
daphne -b 0.0.0.0 -p 8000 orl.asgi:application
```

GraphQL: `http://localhost:8000/graphql/`

## Docker Compose (production-style)

Docker Compose uses Postgres, Meilisearch, Prometheus, Grafana, and an Nginx
reverse proxy for load balancing and static assets. Loki + Promtail provide
centralized log collection.

```bash
cp .env.example .env
# create TLS certs + secrets (see docker/certs/README.md and docker/secrets/README.md)
docker compose up --build
```

Services:
- App (via Nginx): `https://localhost:8443/graphql/`
- Metrics: `https://localhost:8443/metrics/`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (default login: `admin` / `admin`)
- Loki (log storage): `http://localhost:3100`

Logs are written to `example_project/outer_rim_logistics/logs/`.

Scale web workers:

```bash
docker compose up --build --scale web=2
```

Celery:
- Start the worker with `celery-worker` service (included in compose).
- Set `GM_SEARCH_ASYNC=true` to dispatch search indexing via Celery.

Logging:
- Application and Nginx logs are written to `example_project/outer_rim_logistics/logs/`.
- Promtail ships those logs to Loki; Grafana includes a Loki datasource.
