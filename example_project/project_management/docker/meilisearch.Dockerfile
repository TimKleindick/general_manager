FROM curlimages/curl:8.18.0 AS curl
FROM busybox:1.36.1 AS busybox

FROM getmeili/meilisearch:v1.8

USER root

RUN mkdir -p /usr/bin /bin /etc/ssl/certs
COPY --from=curl /usr/bin/curl /usr/bin/curl
COPY --from=curl /usr/bin/curl /bin/curl
COPY --from=curl /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=busybox /bin/busybox /bin/busybox
COPY --from=busybox /lib /lib

SHELL ["/bin/busybox", "sh", "-c"]

# shellcheck shell=sh
RUN mkdir -p /meili_data && chown -R 1000:1000 /meili_data

USER 1000:1000
