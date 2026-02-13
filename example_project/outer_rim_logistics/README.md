# Outer Rim Logistics

Outer Rim Logistics is a Star Wars-inspired sample project that showcases
GeneralManager features across crew operations, supply chains, and mission
readiness analytics.

## Development (devcontainer)

The devcontainer uses SQLite plus the in-memory DevSearch backend.

```bash
python manage.py migrate
python manage.py seed_outer_rim
python manage.py bulk_seed_outer_rim
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

Bulk seed data for load tests (defaults target ~1k ships, ~3k modules, ~8k crew, ~8k inventory):

```bash
docker compose exec web python manage.py bulk_seed_outer_rim --ships 1000 --modules 3000 --crew 8000 --inventory 8000 --work-orders 4000 --incidents 2000 --schedules 800
```

Temporarily relax GraphQL rate limits for load tests:

```bash
GRAPHQL_LIMIT_RATE=1000r/s GRAPHQL_LIMIT_BURST=1000 docker compose up --build
```

Load testing (k6) via docker-compose profile:

```bash
./k6/run_queries_only.sh
./k6/run_queries_mutations_only.sh
./k6/run_mix.sh
./k6/run_heavy_calc.sh
```

Optional k6 tuning:

```bash
# 90/10 mix with subscriptions (default for run_mix.sh)
READ_WEIGHT=90 WRITE_WEIGHT=10 RUN_SUBSCRIPTIONS=true ./k6/run_mix.sh

# heavy read with writes to trigger invalidation
HEAVY_RATE=1.0 HEAVY_PAGE_SIZE=3 READ_WEIGHT=80 WRITE_WEIGHT=20 ./k6/run_heavy_calc.sh

# fixed RNG seed for repeatability
K6_SEED=1337 ./k6/run_mix.sh
```

Run metadata capture (repeatability):

```bash
RUN_LABEL="baseline" ./k6/record_run.sh
```

Capture run metadata + JSON output (k6):

```bash
RUN_LABEL="baseline" ./k6/run_capture.sh ./k6/run_mix.sh
```

Capture k6 mix plus GM pre/post overhead profiling (CSV artifacts):

```bash
RUN_LABEL="mix-gm" GM_PROFILE_ITERS=120 ./k6/run_mix_with_gm_profile.sh
```

Results are stored in `k6/results/` along with a short README.

Services (all via Nginx):
- App: `https://localhost:${ORL_HTTPS_PORT}/graphql/`
- Metrics: `https://localhost:${ORL_HTTPS_PORT}/metrics/`
- Prometheus: `https://localhost:${ORL_HTTPS_PORT}/prometheus/` (protected by Nginx basic auth)
- Grafana: `https://localhost:${ORL_HTTPS_PORT}/grafana/` (Grafana login; default login: `admin` / `admin`)
- Grafana dashboards are provisioned from `docker/grafana/provisioning/dashboards`.
- The `ORL k6` dashboard appears once k6 is configured to remote-write to Prometheus.
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
