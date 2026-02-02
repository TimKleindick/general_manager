#!/usr/bin/env sh
set -eu

log_dir="${LOG_DIR:-/app/example_project/outer_rim_logistics/logs}"
mkdir -p "$log_dir"

exec "$@"
