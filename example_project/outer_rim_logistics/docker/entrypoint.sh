#!/usr/bin/env bash
set -euo pipefail

python - <<'PY'
import os
import socket
import time
from urllib.parse import urlparse


def wait_for(host: str, port: int, name: str, timeout: int = 45) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(1)
    raise SystemExit(f"Timed out waiting for {name} at {host}:{port}")


postgres_host = os.environ.get("POSTGRES_HOST")
postgres_port = int(os.environ.get("POSTGRES_PORT", "5432"))
if postgres_host:
    wait_for(postgres_host, postgres_port, "Postgres")

meili_url = os.environ.get("MEILISEARCH_URL")
if meili_url:
    parsed = urlparse(meili_url)
    host = parsed.hostname or "meilisearch"
    port = parsed.port or 7700
    wait_for(host, port, "Meilisearch")
PY

python manage.py migrate

if [ "${ORL_SEED_ON_START:-true}" = "true" ]; then
  python manage.py seed_outer_rim
fi

python manage.py collectstatic --noinput

exec daphne -b 0.0.0.0 -p 8000 orl.asgi:application
