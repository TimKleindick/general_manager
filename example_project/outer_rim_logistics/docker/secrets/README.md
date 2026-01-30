Create the following secret files before running docker compose:

- docker/secrets/django_secret_key.txt
- docker/secrets/postgres_password.txt
- docker/secrets/meilisearch_api_key.txt
- docker/secrets/grafana_admin_password.txt

Each file should contain the secret value as plain text with no extra lines.
