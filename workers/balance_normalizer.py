"""Value cleaning for the handwritten psychotropic ledgers.

Everything here exists because the input is handwriting read by a printed-text
model. The recogniser returns the right glyphs often enough to be useful but
puts spaces inside numbers ("45 0"), confuses digits with the letters that look
like them ("3$0", "l0"), and drops thin strokes such as the slashes in a date
("173169" for "1/3/69").

Each function returns the cleaned value *and* whether the cleaning was certain,
so a row that was guessed at can be flagged for review rather than silently
presented as fact.
"""
from __future__ import annotations

import datetime as dt
import re

#: Glyphs the Latin recogniser returns for handwritten digits.
_DIGIT_CONFUSIONS = str.maketrans(
    {
        "O": "0", "o": "0", "D": "0", "Q": "0",
        "l": "1", "I": "1", "i": "1", "|": "1", "!": "1",
        "z": "2", "Z": "2",
        "$": "8", "B": "8",
        "S": "5", "s": "5",
        "G": "6", "b": "6",
        "T": "7",
        "g": "9", "q": "9",
    }
)

#: A dash in a quantity cell means "none", and the writers use several.
_DASH = frozenset({"-", "–", "—", "~", "_", "=", ""})

#: Thai Buddhist years are written with their last two digits on these forms.
_BE_OFFSET = 543
#: Two-digit year window. The forms in use cover 25xx BE; 69 is 2569 BE.
_BE_CENTURY = 2500


def clean_quantity(text: str) -> tuple[int | None, bool]:
    """Parse a ยอดยกมา / รับ / จ่าย / คงเหลือ cell.

    Returns ``(value, certain)``. ``(None, True)`` is a dash, which the forms
    use for a genuine zero movement; ``(None, False)`` is text that could not be
    read as a number at all.
    """
    raw = (text or "").strip()
    if raw in _DASH:
        return None, True

    # Spaces and commas inside a number are the writer's grouping or the
    # recogniser splitting one numeral, never a separator between two values:
    # a quantity cell holds exactly one figure.
    compact = re.sub(r"[\s,.]", "", raw)
    translated = compact.translate(_DIGIT_CONFUSIONS)

    if translated.isdigit():
        # Clean only if nothing had to be substituted; otherwise it is a guess.
        return int(translated), compact.isdigit()
    return None, False


def clean_date(text: str) -> tuple[dt.date | None, bool]:
    """Parse a วัน เดือน ปี cell written as d/m/yy in the Buddhist era.

    ``16/3/69`` is 16 March 2569 BE = 2026-03-16. The separators are thin and
    often lost, so a bare run of digits is also accepted when it can only be
    split one way.
    """
    raw = (text or "").strip()
    if not raw:
        return None, True

    translated = raw.translate(_DIGIT_CONFUSIONS)
    parts = [part for part in re.split(r"[^\d]+", translated) if part]

    if len(parts) == 3:
        return _build_date(*parts), raw == translated

    digits = "".join(parts)
    guessed = _split_digits(digits)
    if guessed is None:
        return None, False
    return _build_date(*guessed), False


def _split_digits(digits: str) -> tuple[str, str, str] | None:
    """Recover d/m/yy from a run of digits whose separators were not read.

    Only lengths that split one way are accepted. ``"1369"`` is 1/3/69 and
    ``"16369"`` is 16/3/69, but five digits could also be 1/63/69, so the month
    has to land in 1-12 for a split to be used, and an ambiguous run is
    rejected rather than guessed.
    """
    candidates = []
    for day_len in (1, 2):
        for month_len in (1, 2):
            if day_len + month_len + 2 != len(digits):
                continue
            day = digits[:day_len]
            month = digits[day_len : day_len + month_len]
            year = digits[day_len + month_len :]
            if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
                candidates.append((day, month, year))
    return candidates[0] if len(candidates) == 1 else None


def _build_date(day: str, month: str, year: str) -> dt.date | None:
    try:
        day_n, month_n, year_n = int(day), int(month), int(year)
    except ValueError:
        return None

    if len(year) <= 2:
        year_n += _BE_CENTURY
    if year_n > 2400:  # a Buddhist-era year; the forms never use CE
        year_n -= _BE_OFFSET

    try:
        return dt.date(year_n, month_n, day_n)
    except ValueError:
        return None


#: Collapses the space the recogniser inserts between a batch number's letter
#: prefix and its digits: "T 25 275" is one code.
_BATCH_RE = re.compile(r"^([A-Za-z]{0,3})[\s.]*([\d\s]+)$")


def clean_batch_no(text: str) -> tuple[str, bool]:
    """Normalise a เลขที่/รุ่นที่/ครั้งที่ผลิต cell such as ``"T 25 275"``."""
    raw = (text or "").strip()
    if not raw:
        return "", True

    match = _BATCH_RE.match(raw)
    if not match:
        return raw, False

    prefix, digits = match.groups()
    return prefix.upper() + re.sub(r"\s+", "", digits), True


def clean_text(text: str) -> str:
    """Collapse the whitespace left by joining several OCR boxes into one cell."""
    return re.sub(r"\s+", " ", (text or "").strip())


#: Latin letters, digits and the punctuation a drug name uses. A cell made
#: mostly of anything else is the Thai recogniser failing, not content.
_READABLE = re.compile(r"[A-Za-z0-9]")


def looks_unreadable(text: str) -> bool:
    """True when a cell is mostly symbols rather than letters and digits.

    This catches stray ink and ruling picked up as text. It does **not** detect
    a Thai cell read by the Latin model: that comes back as plausible Latin
    letters, so no test on the string can recognise it. Thai cells are flagged
    by which column they came from - see ``form_layout.THAI_ROLES``.
    """
    stripped = re.sub(r"\s", "", text or "")
    if not stripped:
        return False
    readable = len(_READABLE.findall(stripped))
    return readable / len(stripped) < 0.5
