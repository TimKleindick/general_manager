from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def _read_secret_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return None


def _env(name: str, default: str | None = None) -> str | None:
    file_var = f"{name}_FILE"
    file_path = os.environ.get(file_var)
    if file_path:
        file_value = _read_secret_file(file_path)
        if file_value is not None:
            return file_value
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    value = os.environ.get(name, "")
    if not value.strip():
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


SECRET_KEY = _env("DJANGO_SECRET_KEY", "dev-secret-key-outer-rim-logistics")
DEBUG = _env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS: list[str] = _env_list("DJANGO_ALLOWED_HOSTS", ["*"])
CSRF_TRUSTED_ORIGINS = _env_list("DJANGO_CSRF_TRUSTED_ORIGINS", [])

INSTALLED_APPS = [
    "django_prometheus",
    "daphne",
    "channels",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "crew.apps.CrewConfig",
    "supply.apps.SupplyConfig",
    "maintenance.apps.MaintenanceConfig",
    "mission.apps.MissionConfig",
    "general_manager",
    "graphene_django",
]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

ROOT_URLCONF = "orl.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

WSGI_APPLICATION = "orl.wsgi.application"
ASGI_APPLICATION = "orl.asgi.application"

if _env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django_prometheus.db.backends.postgresql",
            "NAME": _env("POSTGRES_DB", "orl"),
            "USER": _env("POSTGRES_USER", "orl"),
            "PASSWORD": _env("POSTGRES_PASSWORD", "orl"),
            "HOST": _env("POSTGRES_HOST", "db"),
            "PORT": _env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django_prometheus.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = Path(_env("STATIC_ROOT", str(BASE_DIR / "staticfiles")))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if _env("REDIS_URL"):
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": _env("REDIS_URL"),
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "outer-rim-logistics",
            "OPTIONS": {
                "MAX_ENTRIES": 10000,
                "CULL_FREQUENCY": 3,
            },
        }
    }

LOG_DIR = Path(_env("LOG_DIR", str(BASE_DIR / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "fmt": "%(asctime)s %(levelname)s %(name)s %(message)s",
        },
    },
    "handlers": {
        "gm_file": {
            "class": "logging.FileHandler",
            "filename": LOG_DIR / "general_manager.log",
            "formatter": "json",
        }
    },
    "loggers": {
        "general_manager": {
            "handlers": ["gm_file"],
            "level": "INFO",
            "propagate": False,
        }
    },
}

if _env("MEILISEARCH_URL"):
    search_backend = {
        "class": "general_manager.search.backends.meilisearch.MeilisearchBackend",
        "options": {
            "url": _env("MEILISEARCH_URL"),
            "api_key": _env("MEILISEARCH_API_KEY"),
        },
    }
else:
    search_backend = {
        "class": "general_manager.search.backends.dev.DevSearchBackend",
        "options": {},
    }

GENERAL_MANAGER = {
    "SEARCH_BACKEND": search_backend,
    "SEARCH_AUTO_REINDEX": _env_bool("GM_SEARCH_AUTO_REINDEX", True),
    "SEARCH_ASYNC": _env_bool("GM_SEARCH_ASYNC", False),
    "AUDIT_LOGGER": {
        "class": "general_manager.permission.audit.FileAuditLogger",
        "options": {
            "path": str(LOG_DIR / "permission_audit.log"),
        },
    },
}

GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED = _env_bool(
    "GENERAL_MANAGER_GRAPHQL_METRICS_ENABLED", False
)
GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND = _env(
    "GENERAL_MANAGER_GRAPHQL_METRICS_BACKEND", "prometheus"
)
GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING = _env_bool(
    "GENERAL_MANAGER_GRAPHQL_METRICS_RESOLVER_TIMING", False
)

AUTOCREATE_GRAPHQL = True
GRAPHQL_URL = "graphql/"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = int(_env("DJANGO_SECURE_HSTS_SECONDS", "0") or 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool(
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False
)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

CELERY_BROKER_URL = _env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = _env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

if _env("REDIS_URL"):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [_env("REDIS_URL")]},
        }
    }
else:
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
