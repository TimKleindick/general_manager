#!/usr/bin/env bash
set -euo pipefail

GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
BASELINE=true \
RATE=0 \
SUB_VUS=0 \
docker compose --profile load run --rm --no-deps k6
