"""Run the real pipeline over a PDF and dump what it read, for comparison.

    python eval/run_pipeline.py eval/sample.pdf eval/out/result.json [--pages 1,2]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.sqlite")

import django  # noqa: E402

django.setup()

from workers import balance_pipeline, form_layout  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf")
    parser.add_argument("out")
    parser.add_argument("--pages", default="")
    args = parser.parse_args()

    pages = {int(p) for p in args.pages.split(",") if p.strip()} or None
    result = balance_pipeline.extract(Path(args.pdf), pages=pages)

    rows = []
    for row in result.rows:
        rows.append(
            {
                "page": row.page_number,
                "row": row.row_index,
                "form": row.form_code,
                "date": row.cells.get(form_layout.DATE, ""),
                "entry_date": row.entry_date.isoformat() if row.entry_date else None,
                "name": row.cells.get(form_layout.ISSUED_TO, ""),
                "id": row.cells.get(form_layout.RECIPIENT_ID, ""),
                "trade_name": row.cells.get(form_layout.TRADE_NAME, ""),
                "brought": row.quantities.get(form_layout.BALANCE_BROUGHT),
                "received": row.quantities.get(form_layout.RECEIVED),
                "issued": row.quantities.get(form_layout.ISSUED),
                "balance": row.quantities.get(form_layout.BALANCE),
                "needs_review": row.needs_review,
                "notes": row.review_notes,
            }
        )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "pages": [p.as_dict() for p in result.pages],
                "rows": rows,
                "processing_ms": result.processing_ms,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"{len(rows)} rows, {result.processing_ms} ms")
    for page in result.pages:
        print(page.as_dict())


if __name__ == "__main__":
    main()
