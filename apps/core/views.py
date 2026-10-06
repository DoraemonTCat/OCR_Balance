"""Probes. No models, no business logic - just "is this process usable?"."""
from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import JsonResponse


def health(request) -> JsonResponse:
    """Liveness: the process is up. Deliberately touches nothing else."""
    return JsonResponse({"status": "ok"})


def ready(request) -> JsonResponse:
    """Readiness: database and storage reachable.

    The OCR engine is not loaded here: PaddleOCR takes seconds and hundreds of
    MB to load, and a process that is merely serving the API never OCRs
    anything until a document arrives.
    """
    checks: dict[str, str] = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except DatabaseError as exc:
        checks["database"] = f"error: {exc}"
        ok = False

    root = settings.DOCUMENT_ROOT
    if root.is_dir():
        checks["storage"] = "ok"
    else:
        checks["storage"] = f"error: {root} is not a directory"
        ok = False

    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status=200 if ok else 503,
    )
