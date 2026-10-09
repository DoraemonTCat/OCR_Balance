"""คุมการรันหนึ่งรอบ — ดึงทุกแหล่งข่าวพร้อมกัน แล้วรวมผลเป็น dict เดียว

ยังไม่แตะฐานข้อมูลเหมือนกัน หน้าที่เดียวคือประกอบผลจาก crawler.py
ฝั่ง backend เรียก run() แล้วเอา dict ที่ได้ไปบันทึกเอง
"""
from __future__ import annotations

import json
import pathlib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .crawler import Fetcher, crawl_source

BASE = pathlib.Path(__file__).resolve().parent     # workers/crawler/
PROJECT_ROOT = BASE.parents[1]                     # รากของโปรเจ็กต์
OUTPUT_DIR = PROJECT_ROOT / "data" / "crawl"       # ที่เก็บไฟล์ผลลัพธ์

#: ดึงหลายเว็บพร้อมกันได้ แต่ Fetcher ยังบังคับหน่วง 1 วิต่อ host อยู่
#: ตัวเลขนี้จึงคุมแค่ว่า "กี่เว็บพร้อมกัน" ไม่ได้ทำให้ยิงเว็บเดียวถี่ขึ้น
MAX_WORKERS = 6


#: ฟิลด์ที่ source หนึ่งรายการต้องมี
REQUIRED_FIELDS = ("name", "url")

#: ฟิลด์ที่ใส่หรือไม่ใส่ก็ได้ พร้อมค่าที่ใช้เมื่อไม่ได้ส่งมา
OPTIONAL_FIELDS = {
    "kind": "html",              # "html" หรือ "rss"
    "link_pattern": None,        # regex คัดลิงก์บทความ ใช้เฉพาะ kind=html
    "max_articles": 30,
    "max_pages": 3,              # ไล่อ่านหน้าถัดไปได้กี่หน้า รวมหน้าแรก
    "extract_full_text": False,  # True = ตามเข้าไปดึงเนื้อข่าวเต็ม
    "group": None,               # ป้ายกำกับ ไม่มีผลต่อการทำงาน
    "original_url": None,        # URL หน้าเว็บที่คนเปิดดู ใช้รายงานในผลลัพธ์
}


def normalize_source(source):
    """ตรวจและเติมค่าปริยายให้ source หนึ่งรายการ

    ใช้ตอนรับข้อมูลจากภายนอก เช่นแถวที่อ่านมาจากฐานข้อมูล
    เพื่อให้ได้ข้อผิดพลาดที่อ่านรู้เรื่องตั้งแต่ก่อนเริ่มยิงเว็บ
    แทนที่จะไปพังกลางทางด้วย KeyError
    """
    missing = [f for f in REQUIRED_FIELDS if not source.get(f)]
    if missing:
        raise ValueError(
            f"source ขาดฟิลด์ที่จำเป็น: {', '.join(missing)} — ได้รับ {sorted(source)}")

    kind = source.get("kind") or "html"
    if kind not in ("html", "rss"):
        raise ValueError(f"{source['name']}: kind ต้องเป็น 'html' หรือ 'rss' ไม่ใช่ {kind!r}")

    merged = {**OPTIONAL_FIELDS, **{k: v for k, v in source.items() if v is not None}}
    merged["kind"] = kind
    return merged


def load_keywords(path=None):
    """อ่านคำสำคัญจาก keywords.json

    เหมือน sources.json ไฟล์นี้เป็น *ข้อมูลตั้งต้น* สำหรับทดลองรันและเทสต์
    เมื่อใช้งานจริงให้อ่านคำสำคัญจากฐานข้อมูลแล้วส่ง list เข้า by_keyword() โดยตรง

    รับได้ทั้งไฟล์ที่เป็น list ของสตริง และไฟล์ที่เป็น object ที่มีคีย์ keywords
    """
    path = pathlib.Path(path) if path else BASE / "keywords.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    return config["keywords"] if isinstance(config, dict) else config


def load_sources(path=None, statuses=("ready",)):
    """อ่านแหล่งข่าวจาก sources.json

    ไฟล์นี้เป็น *ข้อมูลตั้งต้น* สำหรับทดลองรันและเทสต์เท่านั้น
    เมื่อใช้งานจริง ผู้เรียกควรอ่านแหล่งข่าวจากฐานข้อมูลของตัวเอง
    แล้วส่ง list เข้า run() โดยตรง ดู docs/NEWS_CRAWLER.md หัวข้อ "สัญญาข้อมูล"

    statuses: กรองตามสถานะจากผลสำรวจ — ค่าปริยายเอาเฉพาะ ready
              ส่ง None เพื่อเอาทั้งหมด
    """
    path = pathlib.Path(path) if path else BASE / "sources.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    sources = config["sources"]
    if statuses is not None:
        sources = [s for s in sources if s.get("status") in statuses]
    return config.get("defaults", {}), sources


class Canceller:
    """ให้ผู้ใช้สั่งยกเลิกกลางรอบได้ (คู่มือหน้า 4)

    crawler จะเรียก is_set() ก่อนดึงบทความแต่ละชิ้น จึงหยุดได้โดยไม่ต้องรอจนจบ
    """

    def __init__(self):
        self._event = threading.Event()

    def cancel(self):
        self._event.set()

    def is_set(self):
        return self._event.is_set()


def run(sources=None, *, max_articles=None, max_pages=None, canceller=None,
        respect_robots=True):
    """ดึงทุกแหล่งข่าวหนึ่งรอบ คืน dict ตามรูปแบบที่ตกลงกับฝั่ง backend

    รอบถือว่า SUCCESS เมื่อมีอย่างน้อยหนึ่งแหล่งสำเร็จ ไม่มีเลย = FAILED
    (คู่มือหน้า 3 ขั้น 4)
    """
    if sources is None:
        _, sources = load_sources()
    # ตรวจก่อนยิงเว็บ ผิดตรงไหนจะรู้ทันทีพร้อมชื่อแหล่งข่าว
    sources = [normalize_source(s) for s in sources]
    stop = canceller.is_set if canceller else None

    with Fetcher(respect_robots=respect_robots) as fetcher:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            results = list(pool.map(
                lambda s: crawl_source(s, fetcher, should_stop=stop,
                                       max_articles=max_articles, max_pages=max_pages),
                sources,
            ))

    counts = {"ok": 0, "blocked": 0, "error": 0}
    for item in results:
        counts[item["status"]] += 1
    articles = sum(len(r["articles"]) for r in results)
    pages = sum(r.get("pages_read", 0) for r in results)
    with_text = sum(1 for r in results for a in r["articles"] if a.get("text"))

    return {
        "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "normalized": True,
        "_normalized_note": ("title/summary/text ผ่าน NFC, รวม 'ํา' กับ 'ำ' "
                             "และลบ zero-width space แล้ว แมพ keyword ได้เลย"),
        "status": "SUCCESS" if counts["ok"] else "FAILED",
        "cancelled": any(r["cancelled"] for r in results),
        "summary": {
            "sources_total": len(results),
            **counts,
            "articles_total": articles,
            "articles_with_full_text": with_text,
            "pages_read": pages,
        },
        "sources": results,
    }
