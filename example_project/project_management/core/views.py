from django.shortcuts import render


def project_list_view(request):
    return render(request, "core/project_list.html")


def dashboard_view(request):
    return render(request, "core/dashboard.html")
