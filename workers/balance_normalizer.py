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
        # "%" is not a guess about an ambiguous shape the way "S" for 5 is: a
        # percent sign cannot occur inside a quantity on these forms at all, so
        # wherever the recogniser reports one it has misread a handwritten 8,
        # which is what it does across the sample ("2%0", "9b%", "95%").
        "$": "8", "B": "8", "%": "8",
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


def normalise_digits(text: str) -> str:
    """Replace the letters the recogniser returns for handwritten digits.

    Exposed because the date reconstruction in ``balance_parser`` has to read
    the same digits out of a cell that this module failed to parse as a date.
    """
    return (text or "").translate(_DIGIT_CONFUSIONS)


#: A quantity cell holding two figures this long, with space between them, is a
#: line the writer corrected: the old figure struck through and the new one
#: beside it. One of the two is a stray digit the recogniser boxed apart from
#: its numeral ("45 0" is 450), so both groups have to be substantial.
_FIGURE_DIGITS = 2


def holds_two_figures(text: str) -> bool:
    """True when a quantity cell holds two separate numbers rather than one.

    The writers correct a line by striking the old figure through and writing
    the new one beside it, and the recogniser returns both in one cell -
    "39 o 410" for a 390 corrected to 410. Stripping the spaces turns that into
    390410, a number nobody wrote, and it went out as if it had been read off
    the page.

    What it must not catch is a single numeral the recogniser boxed in pieces,
    which is common: "45 0" is 450 and "3 80" is 380. Those leave only one group
    of two digits or more; a corrected cell leaves two.
    """
    groups = [
        re.sub(r"\D", "", part)
        for part in re.split(r"\s+", normalise_digits(text or "").strip())
    ]
    return sum(1 for group in groups if len(group) >= _FIGURE_DIGITS) >= 2


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


#: PyMuPDF returns Thai SARA AM (ำ) as its two parts, NIKHAHIT + SARA AA, so
#: "จำนวน" comes out of a PDF as "จํานวน". It looks almost identical and
#: compares as different, which breaks every lookup and reads oddly in the
#: output, so it is put back together on the way in.
_SARA_AM = ("ํา", "ำ")


def clean_text(text: str) -> str:
    """Collapse the whitespace left by joining several boxes into one cell."""
    return re.sub(r"\s+", " ", (text or "").replace(*_SARA_AM).strip())


# --- Thai dates ------------------------------------------------------------

#: The month abbreviations as the ประเภท ๒ forms write them. Both spellings of
#: each are listed, with and without the full stops, because the forms use both
#: ("3 มค 68" on one, "3 ม.ค.68" on the other).
_THAI_MONTHS: dict[str, int] = {
    "มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6,
    "กค": 7, "สค": 8, "กย": 9, "ตค": 10, "พย": 11, "ธค": 12,
}

#: A range - "3-31 ม.ค. 68" - summarises a month of dispensing on one line. It
#: is not a date, and the agreed output carries the cell's text, so the only
#: thing needed here is to recognise one and not force it into a day.
_RANGE = re.compile(r"\d+\s*-\s*\d+")


def is_date_range(text: str) -> bool:
    """True when the cell covers a span of days rather than one."""
    return bool(_RANGE.search(text or ""))


def clean_thai_date(text: str) -> tuple[dt.date | None, bool]:
    """Parse a date written with a Thai month, such as ``"3 ม.ค.68"``.

    Returns ``(value, certain)`` like the other cleaners. A range returns
    ``(None, True)``: nothing failed to be read, there simply is no single date
    to give, and the output column carries the text either way.
    """
    raw = (text or "").strip()
    if not raw:
        return None, True
    if is_date_range(raw):
        return None, True

    compact = re.sub(r"[\s.]", "", raw)
    month = next(
        (number for name, number in _THAI_MONTHS.items() if name in compact), None
    )
    if month is None:
        return None, False

    numbers = re.findall(r"\d+", compact)
    if len(numbers) < 2:
        return None, False

    # Day first, year last: these forms write them in that order, and the month
    # between them has already been taken out of the running.
    return _build_date(numbers[0], str(month), numbers[-1]), True
