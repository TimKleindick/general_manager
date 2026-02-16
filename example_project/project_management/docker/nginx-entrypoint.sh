#!/usr/bin/env sh
set -eu

mkdir -p /var/log/project-management
touch /var/log/project-management/nginx_access.log /var/log/project-management/nginx_error.log

template="/tmp/nginx.conf.template"
if [ -f "$template" ]; then
  cp "$template" /tmp/nginx.conf
  exec nginx -c /tmp/nginx.conf -g "daemon off;"
fi

exec nginx -g "daemon off;"
