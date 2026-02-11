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


postgres_host = os.environ.get("POSTGRES_HOST", "db")
postgres_port_raw = os.environ.get("POSTGRES_PORT", "5432")
try:
    postgres_port = int(postgres_port_raw)
except ValueError as exc:
    raise SystemExit(
        f"Invalid POSTGRES_PORT value {postgres_port_raw!r}; expected an integer."
    ) from exc
wait_for(postgres_host, postgres_port, "Postgres")

meili_url = os.environ.get("MEILISEARCH_URL")
if meili_url:
    parsed = urlparse(meili_url)
    if not parsed.scheme:
        parsed = urlparse(f"//{meili_url}")
    host = parsed.hostname or "meilisearch"
    port = parsed.port or 7700
    wait_for(host, port, "Meilisearch")
PY

python manage.py migrate --noinput

if [ "${ORL_SEED_ON_START:-true}" = "true" ]; then
  python manage.py seed_outer_rim
fi

python manage.py collectstatic --noinput
