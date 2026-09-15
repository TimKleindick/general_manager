"""Settings used only by :mod:`scripts.benchmark_bulk_throughput`.

The benchmark deliberately uses separate Redis logical databases for Django's
cache and Channels.  They are private to the benchmark service described in
the script's help text, so resetting either database cannot affect a developer
cache or channel layer.
"""

from __future__ import annotations

import os

from tests.test_settings import *  # noqa: F403


BENCHMARK_REDIS_URL = os.environ.get(
    "GENERAL_MANAGER_BENCHMARK_REDIS_URL", "redis://127.0.0.1:56497/15"
)
BENCHMARK_CHANNEL_REDIS_URL = os.environ.get(
    "GENERAL_MANAGER_BENCHMARK_CHANNEL_REDIS_URL", "redis://127.0.0.1:56497/14"
)

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": BENCHMARK_REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [BENCHMARK_CHANNEL_REDIS_URL],
            "capacity": int(
                os.environ.get("GENERAL_MANAGER_BENCHMARK_CHANNEL_CAPACITY", "1000")
            ),
            "expiry": int(
                os.environ.get("GENERAL_MANAGER_BENCHMARK_CHANNEL_EXPIRY", "3600")
            ),
        },
    }
}
