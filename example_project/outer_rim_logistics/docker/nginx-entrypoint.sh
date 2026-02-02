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
  openssl_config="$(mktemp)"
  cat > "$openssl_config" <<'EOF'
[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = localhost

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = nginx
IP.1 = 127.0.0.1
EOF
  openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$key_file" \
    -out "$cert_file" \
    -config "$openssl_config" \
    -extensions v3_req
  rm -f "$openssl_config"
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: curl is required but not installed." >&2
  exit 1
fi

mkdir -p /var/log/outer-rim/nginx
touch /var/log/outer-rim/nginx/nginx_access.log /var/log/outer-rim/nginx/nginx_error.log
tail -n 0 -F /var/log/outer-rim/nginx/nginx_access.log /var/log/outer-rim/nginx/nginx_error.log &

mkdir -p /var/cache/nginx/client_temp
chmod 1777 /var/cache/nginx /var/cache/nginx/client_temp

exec nginx -g "daemon off;"
