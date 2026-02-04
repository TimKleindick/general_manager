#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
READ_WEIGHT=100 \
WRITE_WEIGHT=0 \
docker compose --profile load run --rm --no-deps \
  -e RUN_SUBSCRIPTIONS=false \
  -e RUN_READ_WRITE=true \
  k6
