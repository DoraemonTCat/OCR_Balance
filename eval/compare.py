"""Score a pipeline run (run_pipeline.py output) against the answer key.

    python eval/compare.py eval/out/result.json [--pages 1,2] [--show 40]

Rows are aligned per page in order, matching on the ID number's digits and the
name, so one missing or extra row does not shift every row after it.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

#: The answer key to score against; OCR_KEY_DIR picks another document's.
KEY_DIR = Path(os.environ.get("OCR_KEY_DIR") or Path(__file__).resolve().parent / "key")
FIELDS = ("date", "name", "id", "brought", "received", "issued", "balance", "trade_name")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def digits(text) -> str:
    return re.sub(r"\D", "", str(text or ""))


def squash(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def load_key(page: int) -> dict:
    data = json.loads((KEY_DIR / f"p{page:02d}.json").read_text(encoding="utf-8"))
    current = ""
    for row in data["rows"]:
        # A blank date cell means the same day as the line above.
        current = row["date"] or current
        row["effective_date"] = current
        row["trade_name"] = data.get("trade_name", "")
    return data


def norm_trade(text) -> str:
    return re.sub(r"[\s.]", "", str(text or "")).lower()


def field_equal(name: str, want, got) -> bool:
    if name == "id":
        return digits(want) == digits(got)
    if name in ("brought", "received", "issued", "balance"):
        return want == got
    if name == "trade_name":
        return norm_trade(want) == norm_trade(got)
    return squash(want) == squash(got)


def align(key_rows, got_rows):
    def sig(row):
        return digits(row.get("id")) or squash(row.get("name")) or f"#{row.get('issued')}"

    matcher = difflib.SequenceMatcher(
        a=[sig(r) for r in key_rows], b=[sig(r) for r in got_rows], autojunk=False
    )
    pairs = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            pairs += [(key_rows[i], got_rows[j]) for i, j in zip(range(i1, i2), range(j1, j2))]
        else:
            # Unequal blocks: pair what lines up, the rest is missing/extra.
            n = min(i2 - i1, j2 - j1)
            pairs += [(key_rows[i1 + k], got_rows[j1 + k]) for k in range(n)]
            pairs += [(key_rows[i], None) for i in range(i1 + n, i2)]
            pairs += [(None, got_rows[j]) for j in range(j1 + n, j2)]
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result")
    parser.add_argument("--pages", default="")
    parser.add_argument("--show", type=int, default=60)
    args = parser.parse_args()

    result = json.loads(Path(args.result).read_text(encoding="utf-8"))
    run_pages = sorted({p["page"] for p in result["pages"]})
    wanted = {int(p) for p in args.pages.split(",") if p.strip()} or set(run_pages)

    totals = {f: [0, 0] for f in FIELDS}
    row_stats = {"key": 0, "got": 0, "missing": 0, "extra": 0}
    char_total = char_errors = 0
    mistakes: list[str] = []
    page_lines: list[str] = []

    for page in sorted(wanted):
        key = load_key(page)
        got = [r for r in result["rows"] if r["page"] == page]
        row_stats["key"] += len(key["rows"])
        row_stats["got"] += len(got)
        page_ok = 0
        for want, have in align(key["rows"], got):
            if want is None:
                row_stats["extra"] += 1
                mistakes.append(f"p{page} EXTRA row: {have}")
                continue
            if have is None:
                row_stats["missing"] += 1
                mistakes.append(f"p{page} MISSING row: {want}")
                for f in FIELDS:
                    totals[f][1] += 1
                continue
            all_ok = True
            for f in FIELDS:
                w = want["effective_date"] if f == "date" else want.get(f)
                h = have.get(f)
                totals[f][1] += 1
                if field_equal(f, w, h):
                    totals[f][0] += 1
                else:
                    all_ok = False
                    mistakes.append(f"p{page} {f}: want {w!r} got {h!r}")
            page_ok += all_ok
            ref, hyp = squash(want.get("name")), squash(have.get("name"))
            char_total += max(len(ref), 1)
            char_errors += _edit_distance(ref, hyp)
        page_info = next((p for p in result["pages"] if p["page"] == page), {})
        page_lines.append(
            f"p{page:02d} key {len(key['rows']):2d} got {len(got):2d} "
            f"perfect {page_ok:2d}  form={page_info.get('form')!r} {page_info.get('error') or ''}"
        )

    print("\n".join(page_lines))
    print(
        f"\nrows: key {row_stats['key']} got {row_stats['got']} "
        f"missing {row_stats['missing']} extra {row_stats['extra']}"
    )
    for f, (ok, n) in totals.items():
        print(f"  {f:11s} {ok:4d}/{n:<4d} {100 * ok / max(n, 1):6.2f}%")
    print(f"  name CER    {100 * char_errors / max(char_total, 1):6.2f}%")
    print(f"\n{len(mistakes)} mismatches")
    for line in mistakes[: args.show]:
        print("  " + line)


def _edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


if __name__ == "__main__":
    main()
