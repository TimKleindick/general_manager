from __future__ import annotations

from django.conf import settings


class GraphQLCsrfExemptMiddleware:
    """Allow local GraphQL clients/tools to POST introspection without CSRF token."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        graphql_path = f"/{getattr(settings, 'GRAPHQL_URL', 'graphql/').lstrip('/')}"
        if request.path == graphql_path and settings.DEBUG:
            request._dont_enforce_csrf_checks = True
        return self.get_response(request)
