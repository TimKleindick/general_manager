# Installation

Install GeneralManager in a new or existing Django project. For the complete
empty-directory workflow, including a generated model and GraphQL query, follow
the [quickstart](quickstart.md).

## Requirements

- Python 3.12 or newer
- Django 5.2.15 or newer
- A Django-supported database appropriate for your deployment; this repository's
  CI exercises SQLite, while the maintained production example uses PostgreSQL

GeneralManager is pre-1.0, and the `main` branch may be ahead of the latest PyPI
package. Use the [release history](https://github.com/TimKleindick/general_manager/releases)
to check which behavior has been published.

## Install the base package

Create and activate a virtual environment, then install from PyPI:

```bash
python -m pip install GeneralManager
```

The base package includes the Django, GraphQL, measurement, factory, search,
workflow, and caching dependencies required by the core framework.

## Optional integrations

Install an extra only when the application uses that integration:

| Extra | Install command | Purpose |
| --- | --- | --- |
| Ollama chat | `python -m pip install "GeneralManager[chat-ollama]"` | Local Ollama provider |
| OpenAI chat | `python -m pip install "GeneralManager[chat-openai]"` | OpenAI provider |
| Anthropic chat | `python -m pip install "GeneralManager[chat-anthropic]"` | Anthropic provider |
| Google chat | `python -m pip install "GeneralManager[chat-google]"` | Google provider |
| Image uploads | `python -m pip install "GeneralManager[file-upload-image]"` | Pillow-backed image inspection |
| S3 uploads | `python -m pip install "GeneralManager[file-upload-s3]"` | S3 storage adapters |

Provider credentials, storage policies, and production security settings remain
application responsibilities. See the [chat](concepts/chat_prompting.md) and
[file-upload](concepts/graphql/file_uploads.md) guides before enabling those
features.

## Configure Django

Add GeneralManager to the Django settings module:

```python
INSTALLED_APPS += ["general_manager"]
```

Also add each Django application containing manager definitions. GeneralManager
automatically imports `managers.py` from installed applications during startup;
do not add a second import in `AppConfig.ready()`.

Generated GraphQL is opt-in. To build the schema and append a route during
startup, add:

```python
AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"
```

The [quickstart](quickstart.md) shows the complete project application,
manager, migration, and GraphQL setup.

## Choose database and cache backends

GeneralManager uses Django's database layer. Choose a backend that meets the
application's own deployment requirements, and validate the application's
manager definitions and migrations against it. This repository's automated
test suite exercises SQLite; the maintained
[Outer Rim Logistics example](https://github.com/TimKleindick/general_manager/tree/main/example_project/outer_rim_logistics)
uses PostgreSQL. That evidence should not be read as a claim that every Django
backend is tested here.

Django's local-memory cache is sufficient for one-process development. Use a
shared cache backend when multiple web or worker processes need coordinated
dependency invalidation and cached values. Redis and Memcached are deployment
options, not base installation requirements; follow Django's cache guidance for
the selected backend.

## Verify the installation

Confirm that the active interpreter can import the package:

```bash
python -c "import general_manager; print(general_manager.__name__)"
```

Expected output:

```text
general_manager
```

Continue with the [five-minute quickstart](quickstart.md) to create a record and
retrieve it through generated GraphQL.
