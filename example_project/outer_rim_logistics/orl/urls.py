from __future__ import annotations

from django.contrib import admin
from django.urls import path

from .metrics import metrics

urlpatterns = [
    path("admin/", admin.site.urls),
    path("metrics/", metrics),
]
