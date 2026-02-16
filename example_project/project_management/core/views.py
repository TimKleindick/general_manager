from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def project_list_view(request):
    return render(request, "core/spa_entry.html")


@ensure_csrf_cookie
def dashboard_view(request):
    return render(request, "core/spa_entry.html")
