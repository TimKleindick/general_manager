from __future__ import annotations

from django.urls import path

from .metrics import metrics

urlpatterns = [
    path("metrics/", metrics),
]
