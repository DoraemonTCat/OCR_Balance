"""แปลงผลการดึงข่าวเป็นรูปแบบที่เรียงตามคำสำคัญ

ผลจาก runner.run() เรียงตามแหล่งข่าว เพราะการดึงเว็บทำทีละแหล่ง
แต่ผลลัพธ์ที่ส่งมอบเรียงตามคำสำคัญ จึงต้องพลิกตารางตรงนี้

    จาก   แหล่งข่าว -> บทความ -> ข้อความ
    เป็น  คำสำคัญ   -> แหล่งข่าว -> จำนวนครั้ง

รูปแบบผลลัพธ์:

    [
      {
        "keysword": "สารเสพติดอันตราย",
        "ref": [
          {"url": "https://hss.moph.go.th/info_act/", "count": 3},
          {"url": "https://nida.nih.gov/", "count": 0}
        ]
      }
    ]

ทุกคำจะมี ref ครบทุกแหล่ง แหล่งที่ไม่เจอได้ count 0 ไม่ได้ถูกตัดออก
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .matcher import article_text, compile_keyword, count_in
from .normalize import normalize_text


def _within(article, cutoff):
    """บทความนี้เผยแพร่หลังเส้นตัดหรือไม่

    บทความที่ไม่มีวันที่ถือว่า "ไม่รู้" จึงเก็บไว้ ไม่ตัดทิ้ง
    การตัดทิ้งจะทำให้แหล่งอย่าง CNN ที่ feed ไม่ส่งวันที่มาหายไปทั้งแหล่ง
    """
    raw = article.get("published_at")
    if not raw:
        return True
    try:
        published = datetime.fromisoformat(raw)
    except ValueError:
        return True
    if published.tzinfo is None:
        published = published.replace(tzinfo=cutoff.tzinfo)
    return published >= cutoff


def by_keyword(report, keywords, since_days=None):
    """สร้างผลลัพธ์แบบเรียงตามคำสำคัญจากผลของ runner.run()

    since_days: นับเฉพาะบทความที่เผยแพร่ภายใน N วันล่าสุด
                None = นับทุกบทความที่ดึงมาได้ (ค่าปริยาย)
    """
    compiled = [compile_keyword(k) for k in keywords]
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=since_days)

    # url ที่รายงานคือหน้าเว็บที่คนเปิดดูได้ ไม่ใช่ URL ของ feed xml
    # ซึ่ง original_url เก็บไว้อยู่แล้วสำหรับแหล่งที่เปลี่ยนไปใช้ feed
    rows = []
    for source in report["sources"]:
        display_url = source.get("original_url") or source["url"]
        articles = source.get("articles", [])
        if cutoff is not None:
            articles = [a for a in articles if _within(a, cutoff)]
        texts = [normalize_text(article_text(a)) for a in articles]
        rows.append((display_url, [t for t in texts if t]))

    return [
        {
            "keysword": name,
            "ref": [
                {"url": url, "count": sum(count_in(t, patterns) for t in texts)}
                for url, texts in rows
            ],
        }
        for name, patterns in compiled
    ]
