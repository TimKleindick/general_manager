import sys
from pathlib import Path

from django.utils.crypto import get_random_string

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SECRET_KEY = get_random_string(50)
DEBUG = True

INSTALLED_APPS = [
    "channels",
    "graphene_django",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "general_manager",
    "outer_rim_logistics.crew.apps.CrewConfig",
    "outer_rim_logistics.maintenance.apps.MaintenanceConfig",
    "outer_rim_logistics.supply.apps.SupplyConfig",
    "outer_rim_logistics.mission.apps.MissionConfig",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

AUTOCREATE_GRAPHQL = True
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
ROOT_URLCONF = "orl.urls"
GRAPHQL_URL = "graphql/"
ASGI_APPLICATION = "orl.asgi.application"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "orl-tests",
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
