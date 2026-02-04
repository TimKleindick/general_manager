#!/usr/bin/env bash
set -euo pipefail

RUN_LABEL=${RUN_LABEL:-"k6-run"}
OUT_DIR=${OUT_DIR:-"./k6/results"}
RUN_TS=$(date -u +"%Y%m%dT%H%M%SZ")

mkdir -p "${OUT_DIR}"

RUN_LABEL="${RUN_LABEL}" ./k6/record_run.sh

K6_OUT_FILE="${OUT_DIR}/${RUN_TS}-${RUN_LABEL}.json"
export K6_OUT_FILE
K6_OUT_EXTRA=${K6_OUT_EXTRA:-""}
if [[ -n "${K6_OUT_EXTRA}" ]]; then
  export K6_OUT="json=${K6_OUT_FILE},${K6_OUT_EXTRA}"
else
  export K6_OUT="json=${K6_OUT_FILE}"
fi

if [[ $# -eq 0 ]]; then
  ./k6/run_mix.sh
else
  "$@"
fi

if [[ -f "${K6_OUT_FILE}" ]]; then
  python ./k6/summarize_k6.py "${K6_OUT_FILE}" || true
fi
