#!/usr/bin/env bash
set -euo pipefail

RUN_LABEL=${RUN_LABEL:-"k6-run"}
RUN_TS=$(date -u +"%Y%m%dT%H%M%SZ")
RUN_DIR="$(dirname "$0")/runs"
RUN_FILE="${RUN_DIR}/${RUN_TS}-${RUN_LABEL}.env"

mkdir -p "${RUN_DIR}"

{
  echo "run_label=${RUN_LABEL}"
  echo "run_timestamp_utc=${RUN_TS}"
  echo "git_commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "host=$(uname -a)"
  echo "cwd=$(pwd)"
  echo "---- env ----"
  env | sort
} > "${RUN_FILE}"

echo "wrote ${RUN_FILE}"
