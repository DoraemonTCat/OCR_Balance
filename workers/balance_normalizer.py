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
import difflib
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

    # The unit is written under the figure in the same column on the ประเภท ๒
    # forms - "2,400" with "เม็ด" below it - so a quantity cell can hold a word
    # as well as a number. Only a Thai word is dropped: that is a unit, and a
    # cell is not unreadable because it says what it counts. A Latin one is the
    # recogniser failing, and has to keep the cell unreadable.
    #
    # Glyphs are substituted before this, not after: "9 9 b" is 996 with a
    # misread 6, and testing for digits first would throw the 6 away as a word.
    words = [
        word
        for word in re.split(r"\s+", raw.translate(_DIGIT_CONFUSIONS))
        if word and not _is_unit(word)
    ]
    if not any(any(char.isdigit() for char in word) for word in words):
        return None, False

    # Spaces and commas *inside* a number are the writer's grouping or the
    # recogniser splitting one numeral, never a separator between two values:
    # a quantity cell holds exactly one figure.
    compact = re.sub(r"[\s,.]", "", "".join(words))
    translated = compact

    if translated.isdigit():
        # Certain only if nothing had to be substituted to get there. Compared
        # on the words that were kept, so dropping a unit does not make the
        # figure beside it a guess.
        untouched = re.sub(
            r"[\s,.]",
            "",
            "".join(word for word in re.split(r"\s+", raw) if not _is_unit(word)),
        )
        return int(translated), untouched.isdigit()
    return None, False


#: A word with no digits in it is the unit written beside the figure - these
#: forms put "เม็ด" or "ขวด" in the quantity column - as long as it is not
#: written in Latin script. Latin letters there are the recogniser failing.
_LATIN = re.compile(r"[A-Za-z]")


def _is_unit(word: str) -> bool:
    """True for a word that is neither a figure nor the recogniser failing."""
    return not any(char.isdigit() for char in word) and not _LATIN.search(word)


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

    compact = re.sub(r"[\s.]", "", raw).replace(*_SARA_AM)
    month, exact = _thai_month(compact)
    if month is None:
        return None, False

    numbers = re.findall(r"\d+", compact)
    if len(numbers) < 2:
        return None, False

    # Day first, year last: these forms write them in that order, and the month
    # between them has already been taken out of the running.
    return _build_date(numbers[0], str(month), numbers[-1]), exact


#: The months written out in full, as the pharmacy ledgers type them
#: ("15 มกราคม 2567").
_THAI_MONTH_NAMES: dict[str, int] = {
    "มกราคม": 1, "กุมภาพันธ์": 2, "มีนาคม": 3, "เมษายน": 4, "พฤษภาคม": 5,
    "มิถุนายน": 6, "กรกฎาคม": 7, "สิงหาคม": 8, "กันยายน": 9, "ตุลาคม": 10,
    "พฤศจิกายน": 11, "ธันวาคม": 12,
}

#: How close a misread full month name has to be to one of the twelve. The
#: recogniser drops or swaps a vowel mark now and then ("กุมภาพันธ" for
#: "กุมภาพันธ์"); the nearest two different months are far further apart.
_MONTH_SIMILARITY = 0.75


def thai_date_text(raw: str, value: dt.date) -> str:
    """``value`` written as ``raw`` wrote it: full month name or abbreviation,
    and the Buddhist-era year with as many digits as the form gave."""
    numbers = re.findall(r"\d+", re.sub(r"\s", "", raw or ""))
    year_digits = numbers[-1] if numbers else ""
    year = value.year + 543
    shown = str(year) if len(year_digits) != 2 else f"{year % 100:02d}"
    letters = re.sub(r"[^ก-๎]", "", (raw or "").replace(*_SARA_AM))
    if len(letters) >= 4:  # a month written out, not an abbreviation
        month = next(n for n, m in _THAI_MONTH_NAMES.items() if m == value.month)
        return f"{value.day} {month} {shown}"
    return clean_text(raw)


def _thai_month(compact: str) -> tuple[int | None, bool]:
    """The month named in ``compact``, and whether it was spelt exactly."""
    letters = re.sub(r"[^฀-๿]", "", compact)
    for name, number in _THAI_MONTH_NAMES.items():
        if name in letters:
            return number, True
    for name, number in _THAI_MONTHS.items():
        if name in compact:
            return number, True
    if len(letters) >= 4:
        scored = max(
            (difflib.SequenceMatcher(None, letters, name).ratio(), number)
            for name, number in _THAI_MONTH_NAMES.items()
        )
        if scored[0] >= _MONTH_SIMILARITY:
            # One mark out of place ("กุมภาพันธุ์") still names one month
            # beyond doubt; only a looser match is reported as a guess.
            return scored[1], scored[0] >= _MONTH_CERTAIN
    return None, False


#: A misread month this close to a real one is taken as read, not guessed.
#: "กุมภาพันธุ์" against "กุมภาพันธ์" scores 0.95; the closest two different
#: months ("มกราคม", "มีนาคม") 0.67.
_MONTH_CERTAIN = 0.85


# --- Thai ID numbers -------------------------------------------------------


def thai_id_text(text: str) -> str:
    """A 13-digit Thai ID written the way the card prints it: 1 2345 67890 12 3.

    The recogniser reads the digits of an ID well but splits it wherever the
    glyphs of a "1" leave a gap - "3 3301 01 1 15 41 7" - so a cell holding the
    thirteen digits in groups is regrouped. Written as one run ("3440600130976",
    as the handwritten forms do) or with anything more in it ("เลขที่ ปชช
    1112223334440", a passport number), it is left as written.
    """
    # A mark standing on its own ("|", ".") is the edge of a rule, not text.
    cleaned = clean_text(_LONE_MARK.sub(" ", text or ""))
    digits = re.sub(r"\s", "", cleaned)
    if len(digits) != 13 or not digits.isdigit() or digits == cleaned:
        return cleaned
    return f"{digits[0]} {digits[1:5]} {digits[5:10]} {digits[10:12]} {digits[12]}"


#: A run of punctuation with whitespace (or the cell's edge) on both sides.
_LONE_MARK = re.compile(r"(?<!\S)[|.,:;'`_\-]+(?!\S)")
