#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
HEAVY_CALC=true \
HEAVY_RATE=${HEAVY_RATE:-0.1} \
HEAVY_PAGE_SIZE=${HEAVY_PAGE_SIZE:-3} \
docker compose --profile load run --rm --no-deps k6
