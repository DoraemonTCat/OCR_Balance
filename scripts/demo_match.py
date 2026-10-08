"""
ตัวอย่างสาธิต: งานฝั่งพี่ (ไม่ใช่งานเรา)

เขียนขึ้นเพื่อพิสูจน์อย่างเดียวว่า JSON ที่เราส่งออกไป เอาไปแมพ keyword ได้จริง
ไม่ต้องแก้อะไรเพิ่ม ของจริงพี่เขาจะเขียนเองในระบบ Django + บันทึกลงตาราง KeywordSource

กติกาการจับคู่ตามคู่มือหน้า 3:
  - ไม่สนตัวพิมพ์เล็กใหญ่
  - คำอังกฤษต้องตรงทั้งคำ และนับรูปพหูพจน์ -s / -es ด้วย
  - ภาษาไทยไม่มีช่องว่างระหว่างคำ จึงจับคู่แบบ substring
"""

import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, pathlib, re, sys, glob

BASE = pathlib.Path(__file__).resolve().parent  # scripts/


def keyword_pattern(keyword, lang):
    """สร้าง regex สำหรับ keyword หนึ่งคำ"""
    if lang == "th":
        # ไทยจับแบบ substring เพราะไม่มีช่องว่างคั่นคำ
        return re.compile(re.escape(keyword), re.IGNORECASE)
    # อังกฤษ: ตรงทั้งคำ + ยอมให้ลงท้าย s หรือ es
    body = r"\s+".join(re.escape(w) for w in keyword.split())
    return re.compile(rf"\b{body}(?:es|s)?\b", re.IGNORECASE)


def build_patterns(kw):
    """1 keyword อาจได้หลาย pattern เช่น Nitrosamines ต้องจับ NDMA ด้วยถ้ามี full_form"""
    pats = [keyword_pattern(kw["keyword"], kw.get("lang", "en"))]
    if kw.get("full_form"):
        pats.append(keyword_pattern(kw["full_form"], "en"))
    return pats


def main():
    kws = json.loads((BASE / "keywords.json").read_text(encoding="utf-8"))["keywords"]
    # ไม่ระบุไฟล์ = ใช้ผลการรันล่าสุด
    path = sys.argv[1] if len(sys.argv) > 1 else sorted(
        glob.glob(str(OUTPUT_DIR / "crawl_*.json")))[-1]
    print(f"อ่านจาก {path}\n")
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))

    compiled = [(kw["keyword"], build_patterns(kw)) for kw in kws]
    rows = []

    for src in data["sources"]:
        for art in src["articles"]:
            # ค้นในทุกช่องที่มีข้อความ — ถ้า text เป็น null (เว็บข่าวเชิงพาณิชย์)
            # ก็ยังแมพจาก title กับ summary ได้
            haystack = " ".join(filter(None, [art.get("title"),
                                              art.get("summary"),
                                              art.get("text")]))
            for name, pats in compiled:
                hits = sum(len(p.findall(haystack)) for p in pats)
                if hits:
                    rows.append({
                        "keyword": name,
                        "source": src["name"],
                        "link": art["url"],
                        "title": art["title"],
                        "count": hits,
                        "text_source": art["text_source"],
                    })

    out = OUTPUT_DIR / "sample_matched.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"keyword ทั้งหมด {len(kws)} คำ  ข่าวทั้งหมด "
          f"{data['summary']['articles_total']} ชิ้น")
    print(f"จับคู่ได้ {len(rows)} แถว (1 แถว = 1 คู่ keyword กับบทความ)\n")

    by_kw = {}
    for r in rows:
        by_kw.setdefault(r["keyword"], []).append(r)
    for name in sorted(by_kw, key=lambda k: -sum(x["count"] for x in by_kw[k])):
        group = by_kw[name]
        print(f"  {name}  ->  เจอรวม {sum(x['count'] for x in group)} ครั้ง "
              f"ใน {len(group)} บทความ")
        for r in group[:2]:
            print(f"      {r['count']}x  {(r['title'] or '')[:62]}")

    print(f"\nบันทึกผลจับคู่ที่ {out}")


if __name__ == "__main__":
    main()
