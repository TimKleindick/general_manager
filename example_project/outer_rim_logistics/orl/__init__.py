try:
    from .celery import app as celery_app
except ImportError:  # pragma: no cover - optional in dev container
    celery_app = None

__all__ = ["celery_app"]
