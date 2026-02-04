#!/usr/bin/env bash
set -euo pipefail

RUN_LABEL=${RUN_LABEL:-"scale-suite"}
OUT_DIR=${OUT_DIR:-"./k6/results"}
RUN_TS=$(date -u +"%Y%m%dT%H%M%SZ")

mkdir -p "${OUT_DIR}"
RUN_LABEL="${RUN_LABEL}" ./k6/record_run.sh

K6_OUT_FILE="${OUT_DIR}/${RUN_TS}-${RUN_LABEL}.json"
export K6_OUT_FILE
export K6_OUT="json=${K6_OUT_FILE}"

./k6/run_scale_suite.sh
