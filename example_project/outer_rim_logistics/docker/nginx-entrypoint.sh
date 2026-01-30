#!/usr/bin/env sh
set -eu

cert_dir="/etc/nginx/certs"
cert_file="${cert_dir}/tls.crt"
key_file="${cert_dir}/tls.key"

if [ ! -f "$cert_file" ] || [ ! -f "$key_file" ]; then
  echo "Generating self-signed TLS certificate for localhost..."
  if ! command -v openssl >/dev/null 2>&1; then
    echo "ERROR: openssl is required but not installed." >&2
    exit 1
  fi
  mkdir -p "$cert_dir"
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$key_file" \
    -out "$cert_file" \
    -subj "/CN=localhost"
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required but not installed." >&2
  exit 1
fi

mkdir -p /var/log/outer-rim/nginx
touch /var/log/outer-rim/nginx/nginx_access.log /var/log/outer-rim/nginx/nginx_error.log
tail -n 0 -F /var/log/outer-rim/nginx/nginx_access.log /var/log/outer-rim/nginx/nginx_error.log &

exec nginx -g "daemon off;"
