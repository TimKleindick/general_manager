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


redis_url = os.environ.get("REDIS_URL")
if redis_url:
    parsed = urlparse(redis_url)
    host = parsed.hostname or "redis"
    port = parsed.port or 6379
    wait_for(host, port, "Redis")
PY

exec "$@"
