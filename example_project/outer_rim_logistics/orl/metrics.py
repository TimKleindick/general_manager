from __future__ import annotations

from django.http import HttpResponse


def metrics(_request) -> HttpResponse:
    try:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    except ImportError:
        return HttpResponse(
            "prometheus_client is not installed.",
            status=501,
            content_type="text/plain",
        )
    return HttpResponse(generate_latest(), content_type=CONTENT_TYPE_LATEST)
