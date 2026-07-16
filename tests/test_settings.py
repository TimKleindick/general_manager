import os
from collections.abc import Mapping

from django.utils.crypto import get_random_string


_SERVER_DATABASE_ENGINES = {
    "postgresql": "django.db.backends.postgresql",
    "mariadb": "django.db.backends.mysql",
}


def _database_config(environ: Mapping[str, str]) -> dict[str, object]:
    selector = environ.get("GENERAL_MANAGER_TEST_DATABASE", "sqlite").strip().lower()
    if selector == "sqlite":
        return {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
        }

    try:
        engine = _SERVER_DATABASE_ENGINES[selector]
    except KeyError as error:
        message = (
            f"Unsupported GENERAL_MANAGER_TEST_DATABASE {selector!r}; "
            "expected sqlite, postgresql, or mariadb"
        )
        raise ValueError(message) from error

    return {
        "ENGINE": engine,
        "NAME": environ["GENERAL_MANAGER_TEST_DATABASE_NAME"],
        "USER": environ["GENERAL_MANAGER_TEST_DATABASE_USER"],
        "PASSWORD": environ["GENERAL_MANAGER_TEST_DATABASE_PASSWORD"],
        "HOST": environ["GENERAL_MANAGER_TEST_DATABASE_HOST"],
        "PORT": environ["GENERAL_MANAGER_TEST_DATABASE_PORT"],
    }


def _database_configs(
    environ: Mapping[str, str],
) -> dict[str, dict[str, object]]:
    default = _database_config(environ)
    secondary = default.copy()
    if default["ENGINE"] != "django.db.backends.sqlite3":
        secondary["NAME"] = environ.get(
            "GENERAL_MANAGER_TEST_SECONDARY_DATABASE_NAME",
            f"{default['NAME']}_secondary",
        )
    return {"default": default, "secondary": secondary}


SECRET_KEY = get_random_string(50)
DEBUG = True

INSTALLED_APPS = [
    "channels",
    "graphene_django",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    # deine App-Package(s):
    "general_manager",  # falls du pip install -e . genutzt hast
]

DATABASES = _database_configs(os.environ)

# Alle weiteren von deinem Code abgefragten Settings
AUTOCREATE_GRAPHQL = True
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
ROOT_URLCONF = "tests.test_urls"
GRAPHQL_URL = "graphql/"
ASGI_APPLICATION = "tests.testing_asgi.application"

MIDDLEWARE = [
    # ggf. noch andere Middleware …
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]
