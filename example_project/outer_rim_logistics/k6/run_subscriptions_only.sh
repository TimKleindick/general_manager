#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
docker compose --profile load run --rm --no-deps \
  -e RUN_READ_WRITE=false \
  -e RUN_SUBSCRIPTIONS=true \
  -e SUB_DURATION=30s \
  k6
