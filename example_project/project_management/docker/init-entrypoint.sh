#!/usr/bin/env sh
set -eu

python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse


def wait_for(host: str, port: int, name: str, timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {name} at {host}:{port}")


postgres_host = os.environ.get("POSTGRES_HOST")
if postgres_host:
    postgres_port = int(os.environ.get("POSTGRES_PORT", "5432"))
    wait_for(postgres_host, postgres_port, "Postgres")

redis_url = os.environ.get("REDIS_URL")
if redis_url:
    parsed = urlparse(redis_url)
    host = parsed.hostname or "redis"
    port = parsed.port or 6379
    wait_for(host, port, "Redis")

meili_url = os.environ.get("MEILISEARCH_URL")
if meili_url:
    parsed = urlparse(meili_url)
    host = parsed.hostname or "meilisearch"
    port = parsed.port or 7700
    wait_for(host, port, "Meilisearch")
PY

python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py search_index --reindex

if [ "${PM_SEED_ON_START:-false}" = "true" ]; then
  python manage.py generate_test_data
fi
