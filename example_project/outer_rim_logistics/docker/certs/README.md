Create TLS certs for Nginx:

```bash
mkdir -p docker/certs
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout docker/certs/tls.key \
  -out docker/certs/tls.crt \
  -subj "/CN=localhost"
```

Update the certs for real deployments.
