#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
RUN_READ_WRITE=false \
RUN_SUBSCRIPTIONS=false \
RUN_STRESS=true \
docker compose --profile load run --rm --no-deps k6
