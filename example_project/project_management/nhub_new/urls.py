from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("core.urls")),
    path("", lambda _request: redirect("dashboard/", permanent=True)),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
