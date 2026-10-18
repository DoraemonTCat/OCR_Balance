"""Reading a buyer's name - in Thai, where a single mark changes the name.

A name is the one cell on the sale ledgers that nothing else on the page can
check: a figure has the running balance, an ID has its thirteen digits, a date
has the rows around it. And it is the cell the recognisers find hardest. At
200 dpi the scan leaves a few pixels between ช and ซ, ด and ต, ิ and ี, and each
engine settles them its own way.

So a name is read several times and the readings are combined:

1. **Readings.** PaddleOCR on the crop as cut and scaled to its training
   height, and Tesseract with its accurate model, its fast model, and on the
   crop doubled in size. Five readings that are mostly right and wrong in
   different places.
2. **Vote.** The readings are aligned character by character against the most
   reliable one and each position takes the majority (``rover``).
3. **Spelling.** Where the vote leaves a letter from a look-alike set, the
   alternatives are scored against how Thai names and words are actually
   spelt - a character model built from PyThaiNLP's lists of Thai given names,
   family names and words - together with how many readings saw each one.
   "ชลธิตา" and "ชลธิดา" look the same on this scan; only the second is a name.
4. **Titles.** The title a name starts with comes from a short closed list
   (นาย, นาง, นางสาว, น.ส., เด็กชาย, ...), so a misread one is put right.

Without Tesseract installed, step 1 is PaddleOCR's two readings alone and the
rest still applies.
"""
from __future__ import annotations

import collections
import difflib
import functools
import itertools
import logging
import math
import re
from typing import Callable, Sequence

log = logging.getLogger(__name__)

#: Titles a name can start with, longest first so "นางสาว" wins over "นาง".
TITLES: tuple[str, ...] = (
    "เด็กหญิง", "เด็กชาย", "นางสาว", "น.ส.", "ด.ช.", "ด.ญ.", "นาย", "นาง",
)

#: Abbreviated titles by their letters alone, for a reading that lost the stops.
_BARE_ABBREVIATIONS = {"นส": "น.ส.", "ดช": "ด.ช.", "ดญ": "ด.ญ."}

#: Letters this typeface and this scan make look alike. Only these are ever
#: swapped by the spelling step; anything else is left as the vote decided.
LOOK_ALIKE: tuple[frozenset[str], ...] = (
    frozenset("ชซข"),
    frozenset("ดต"),
    frozenset("ใไ"),
    frozenset("บป"),
    frozenset("ิี"),  # ิ ี
    frozenset("ั้"),  # ั ้
)

#: Weight of the vote against the spelling model, per reading that agrees.
#: Kept low: where the scan makes two letters look alike, every engine tends
#: to make the same mistake, so their agreement is weak evidence there.
#: Tuned on the 452 names of ต.ย. OCR.pdf.
_VOTE_WEIGHT = 0.1
#: Bonus for a part of a name that is a known name or word outright.
_KNOWN_BONUS = 0.5

#: A name fewer than this share of the readings agree on, as finally settled,
#: is flagged for review. On ต.ย. OCR.pdf four in five catches 32 of the 36
#: names still wrong, and the names it leaves unflagged are 98.7% right.
REVIEW_AGREEMENT = 0.8

#: Punctuation that is never part of a name: what is left of a speck or of the
#: rule the recogniser read as a mark.
_PUNCTUATION = re.compile(r"[.\-,:;'\"_|/!?`’]+")
#: An abbreviated title or rank - น.ส., ส.ต.อ., พ.ต.ท. - whose full stops stay.
_ABBREVIATION = re.compile(r"(?:[\u0E01-\u0E2E]{1,3}\.)+")
#: Beyond this many spellings to try, a part is left as voted.
_MAX_SPELLINGS = 256

Reader = Callable[[Sequence], list[str]]


def read(crops, readers: Sequence[tuple[Reader, float]]) -> list[tuple[str, float]]:
    """Read every crop with every reader and combine the readings per crop.

    ``readers`` are (reader, weight) pairs, the most reliable first; a reader
    that fails is skipped rather than failing the page.

    Returns (name, agreement) per crop: the share of the readings that came
    out exactly as the name finally settled on. See ``REVIEW_AGREEMENT``.
    """
    readings: list[list[str]] = []
    weights: list[float] = []
    for reader, weight in readers:
        try:
            texts = reader(crops)
        except Exception:  # pragma: no cover - a second opinion is optional
            log.warning("name reader failed", exc_info=True)
            continue
        readings.append([clean(text) for text in texts])
        weights.append(weight)
    if not readings:
        return [("", 0.0)] * len(crops)

    names = []
    for index in range(len(crops)):
        candidates = [reading[index] for reading in readings]
        voted = _drop_punctuation(fix_title(rover(candidates, weights)))
        name = respell(voted, candidates)
        agreement = sum(candidate == name for candidate in candidates) / len(candidates)
        names.append((name, agreement))
    return names


def clean(text: str) -> str:
    """Normalise one reading: Thai SARA AM, spacing, and stray trailing marks."""
    text = (text or "").replace("ํา", "ำ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[^ก-๎]+$", "", text).strip()
    return _drop_punctuation(fix_title(text))


def _drop_punctuation(name: str) -> str:
    """Remove stray punctuation, keeping the full stops of abbreviations."""
    kept = []
    for word in name.split(" "):
        if _ABBREVIATION.fullmatch(word):
            kept.append(word)
            continue
        title = next(
            (t for t in TITLES if word.startswith(t) and _ABBREVIATION.fullmatch(t)), ""
        )
        word = title + _PUNCTUATION.sub("", word[len(title) :])
        if word:
            kept.append(word)
    return " ".join(kept)


def fix_title(text: str) -> str:
    """Put right a title that was read with a letter or two wrong.

    A long title (นางสาว, เด็กชาย) is distinctive enough to match with two
    letters wrong, or one more or fewer. A short one is not - นาค, นาวิน are
    names - so นาย and นาง are only restored when the misreading stands alone
    as a word, the way the ledgers type them.
    """
    first, _, rest = text.partition(" ")
    bare = _BARE_ABBREVIATIONS.get(first.replace(".", "").replace(",", ""))
    if bare and first != bare and rest:
        # "นส วาริณี": the full stops of an abbreviated title were lost.
        return f"{bare} {rest}"
    if any(text.startswith(title) for title in TITLES):
        # Already a title, written out: "นางนวล" is นาง + นวล, not a misread
        # นางสาว.
        return text
    best = None
    for title in TITLES:
        if len(title) >= 5:
            allowed, lengths = 2, {len(title) - 1, len(title), len(title) + 1}
        else:
            allowed, lengths = 1, {len(title)}
        for length in lengths:
            if length > len(text):
                continue
            if len(title) < 5 and len(text) > length and text[length] != " ":
                continue
            distance = _distance(text[:length], title)
            if distance <= allowed and (best is None or distance < best[0]):
                best = (distance, title, length)
    if best and best[0] > 0:
        return best[1] + text[best[2] :]
    return text


def rover(candidates: Sequence[str], weights: Sequence[float]) -> str:
    """Character-level weighted vote, aligned against the first candidate."""
    base = candidates[0]
    slots = [collections.Counter() for _ in base]
    gaps = [collections.Counter() for _ in range(len(base) + 1)]
    for candidate, weight in zip(candidates, weights):
        matcher = difflib.SequenceMatcher(None, base, candidate, autojunk=False)
        inserted = set()
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
                for k in range(i2 - i1):
                    slots[i1 + k][candidate[j1 + k]] += weight
            else:
                for k in range(i1, i2):
                    slots[k][""] += weight
                if j2 > j1:
                    gaps[i2 if tag == "replace" else i1][candidate[j1:j2]] += weight
                    inserted.add(i2 if tag == "replace" else i1)
        for k in range(len(base) + 1):
            if k not in inserted:
                gaps[k][""] += weight
    out = []
    for k in range(len(base) + 1):
        out.append(gaps[k].most_common(1)[0][0])
        if k < len(base):
            out.append(slots[k].most_common(1)[0][0])
    return "".join(out)


def respell(name: str, readings: Sequence[str]) -> str:
    """Settle look-alike letters by how Thai names are spelt."""
    model = _model()
    if model is None:
        return name
    parts = []
    for part in name.split(" "):
        title = next((t for t in TITLES if part.startswith(t)), "")
        body = part[len(title) :]
        if len(body) < 2:
            parts.append(part)
            continue

        def score(spelling: str) -> float:
            agreeing = sum(
                any(token.endswith(spelling) for token in reading.split(" "))
                for reading in readings
            )
            known = _KNOWN_BONUS if spelling in model.known else 0.0
            return model.per_char(spelling) + _VOTE_WEIGHT * agreeing + known

        parts.append(title + max(_spellings(body), key=score))
    return " ".join(parts)


def _spellings(body: str) -> list[str]:
    options = []
    for char in body:
        group = next((g for g in LOOK_ALIKE if char in g), None)
        options.append(sorted(group) if group else [char])
    if math.prod(len(o) for o in options) > _MAX_SPELLINGS:
        return [body]
    return ["".join(choice) for choice in itertools.product(*options)]


class _CharModel:
    """An interpolated character 4-gram model over Thai names and words."""

    ORDER = 4
    LAMBDAS = (0.05, 0.15, 0.3, 0.5)
    VOCABULARY = 80

    def __init__(self, names: set[str], words: set[str]) -> None:
        self.known = names | words
        self.grams: collections.Counter = collections.Counter()
        self.context: collections.Counter = collections.Counter()
        # Names count three times over: they are what this model is for.
        for entry, times in itertools.chain(
            ((n, 3) for n in names), ((w, 1) for w in words)
        ):
            padded = "^" * (self.ORDER - 1) + entry + "$"
            for i in range(self.ORDER - 1, len(padded)):
                for n in range(1, self.ORDER + 1):
                    gram = padded[i - n + 1 : i + 1]
                    self.grams[gram] += times
                    self.context[gram[:-1]] += times

    def per_char(self, text: str) -> float:
        padded = "^" * (self.ORDER - 1) + text + "$"
        total = 0.0
        for i in range(self.ORDER - 1, len(padded)):
            p = 0.0
            for n, weight in zip(range(1, self.ORDER + 1), self.LAMBDAS):
                gram = padded[i - n + 1 : i + 1]
                p += weight * (self.grams[gram] + 0.01) / (
                    self.context[gram[:-1]] + 0.01 * self.VOCABULARY
                )
            total += math.log(p)
        return total / max(len(text), 1)


@functools.lru_cache(maxsize=1)
def _model() -> _CharModel | None:
    try:
        from pythainlp.corpus import (
            thai_family_names,
            thai_female_names,
            thai_male_names,
            thai_words,
        )
    except ImportError:  # pragma: no cover - optional
        log.warning("pythainlp is not installed; names are not respelt")
        return None
    names = set(thai_family_names()) | set(thai_female_names()) | set(thai_male_names())
    return _CharModel(names, set(thai_words()))


def _distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]
