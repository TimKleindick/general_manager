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
./k6/run_baseline.sh
./k6/run_mix.sh
./k6/run_heavy_calc.sh
./k6/run_stress.sh
./k6/run_spike.sh
./k6/run_soak.sh
./k6/run_queries_only.sh
./k6/run_subscriptions_only.sh
./k6/run_mix_scaled.sh
./k6/run_scale_suite.sh
```

Optional k6 tuning:

```bash
# heavy calculations pack
HEAVY_CALC=true HEAVY_RATE=0.1 HEAVY_PAGE_SIZE=3 ./k6/run_mix.sh

# fixed RNG seed for repeatability
K6_SEED=1337 ./k6/run_mix.sh

# scale suite (override defaults)
SCALE_LEVELS="1 2 4 6" RATE_1=30 RATE_2=50 RATE_4=70 RATE_6=90 DURATION=10m ./k6/run_scale_suite.sh
```

Run metadata capture (repeatability):

```bash
RUN_LABEL="baseline" ./k6/record_run.sh
```

Capture run metadata + JSON output (k6):

```bash
RUN_LABEL="baseline" ./k6/run_capture.sh ./k6/run_mix.sh
```

Capture metadata + JSON output for scale suite:

```bash
RUN_LABEL="scale-suite" ./k6/run_scale_capture.sh
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
