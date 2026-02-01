#!/usr/bin/env sh
set -eu

password_file="/run/secrets/postgres_password"
if [ ! -f "$password_file" ]; then
  echo "Missing postgres password secret at $password_file" >&2
  exit 1
fi

IFS= read -r password < "$password_file"
export DATA_SOURCE_NAME="postgresql://${POSTGRES_USER:-orl}:$password@db:5432/${POSTGRES_DB:-orl}?sslmode=disable"
exec /bin/postgres_exporter
