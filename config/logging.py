"""Structured JSON logging.

Worker log records carry transaction_id / worker_id / file_name / page /
step / duration_ms whenever the caller supplies them via ``extra``.
"""
from __future__ import annotations

import json
import logging

_CONTEXT_FIELDS = (
    "transaction_id",
    "worker_id",
    "file_name",
    "page",
    "step",
    "duration_ms",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "message": record.getMessage(),
        }
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
