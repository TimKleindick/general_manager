from django.shortcuts import render


def project_list_view(request):
    return render(request, "core/spa_entry.html")


def dashboard_view(request):
    return render(request, "core/spa_entry.html")
