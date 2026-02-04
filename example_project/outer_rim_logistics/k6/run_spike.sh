#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
READ_WEIGHT=100 \
WRITE_WEIGHT=0 \
RUN_READ_WRITE=false \
RUN_SUBSCRIPTIONS=false \
RUN_SPIKE=true \
docker compose --profile load run --rm --no-deps k6
