FROM nginx:1.28.1-alpine

RUN apk add --no-cache openssl curl ca-certificates
