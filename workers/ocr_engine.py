"""PaddleOCR wrapper with OpenCV preprocessing.

PaddleOCR is loaded lazily and only once per process: model loading costs
seconds and hundreds of MB, and a worker that only ever sees text-layer PDFs
should never pay for it.
"""
from __future__ import annotations

import logging
import os
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
_recognizer = None


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
            # PaddleX checks every model host on import, which on a machine
            # without direct internet access costs a timeout per host.
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            try:
                from paddleocr import PaddleOCR
            except ImportError as exc:  # pragma: no cover - deployment issue
                raise OcrFailed(f"PaddleOCR is not installed: {exc}") from exc
            language = settings.OCR["LANGUAGE"]
            log.info("loading PaddleOCR", extra={"step": "ocr_engine_load"})
            try:
                _engine = PaddleOCR(
                    lang=language,
                    device="gpu" if settings.OCR["USE_GPU"] else "cpu",
                    # The pipeline turns and straightens the page itself
                    # (grid_detector.orient / deskew), from the table's rules,
                    # which is more reliable on these forms than a classifier.
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    # Lower than the defaults: the header lines are broken up
                    # by dotted leaders, and at the default thresholds the line
                    # carrying the drug's name is not detected at all.
                    text_det_thresh=settings.OCR["DET_THRESH"],
                    text_det_box_thresh=settings.OCR["DET_BOX_THRESH"],
                )
            except Exception as exc:
                # An unsupported language surfaces as a bare assertion deep in
                # PaddleX; the operator needs to see which setting caused it.
                raise OcrFailed(
                    f"cannot load PaddleOCR for OCR_LANGUAGE={language!r}: {exc}"
                ) from exc
    return _engine


def get_recognizer():
    """The text-recognition model alone, for pages read cell by cell."""
    global _recognizer
    if _recognizer is not None:
        return _recognizer
    with _engine_lock:
        if _recognizer is None:
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            try:
                from paddleocr import TextRecognition
            except ImportError as exc:  # pragma: no cover - deployment issue
                raise OcrFailed(f"PaddleOCR is not installed: {exc}") from exc
            model = settings.OCR["REC_MODEL"]
            log.info("loading recogniser", extra={"step": "ocr_rec_load", "model": model})
            try:
                _recognizer = TextRecognition(
                    model_name=model,
                    device="gpu" if settings.OCR["USE_GPU"] else "cpu",
                )
            except Exception as exc:
                raise OcrFailed(f"cannot load recognition model {model!r}: {exc}") from exc
    return _recognizer


def recognize_crops(crops) -> list[tuple[str, float]]:
    """Recognise a batch of word images; one (text, confidence) per image."""
    if not crops:
        return []
    model = get_recognizer()
    try:
        results = model.predict(list(crops), batch_size=settings.OCR["REC_BATCH"])
    except Exception as exc:
        raise OcrFailed(f"PaddleOCR recognition failed: {exc}") from exc
    return [(str(r["rec_text"]), float(r["rec_score"])) for r in results]


def recognize_cells(image, grid, page_number: int = 0, name_columns=()):
    """Read a ruled table cell by cell - see ``workers.cell_ocr``.

    The cells of ``name_columns`` are read by several engines and combined -
    see ``workers.thai_names``.
    """
    from workers import cell_ocr

    return cell_ocr.recognize(
        image,
        grid,
        recognize_crops,
        header_reader=recognize_image,
        line_reader=read_names if name_columns else None,
        line_columns=name_columns,
    )


def _at_height(crops, height: int):
    """The crops scaled to the recogniser's own input height."""
    scaled = []
    for crop in crops:
        factor = height / max(crop.shape[0], 1)
        scaled.append(
            cv2.resize(
                crop,
                None,
                fx=factor,
                fy=factor,
                interpolation=cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA,
            )
        )
    return scaled


def read_names(crops) -> list[tuple[str, float]]:
    """Read lines of Thai names with every engine available, and combine them.

    The confidence returned with each name is how many of the readings agreed
    with it - see ``thai_names.REVIEW_AGREEMENT``.
    """
    from workers import tesseract_engine, thai_names

    def paddle(batch):
        return [text for text, _ in recognize_crops(batch)]

    accurate = settings.OCR["TESSERACT_LANG"]
    fast = settings.OCR["TESSERACT_FAST_LANG"]
    readers = [(paddle, 1.1)]
    if tesseract_engine.available(accurate):
        readers.append((lambda b: tesseract_engine.recognize_lines(b, accurate), 1.0))
    if tesseract_engine.available(fast):
        readers.append((lambda b: tesseract_engine.recognize_lines(b, fast), 0.9))
    if tesseract_engine.available(accurate):
        readers.append(
            (lambda b: tesseract_engine.recognize_lines(b, accurate, scale=2.0), 0.9)
        )
    readers.append((lambda batch: paddle(_at_height(batch, 48)), 0.9))
    if len(readers) == 2:
        log.info("tesseract not available; names read by PaddleOCR alone")
    return thai_names.read(crops, readers)


def recognize_image(image, page_number: int = 0) -> OcrPage:
    """OCR a page array into lines with bounding boxes and confidence."""
    engine = get_engine()
    try:
        results = engine.predict(image)
    except Exception as exc:
        raise OcrFailed(f"PaddleOCR failed on page {page_number}: {exc}") from exc

    lines: list[TextLine] = []
    confidences: list[float] = []
    for result in results or []:
        for text, confidence, box in zip(
            result["rec_texts"], result["rec_scores"], result["rec_boxes"]
        ):
            text = (text or "").strip()
            if not text:
                continue
            x0, y0, x1, y1 = (float(value) for value in box)
            lines.append(TextLine(text=text, bbox=(x0, y0, x1, y1), confidence=float(confidence)))
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
