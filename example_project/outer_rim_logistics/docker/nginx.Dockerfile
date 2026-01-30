FROM nginx:1.27-alpine

RUN apk add --no-cache openssl curl ca-certificates
