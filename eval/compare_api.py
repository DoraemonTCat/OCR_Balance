"""Check the API's own output - the 12 columns of `entries` - against a key.

    python eval/compare_api.py response.json KEY_DIR

`response.json` is what POST /api/v1/balance/documents returned. KEY_DIR holds
pNN.json answer keys in either of the two shapes in eval/: the sale-ledger key
(name / id, one trade_name per page) or the full-column key of eval3.

Every column is compared exactly, after collapsing runs of spaces; the ID
column is also scored on its digits alone, since how the digits are grouped is
presentation and the number is what matters.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

COLUMNS = (
    "sequence", "date", "generic_name", "trade_name", "batch_no", "received_from",
    "issued_to", "recipient_id", "balance_brought", "received", "issued", "balance",
)
NUMBERS = {"sequence", "balance_brought", "received", "issued", "balance"}


def squash(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def expected_rows(key_dir: Path) -> list[dict]:
    rows, form, count = [], None, 0
    for path in sorted(key_dir.glob("p*.json")):
        page = json.loads(path.read_text(encoding="utf-8"))
        current_date = ""
        for row in page["rows"]:
            if page["form_code"] != form:
                form, count = page["form_code"], 0
            count += 1
            current_date = row["date"] or current_date
            rows.append({
                "page": page["page"],
                "sequence": count,
                "date": current_date,
                "generic_name": row.get("generic_name", ""),
                "trade_name": row.get("trade_name", page.get("trade_name", "")),
                "batch_no": row.get("batch_no", ""),
                "received_from": row.get("received_from", ""),
                "issued_to": row.get("issued_to", row.get("name", "")),
                "recipient_id": row.get("recipient_id", row.get("id", "")),
                "balance_brought": row.get("balance_brought", row.get("brought")),
                "received": row["received"],
                "issued": row["issued"],
                "balance": row["balance"],
            })
    return rows


def main() -> None:
    response = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    got = response["entries"]
    want = expected_rows(Path(sys.argv[2]))
    print(f"document {response['document_id']}  status {response['status']}  "
          f"pages {response['page_count']}  review_count {response['review_count']}")
    keys = list(got[0].keys()) if got else []
    print(f"columns in entries: {len(keys)} -> {keys}")
    print(f"rows: key {len(want)}  got {len(got)}")

    score = {c: 0 for c in COLUMNS}
    id_digits = 0
    wrong: list[str] = []
    for w, g in zip(want, got):
        for column in COLUMNS:
            a, b = w[column], g.get(column)
            same = a == b if column in NUMBERS else squash(a) == squash(b)
            if column in NUMBERS and not same and a is None and b in ("", None):
                same = True
            score[column] += same
            if not same:
                wrong.append(f"  p{w['page']} seq {w['sequence']} {column}: want {a!r} got {b!r}")
        id_digits += re.sub(r"\D", "", str(w["recipient_id"])) == re.sub(r"\D", "", str(g.get("recipient_id") or ""))

    n = min(len(want), len(got))
    print()
    for column in COLUMNS:
        print(f"  {column:16s} {score[column]:4d}/{n}  {100 * score[column] / max(n, 1):6.2f}%")
    print(f"  {'recipient_id*':16s} {id_digits:4d}/{n}  {100 * id_digits / max(n, 1):6.2f}%   (* digits only)")
    cells = sum(score.values())
    print(f"\ncells exactly right: {cells}/{n * len(COLUMNS)} ({100 * cells / max(n * len(COLUMNS), 1):.2f}%)")
    print(f"{len(wrong)} wrong cells")
    for line in wrong[: int(sys.argv[3]) if len(sys.argv) > 3 else 60]:
        print(line)


if __name__ == "__main__":
    main()
