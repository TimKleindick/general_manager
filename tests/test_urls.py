from django.urls import include, path

urlpatterns = [
    path("", include("general_manager.mcp.urls")),
]
