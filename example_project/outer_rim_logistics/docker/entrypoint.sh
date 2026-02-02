#!/usr/bin/env bash
set -euo pipefail

log_dir="${LOG_DIR:-/app/example_project/outer_rim_logistics/logs}"
mkdir -p "$log_dir"

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
postgres_port_raw = os.environ.get("POSTGRES_PORT", "5432")
try:
    postgres_port = int(postgres_port_raw)
except ValueError as exc:
    raise SystemExit(
        f"Invalid POSTGRES_PORT value {postgres_port_raw!r}; expected an integer."
    ) from exc
if postgres_host:
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

python - <<'PY'
import os

import django
from django.core.management import call_command
from django.db import connection

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "orl.settings")
django.setup()

lock_id = 424242  # Stable ID to serialize migrations across replicas.
seed_lock_id = 424243
with connection.cursor() as cursor:
    cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
    try:
        call_command("migrate", interactive=False)
        if os.environ.get("ORL_SEED_ON_START", "true").lower() == "true":
            cursor.execute("SELECT pg_advisory_lock(%s)", [seed_lock_id])
            try:
                call_command("seed_outer_rim")
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [seed_lock_id])
    finally:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
PY

python manage.py collectstatic --noinput

exec daphne -b 0.0.0.0 -p 8000 orl.asgi:application
