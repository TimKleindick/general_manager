# Outer Rim Logistics

Outer Rim Logistics is a Star Wars-inspired sample project that showcases
GeneralManager features across crew operations, supply chains, and mission
readiness analytics.

## Development (devcontainer)

The devcontainer uses SQLite plus the in-memory DevSearch backend.

```bash
python manage.py migrate
python manage.py seed_outer_rim
python manage.py runserver
```

GraphQL: `http://localhost:8000/graphql/`

## Docker Compose (production-style)

Docker Compose uses Postgres, Meilisearch, Prometheus, and Grafana.

```bash
cp .env.example .env
docker compose up --build
```

Services:
- App: `http://localhost:8000/graphql/`
- Metrics: `http://localhost:8000/metrics/`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (default login: `admin` / `admin`)

Logs are written to `example_project/outer_rim_logistics/logs/`.
