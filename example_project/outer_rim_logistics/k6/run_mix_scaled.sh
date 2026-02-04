#!/usr/bin/env bash
set -euo pipefail

WEB_SCALE=${WEB_SCALE:-5}

docker compose --profile load up -d --scale web="${WEB_SCALE}"
GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
docker compose --profile load run --rm --no-deps k6
