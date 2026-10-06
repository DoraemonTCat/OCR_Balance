"""CORS for whichever frontend calls this service.

A single hand-rolled middleware rather than django-cors-headers: the allow-list
is one setting, the API has no cookies or credentials, and this keeps the
dependency list short for an image that already carries PaddleOCR.
"""
from __future__ import annotations

from django.conf import settings
from django.http import HttpResponse

_ALLOWED_METHODS = "GET, POST, OPTIONS"
_ALLOWED_HEADERS = "Content-Type, Authorization, X-Requested-With"


class CorsMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response
        self.allowed = set(settings.CORS_ALLOW_ORIGINS)

    def __call__(self, request):
        origin = request.headers.get("Origin", "")

        if request.method == "OPTIONS" and "Access-Control-Request-Method" in request.headers:
            response = HttpResponse(status=204)
        else:
            response = self.get_response(request)

        if origin and origin in self.allowed:
            response["Access-Control-Allow-Origin"] = origin
            response["Access-Control-Allow-Methods"] = _ALLOWED_METHODS
            response["Access-Control-Allow-Headers"] = _ALLOWED_HEADERS
            response["Access-Control-Max-Age"] = "600"
            # Caches must not serve one origin's response to another.
            response["Vary"] = "Origin"
        return response
