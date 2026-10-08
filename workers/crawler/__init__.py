"""ชั้นดึงข่าวจากเว็บ — ไม่ยุ่งกับฐานข้อมูลและไม่รู้จัก Django

ดึงข่าวจากแหล่งที่ตั้งไว้ใน sources.json แล้วคืนข้อความที่ล้างแล้ว
การนับคำสำคัญและการบันทึกฐานข้อมูลเป็นหน้าที่ของชั้นบน

ใช้งาน:
    from workers.crawler import load_sources, run

    _, sources = load_sources()
    report = run(sources)

หรือดึงทีละแหล่งเมื่อต้องการคุมลำดับเอง:
    from workers.crawler import Fetcher, crawl_source

    with Fetcher() as fetcher:
        result = crawl_source(source_dict, fetcher)

สั่งรันจากบรรทัดคำสั่ง:
    python -m workers.crawler.cli
"""
from .crawler import (BlockedError, Fetcher, crawl_source, extract_article,
                      extract_links, extract_title, parse_feed)
from .normalize import normalize_text, normalize_thai
from .runner import Canceller, load_sources, run

__all__ = [
    "BlockedError",
    "Canceller",
    "Fetcher",
    "crawl_source",
    "extract_article",
    "extract_links",
    "extract_title",
    "load_sources",
    "normalize_text",
    "normalize_thai",
    "parse_feed",
    "run",
]
