FROM curlimages/curl:8.18.0 AS curl

FROM getmeili/meilisearch:v1.34.3

USER root

RUN mkdir -p /usr/bin /bin /etc/ssl/certs
COPY --from=curl /usr/bin/curl /usr/bin/curl
COPY --from=curl /usr/bin/curl /bin/curl
COPY --from=curl /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# shellcheck shell=sh
RUN mkdir -p /meili_data \
    && chown -R 1000:1000 /meili_data

USER 1000:1000
