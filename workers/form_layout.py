"""The three psychotropic report forms and how their columns are identified.

Form identification cannot use the printed titles. PaddleOCR 2.9.1 ships no Thai
recognition model (its languages are ch / en / korean / japan / chinese_cht /
ta / te / ka / latin / arabic / cyrillic / devanagari), so the Thai column
headings come back as noise - "หมายเหตุ" reads as "n&nan". What survives is the
geometry: each form rules its columns to fixed proportions, and the three
layouts are far enough apart that the vector of relative column widths
identifies a page on its own.

Measured on ``ข้อมูล OCR.PDF`` at 200 dpi, the three signatures differ by at
least 0.02 in several components while the same form varies by under 0.002
across pages, so nearest-signature matching has a wide margin.

Add a form by appending a ``FormLayout``: give it the column roles in physical
order and a signature read off a clean scan with
``grid_detector.detect(...).column_widths``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


class FormNotRecognised(Exception):
    """Raised when a page's column layout matches no known form."""

    error_code = "TABLE_PARSE_FAILED"


# --- column roles ----------------------------------------------------------
#
# One vocabulary shared by all three forms, so the parser does not branch per
# form. A role absent from a form is simply absent from its column list.

DATE = "date"                      #: วัน เดือน ปี
GENERIC_NAME = "generic_name"      #: ชื่อและความแรงของวัตถุออกฤทธิ์
TRADE_NAME = "trade_name"          #: ชื่อการค้า
BATCH_NO = "batch_no"              #: เลขที่/รุ่นที่/ครั้งที่ผลิต
MANUFACTURER = "manufacturer"      #: ชื่อผู้ผลิตและแหล่งผลิต
RECEIVED_FROM = "received_from"    #: ได้มาจาก
ISSUED_TO = "issued_to"            #: จ่ายไปให้ / จำหน่ายให้ / ชื่อ-นามสกุลผู้รับยา
RECIPIENT_ID = "recipient_id"      #: เลขที่บัตรประจำตัวประชาชน
BALANCE_BROUGHT = "balance_brought"  #: ยอดยกมา
RECEIVED = "received"              #: รับ
ISSUED = "issued"                  #: จ่าย
BALANCE = "balance"                #: คงเหลือ
UNIT = "unit"                      #: หน่วย
PRESCRIPTION_NO = "prescription_no"  #: เลขที่ใบสั่งยา
REMARK = "remark"                  #: หมายเหตุ

#: Roles whose cell holds a quantity, so OCR noise can be held to digits.
NUMERIC_ROLES = frozenset({BALANCE_BROUGHT, RECEIVED, ISSUED, BALANCE})

#: Roles the writers fill in Thai script. Without a Thai recognition model these
#: cells cannot be read at all, and - this is the trap - they do not *look*
#: unreadable: the Latin model answers Thai handwriting with plausible Latin
#: letters ("ชลิดา จันทร์สด" comes back as "t@on SunScQ"), so no inspection of
#: the returned string can tell it apart from a name that was read correctly.
#: Which column a cell came from is the only reliable signal, so these are
#: flagged by role.
#:
#: ``MANUFACTURER`` and ``RECEIVED_FROM`` are not here: the manufacturer is
#: written in Latin on these ledgers ("Asian Pharm").
THAI_ROLES = frozenset({ISSUED_TO, UNIT, REMARK})

#: PaddleOCR languages that can read Thai. Empty for 2.9.1, which ships none;
#: the flagging above switches off by itself if a Thai model is ever configured.
THAI_CAPABLE_LANGUAGES = frozenset({"th"})


@dataclass(frozen=True, slots=True)
class FormLayout:
    """One form: its code, its columns in physical order, and how to spot it."""

    code: str
    title: str
    #: Column roles, left to right. Length must equal the detected column count.
    columns: tuple[str, ...]
    #: Relative column widths, each as a fraction of the table width.
    signature: tuple[float, ...]
    #: Wording that only this form's printed title contains. Used when the page
    #: carries its own text, which is both exact and immune to a scan that
    #: stretched the table. Empty for the handwritten forms, whose titles no
    #: engine here can read.
    markers: tuple[str, ...] = ()

    def role_at(self, index: int | None) -> str | None:
        if index is None or not 0 <= index < len(self.columns):
            return None
        return self.columns[index]

    def index_of(self, role: str) -> int | None:
        return self.columns.index(role) if role in self.columns else None


RVJ_7_4 = FormLayout(
    code="ร.ว.จ ๗/๔",
    title="รายงานผลการดำเนินกิจการเกี่ยวกับวัตถุออกฤทธิ์ในประเภท ๓ หรือประเภท ๔",
    columns=(
        DATE,
        GENERIC_NAME,
        BATCH_NO,
        MANUFACTURER,
        RECEIVED_FROM,
        ISSUED_TO,
        BALANCE_BROUGHT,
        RECEIVED,
        ISSUED,
        BALANCE,
        UNIT,
        REMARK,
    ),
    signature=(0.081, 0.157, 0.080, 0.100, 0.070, 0.081,
               0.080, 0.060, 0.070, 0.080, 0.070, 0.070),
)

BVJ_7_4_KP = FormLayout(
    code="บ.ว.จ ๗/๔-ขพ",
    title="บัญชีจำหน่ายวัตถุออกฤทธิ์ในประเภท ๓ หรือประเภท ๔",
    columns=(
        DATE,
        GENERIC_NAME,
        TRADE_NAME,
        BATCH_NO,
        RECEIVED_FROM,
        ISSUED_TO,
        RECIPIENT_ID,
        BALANCE_BROUGHT,
        RECEIVED,
        ISSUED,
        BALANCE,
        PRESCRIPTION_NO,
    ),
    signature=(0.044, 0.113, 0.079, 0.096, 0.077, 0.108,
               0.167, 0.074, 0.065, 0.065, 0.065, 0.046),
)

RK_4 = FormLayout(
    code="ร.ค.-๔",
    title="รายงานเกี่ยวกับการดำเนินกิจการมีไว้ในครอบครองวัตถุออกฤทธิ์ในประเภท ๓ หรือประเภท ๔",
    columns=(
        DATE,
        GENERIC_NAME,
        TRADE_NAME,
        BATCH_NO,
        RECEIVED_FROM,
        ISSUED_TO,
        BALANCE_BROUGHT,
        RECEIVED,
        ISSUED,
        BALANCE,
        REMARK,
    ),
    signature=(0.082, 0.160, 0.104, 0.092, 0.102, 0.102,
               0.082, 0.061, 0.061, 0.071, 0.082),
)

# --- วัตถุออกฤทธิ์ / ยาเสพติดให้โทษ ในประเภท ๒ ---------------------------
#
# A newer family, and a different one: these are typed rather than filled in by
# hand, and the files carry their own text. They are identified by their printed
# title, so their signatures are only the fallback for a copy that arrived as a
# plain scan.

BYS_2 = FormLayout(
    code="บ.ย.ส. ๒/ว.จ. ๒-จ๑",
    title="บัญชีจำหน่ายยาเสพติดให้โทษในประเภท ๒ หรือวัตถุออกฤทธิ์ในประเภท ๒",
    columns=(
        DATE,
        GENERIC_NAME,
        TRADE_NAME,
        BATCH_NO,
        RECEIVED_FROM,
        ISSUED_TO,
        RECIPIENT_ID,
        BALANCE_BROUGHT,
        RECEIVED,
        ISSUED,
        BALANCE,
    ),
    signature=(0.072, 0.126, 0.103, 0.070, 0.070, 0.132,
               0.161, 0.064, 0.064, 0.064, 0.073),
    markers=("บัญชีจำหน่ายยาเสพติดให้โทษในประเภท",),
)

RYS_2 = FormLayout(
    code="ร.ย.ส. ๒/ว.จ. ๒-จ๑",
    title="รายงานเกี่ยวกับการดำเนินกิจการจำหน่ายยาเสพติดให้โทษในประเภท ๒ "
          "หรือวัตถุออกฤทธิ์ในประเภท ๒",
    columns=(
        DATE,
        GENERIC_NAME,
        TRADE_NAME,
        BATCH_NO,
        RECEIVED_FROM,
        ISSUED_TO,
        BALANCE_BROUGHT,
        RECEIVED,
        ISSUED,
        BALANCE,
        UNIT,
        REMARK,
    ),
    signature=(0.066, 0.134, 0.132, 0.070, 0.061, 0.156,
               0.062, 0.063, 0.063, 0.071, 0.051, 0.074),
    # "รายงานเกี่ยวกับ" is spelt both with and without the tone mark across the
    # sample, so the marker starts after it.
    markers=("การดำเนินกิจการจำหน่ายยาเสพติด",),
)

FORMS: tuple[FormLayout, ...] = (RVJ_7_4, BVJ_7_4_KP, RK_4, BYS_2, RYS_2)

#: Mean absolute difference per column, above which the page is not one of
#: these forms. The same form reproduces to under 0.002; the closest pair of
#: different forms is 0.012 apart, so the cut sits well clear of both.
_MAX_SIGNATURE_DISTANCE = 0.008


#: Words that only ever appear in a table's printed column titles. Used to find
#: where the header band ends on a page whose text can be read: the lowest of
#: them is the last thing above the first entry. The horizontal rules cannot say
#: this - a page may rule a note in its margin as heavily as its own table.
HEADER_WORDS: tuple[str, ...] = (
    "ยอดยกมา",
    "คงเหลือ",
    "ชื่อการค้า",
    "ได้มาจาก",
    "จำหน่ายให้",
    "ครั้งที่ผลิต",
    "ผู้รับยา",
    "ที่ทางราชการออกให้",
    "หมายเหตุ",
    "ชื่อและความแรงของ",
    "จ่ายไปให้",
    "ชื่อผู้ผลิต",
)


#: PyMuPDF returns Thai SARA AM as its two parts, so "จำหน่าย" comes out as
#: "จําหน่าย" and no literal comparison matches. Put back together before any.
_SARA_AM = ("ํา", "ำ")


def normalise(text: str) -> str:
    """Thai text as it would be typed, with the layout's spacing removed."""
    return re.sub(r"\s+", "", (text or "").replace(*_SARA_AM))


def identify_by_title(texts) -> FormLayout | None:
    """Return the form whose printed title appears in ``texts``, if any.

    Preferred over the geometry whenever the page carries its own text: a title
    is exact, while proportions can only be close, and a form the service has
    never been shown is then rejected outright rather than matched to whichever
    known layout happens to be nearest.

    The markers are deliberately the form's *title*, not its code. The code -
    "แบบ บ.ย.ส. ๒/ว.จ. ๒-จ๑" - also turns up inside the table, where one form
    cites the other, and matching on it reads a ร.ย.ส. page as a บ.ย.ส. one.
    """
    page = normalise(" ".join(texts))
    for form in FORMS:
        if any(normalise(marker) in page for marker in form.markers):
            log.debug(
                "form identified by title",
                extra={"step": "form_identify", "form": form.code},
            )
            return form
    return None


def identify(column_widths: list[float]) -> FormLayout:
    """Return the form whose column proportions best match the detected grid.

    The fallback for a page with no text of its own - every handwritten scan.
    ``column_widths`` are absolute; they are normalised here so the render dpi
    and the scanner's margins do not matter.
    """
    total = sum(column_widths)
    if total <= 0:
        raise FormNotRecognised("empty grid")
    observed = [width / total for width in column_widths]

    candidates = [form for form in FORMS if len(form.signature) == len(observed)]
    if not candidates:
        raise FormNotRecognised(
            f"no known form has {len(observed)} columns "
            f"(known: {sorted({len(f.signature) for f in FORMS})})"
        )

    scored = sorted((_distance(observed, form.signature), form) for form in candidates)
    distance, best = scored[0]
    if distance > _MAX_SIGNATURE_DISTANCE:
        raise FormNotRecognised(
            f"{len(observed)}-column page matches no known form "
            f"(closest {best.code} at {distance:.4f})"
        )

    log.debug(
        "form identified",
        extra={"step": "form_identify", "form": best.code, "distance": round(distance, 5)},
    )
    return best


def _distance(observed: list[float], signature: tuple[float, ...]) -> float:
    return sum(abs(a - b) for a, b in zip(observed, signature)) / len(signature)
