"""Tesseract, as a second reader of Thai text lines.

PaddleOCR's Thai recogniser and Tesseract's are trained differently and are
wrong in different places - one reads ช as ซ where the other reads it as ข - so
on the cells where every character counts (a buyer's name) their readings are
combined; see ``workers.thai_names``.

Tesseract runs as its command-line program, once per batch: it accepts a file
listing images and answers with their texts separated by form feeds, so a page
of names costs one process start rather than one per name.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from django.conf import settings

log = logging.getLogger(__name__)

#: Where the UB Mannheim installer puts it on Windows, used when it is not on
#: PATH (the installer does not add it).
_WINDOWS_DEFAULT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


class TesseractUnavailable(Exception):
    pass


def command() -> str | None:
    """The tesseract executable, or None when it is not installed."""
    configured = settings.OCR.get("TESSERACT_CMD") or ""
    for candidate in (configured, shutil.which("tesseract"), _WINDOWS_DEFAULT):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def available(language: str) -> bool:
    """True when tesseract and the traineddata for ``language`` are both there."""
    return command() is not None and (
        Path(settings.OCR["TESSDATA_DIR"]) / f"{language}.traineddata"
    ).is_file()


def recognize_lines(crops, language: str, *, scale: float = 1.0) -> list[str]:
    """Read each crop as a single line of text (page segmentation mode 7)."""
    if not crops:
        return []
    executable = command()
    if executable is None:
        raise TesseractUnavailable("tesseract is not installed")

    env = dict(os.environ, TESSDATA_PREFIX=str(settings.OCR["TESSDATA_DIR"]))
    with tempfile.TemporaryDirectory(prefix="ocr-tess-") as folder:
        listing = Path(folder) / "images.txt"
        names = []
        for index, crop in enumerate(crops):
            if scale != 1.0:
                crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            name = Path(folder) / f"{index:05d}.png"
            cv2.imwrite(str(name), crop)
            names.append(str(name))
        listing.write_text("\n".join(names) + "\n", encoding="utf-8")
        try:
            result = subprocess.run(
                [executable, str(listing), "stdout", "-l", language, "--psm", "7"],
                capture_output=True,
                env=env,
                timeout=max(60, 2 * len(crops)),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TesseractUnavailable(f"tesseract failed: {exc}") from exc
    if result.returncode != 0:
        raise TesseractUnavailable(
            f"tesseract exited {result.returncode}: "
            f"{result.stderr.decode('utf-8', 'replace')[-300:]}"
        )

    # One page per image, each ended by a form feed.
    pages = result.stdout.decode("utf-8", "replace").split("\f")
    texts = [page.strip() for page in pages[: len(crops)]]
    texts += [""] * (len(crops) - len(texts))
    return texts
