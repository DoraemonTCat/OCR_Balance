"""PaddleOCR wrapper with OpenCV preprocessing.

PaddleOCR is loaded lazily and only once per process: model loading costs
seconds and hundreds of MB, and a worker that only ever sees text-layer PDFs
should never pay for it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

try:
    import cv2
except ImportError:  # pragma: no cover - the API image does not need OpenCV
    cv2 = None

from django.conf import settings

from workers.pdf_extractor import TextLine

log = logging.getLogger(__name__)

_engine = None
_engine_lock = threading.Lock()


class OcrFailed(Exception):
    error_code = "OCR_FAILED"


@dataclass(slots=True)
class OcrPage:
    text: str
    lines: list[TextLine]
    confidence: float | None


def get_engine():
    """Return the process-wide PaddleOCR instance, loading it on first use."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is None:
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:  # pragma: no cover - deployment issue
                raise OcrFailed(f"PaddleOCR is not installed: {exc}") from exc
            language = settings.OCR["LANGUAGE"]
            log.info("loading PaddleOCR", extra={"step": "ocr_engine_load"})
            try:
                _engine = PaddleOCR(
                    use_angle_cls=True,
                    lang=language,
                    use_gpu=settings.OCR["USE_GPU"],
                    show_log=False,
                )
            except Exception as exc:
                # PaddleOCR asserts on an unsupported language, so a bad
                # OCR_LANGUAGE surfaced as a bare AssertionError and a 500 with
                # no explanation. The message it raises names the languages it
                # does ship, which is what the operator needs to see.
                raise OcrFailed(
                    f"cannot load PaddleOCR for OCR_LANGUAGE={language!r}: {exc}"
                ) from exc
    return _engine


def preprocess_image(image):
    """Grayscale, denoise and binarise before OCR.

    Adaptive thresholding is used rather than a global threshold because these
    documents are scanned unevenly: a global cut-off loses the faint text on the
    darker side of the page.

    Takes an array, not encoded bytes: the pipeline renders a page, deskews it
    and hands the array straight over, so nothing is ever encoded to PNG.
    """
    if cv2 is None:  # pragma: no cover
        raise OcrFailed("OpenCV (opencv-python-headless) is not installed")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7,
                                    searchWindowSize=21)
    binary = cv2.adaptiveThreshold(
        gray,
        maxValue=255,
        adaptiveMethod=cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        thresholdType=cv2.THRESH_BINARY,
        blockSize=31,
        C=15,
    )
    # PaddleOCR expects 3 channels.
    return cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)


def recognize_image(image, page_number: int = 0) -> OcrPage:
    """OCR a page array into lines with bounding boxes and confidence."""
    image = preprocess_image(image)
    engine = get_engine()
    try:
        result = engine.ocr(image, cls=True)
    except Exception as exc:
        raise OcrFailed(f"PaddleOCR failed on page {page_number}: {exc}") from exc

    lines: list[TextLine] = []
    confidences: list[float] = []

    # PaddleOCR returns one list per image; a blank page yields [None].
    for page_result in result or []:
        for entry in page_result or []:
            box, (text, confidence) = entry[0], entry[1]
            text = (text or "").strip()
            if not text:
                continue
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            lines.append(
                TextLine(
                    text=text,
                    bbox=(min(xs), min(ys), max(xs), max(ys)),
                    confidence=float(confidence),
                )
            )
            confidences.append(float(confidence))

    # Recognised boxes carry no order of their own, so reading order is the
    # geometric one; the parser then treats both kinds of box the same way.
    lines.sort(key=lambda item: (round(item.top, 1), item.x0))
    for index, line in enumerate(lines):
        line.order = index
    page_confidence = (
        round(sum(confidences) / len(confidences), 4) if confidences else None
    )
    return OcrPage(
        text="\n".join(line.text for line in lines),
        lines=lines,
        confidence=page_confidence,
    )

