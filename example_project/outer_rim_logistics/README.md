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
# create secrets (see docker/secrets/README.md)
# TLS certs are auto-generated for localhost if missing
docker compose up --build
```

The application build uses the repository-root `.dockerignore`; the supporting
service builds use the `.dockerignore` in this directory. Runtime secret files,
local environment files, certificates, logs, and backups are excluded from the
build contexts. Application images copy only package files and entrypoint
scripts. Keep credentials in the runtime secret mounts.

If you previously built images after creating real secret files, treat those
credentials as exposed to anyone with access to the images or their layers.
Rotate the affected credentials, rebuild with `docker compose build --no-cache`,
and remove the affected images and cached layers from builders and registries
under your control. Rebuilding does not remove credentials from older images.

Run the build-context regression checks from the repository root:

```bash
python tests/packaging/test_docker_build_context.py -v
```

Docker is required for the two synthetic-secret image-layer checks. Without it,
those checks are skipped and only the static COPY boundary checks run.

Services (all via Nginx):
- App: `https://localhost:${ORL_HTTPS_PORT}/graphql/`
- Metrics: `https://localhost:${ORL_HTTPS_PORT}/metrics/`
- Prometheus: `https://localhost:${ORL_HTTPS_PORT}/prometheus/` (protected by Nginx basic auth)
- Grafana: `https://localhost:${ORL_HTTPS_PORT}/grafana/` (Grafana login; default login: `admin` / `admin`)
- Grafana dashboards are provisioned from `docker/grafana/provisioning/dashboards`.
- Prometheus scrapes app metrics, Meilisearch, Postgres exporter, Redis exporter, Nginx exporter, and Celery exporter.

Nginx basic auth uses `docker/htpasswd/observability`. A template exists at
`docker/htpasswd/observability.example` (default user: `orl_admin`, password:
`changeme`).
- Loki: `https://localhost:${ORL_HTTPS_PORT}/loki/`

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
