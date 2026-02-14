from django.urls import path

from core.views import project_list_view

urlpatterns = [
    path("projects/", project_list_view, name="project-list"),
]
