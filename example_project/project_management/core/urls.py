from django.urls import path

from core.views import dashboard_view, project_list_view, upload_project_image_view

urlpatterns = [
    path("dashboard/", dashboard_view, name="dashboard"),
    path("projects/", project_list_view, name="project-list"),
    path(
        "api/projects/<int:project_id>/image/",
        upload_project_image_view,
        name="project-image-upload",
    ),
]
