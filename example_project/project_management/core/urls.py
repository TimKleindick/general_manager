from django.urls import path

from core.views import dashboard_view, project_list_view

urlpatterns = [
    path("dashboard/", dashboard_view, name="dashboard"),
    path("projects/", project_list_view, name="project-list"),
]
