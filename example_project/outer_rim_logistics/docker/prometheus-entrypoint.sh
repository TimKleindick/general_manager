#!/usr/bin/env sh
set -eu

cert_file="/etc/prometheus/certs/tls.crt"

if [ ! -f "$cert_file" ]; then
  echo "Waiting for TLS cert at $cert_file..."
  for _ in $(seq 1 30); do
    if [ -f "$cert_file" ]; then
      break
    fi
    sleep 1
  done
fi

if [ ! -f "$cert_file" ]; then
  echo "TLS cert missing at $cert_file" >&2
  exit 1
fi

exec /bin/prometheus "$@"
