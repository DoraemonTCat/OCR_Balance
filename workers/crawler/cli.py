"""สั่งรันหนึ่งรอบจากบรรทัดคำสั่ง แล้วเขียนผลเป็นไฟล์ JSON

ส่วนนี้เป็นเปลือกบาง ๆ เท่านั้น ฝั่ง backend ที่อยาก import ไปใช้ตรง ๆ
ให้เรียก runner.run() แทน ไม่ต้องผ่านไฟล์

ตัวอย่าง:
    python -m workers.crawler.cli                          ดึงทุกแหล่งที่สถานะ ready
    python -m workers.crawler.cli --max-articles 5         จำกัดบทความต่อแหล่ง (ไว้ลองเร็ว ๆ)
    python -m workers.crawler.cli --only "US FDA"          เฉพาะแหล่งที่ชื่อมีคำนี้
    python -m workers.crawler.cli --out ของผม.json          ตั้งชื่อไฟล์ผลลัพธ์เอง
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import datetime

from .runner import OUTPUT_DIR, load_sources, run


def main(argv=None):
    parser = argparse.ArgumentParser(description="ดึงข่าวจากแหล่งที่ตั้งไว้ แล้วเขียนเป็น JSON")
    parser.add_argument("--out", help="ไฟล์ผลลัพธ์ (ค่าปริยาย output/crawl_<เวลา>.json)")
    parser.add_argument("--max-articles", type=int, help="จำกัดจำนวนบทความต่อแหล่ง")
    parser.add_argument("--only", help="ดึงเฉพาะแหล่งที่ชื่อมีข้อความนี้")
    parser.add_argument("--status", default="ready",
                        help="สถานะที่จะดึง คั่นด้วยจุลภาค หรือ all (ค่าปริยาย ready)")
    parser.add_argument("--no-robots", action="store_true",
                        help="ข้ามการตรวจ robots.txt (ใช้ตอนทดสอบเท่านั้น ห้ามใช้จริง)")
    args = parser.parse_args(argv)

    statuses = None if args.status == "all" else tuple(s.strip() for s in args.status.split(","))
    _, sources = load_sources(statuses=statuses)
    if args.only:
        sources = [s for s in sources if args.only.lower() in s["name"].lower()]

    if not sources:
        print("ไม่มีแหล่งข่าวที่ตรงเงื่อนไข", file=sys.stderr)
        return 1

    print(f"เริ่มดึง {len(sources)} แหล่ง...")
    report = run(sources, max_articles=args.max_articles,
                 respect_robots=not args.no_robots)

    out = pathlib.Path(args.out) if args.out else (
        OUTPUT_DIR / f"crawl_{datetime.now().strftime('%Y%m%dT%H%M')}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        sys.stdout.reconfigure(errors="replace")   # คอนโซลไทยเป็น cp874 พิมพ์บางอักขระไม่ได้
    except Exception:
        pass

    s = report["summary"]
    print()
    print(f"{'สถานะ':<9} {'ชนิด':<5} {'แหล่งข่าว':<34} บทความ")
    print("-" * 82)
    for item in sorted(report["sources"], key=lambda x: (x["status"] != "ok", x["name"])):
        detail = str(len(item["articles"]))
        if item["status"] != "ok":
            detail = item["error"] or ""
        print(f"{item['status'].upper():<9} {item['kind']:<5} {item['name'][:33]:<34} {detail}")
    print("-" * 82)
    print(f"รอบนี้ {report['status']} | ใช้ได้ {s['ok']} | ถูกบล็อก {s['blocked']} "
          f"| ผิดพลาด {s['error']} | บทความรวม {s['articles_total']} "
          f"(มีเนื้อเต็ม {s['articles_with_full_text']})")
    print(f"\nเขียนผลที่ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
