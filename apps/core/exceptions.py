"""One error envelope for every API failure."""
from __future__ import annotations

import logging

from django.db import DatabaseError
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

log = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    """Wrap DRF errors as {"error": {"code", "message", "details"}}."""
    response = exception_handler(exc, context)

    if response is not None:
        code = getattr(exc, "default_code", "ERROR")
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            code = "VALIDATION_ERROR"
        response.data = {
            "error": {
                "code": str(code).upper(),
                "message": _message_from(response.data),
                "details": response.data,
            }
        }
        return response

    if isinstance(exc, DatabaseError):
        log.exception("database error while serving request")
        return Response(
            {"error": {"code": "DATABASE_ERROR", "message": "database unavailable"}},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # Anything else propagates so Django logs it with a full traceback.
    return None


def _message_from(data) -> str:
    if isinstance(data, dict):
        if "detail" in data:
            return str(data["detail"])
        for key, value in data.items():
            return f"{key}: {_message_from(value)}"
    if isinstance(data, list) and data:
        return _message_from(data[0])
    return str(data)
