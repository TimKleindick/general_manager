#!/usr/bin/env sh
set -eu

cert_dir="/etc/nginx/certs"
cert_file="${cert_dir}/tls.crt"
key_file="${cert_dir}/tls.key"

if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
  echo "Generating self-signed TLS certificate for localhost..."
  if ! command -v openssl >/dev/null 2>&1; then
    apk add --no-cache openssl
  fi
  mkdir -p "$cert_dir"
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$key_file" \
    -out "$cert_file" \
    -subj "/CN=localhost"
fi

exec nginx -g "daemon off;"
