#!/usr/bin/env bash
set -euo pipefail

SCALE_LEVELS=(${SCALE_LEVELS:-1 2 4 6})
DURATION=${DURATION:-10m}

rate_for_scale() {
  case "$1" in
    1) echo "${RATE_1:-30}" ;;
    2) echo "${RATE_2:-50}" ;;
    4) echo "${RATE_4:-70}" ;;
    6) echo "${RATE_6:-90}" ;;
    *) echo "${RATE_DEFAULT:-30}" ;;
  esac
}

for scale in "${SCALE_LEVELS[@]}"; do
  rate=$(rate_for_scale "$scale")
  echo "Running scale suite: web=$scale rate=$rate duration=$DURATION"
  docker compose --profile load up -d --scale web="${scale}"
  GRAPHQL_LIMIT_ENABLED=${GRAPHQL_LIMIT_ENABLED:-0} \
  RATE="${rate}" \
  DURATION="${DURATION}" \
  docker compose --profile load run --rm --no-deps k6
done
