Create the following secret files before running docker compose:

- docker/secrets/django_secret_key.txt
- docker/secrets/postgres_password.txt
- docker/secrets/meilisearch_api_key.txt
- docker/secrets/grafana_admin_password.txt

Each file should contain the secret value as plain text with no extra lines.

Notes:

- `docker/secrets/meilisearch_api_key.txt.example` is provided as a template.
  Copy it to `docker/secrets/meilisearch_api_key.txt` and replace the value.
- `docker/secrets/postgres_password.txt` and
  `docker/secrets/grafana_admin_password.txt` contain placeholder values that
  should be replaced before use outside local development.
