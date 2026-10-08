"""
เฟส 0.5 - สร้างไฟล์ JSON ตัวอย่างด้วยข้อมูลข่าวจริง

จุดประสงค์: ให้พี่เห็นหน้าตาไฟล์ที่เราจะส่งมอบ แล้วเริ่มงานฝั่งแมพ keyword ได้เลย
โดยไม่ต้องรอ crawler ตัวเต็มเสร็จ

ดึงแค่ 2 แหล่งและไม่กี่รายการ พอให้เห็นโครงสร้าง ไม่ใช่ตัว crawler จริง
"""

import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, pathlib, re, time, unicodedata
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from workers.crawler.net import ssl_context

BASE = ROOT
UA = "HerbRiskBot/1.0"
BKK = timezone(timedelta(hours=7))
ARTICLES_PER_SOURCE = 3


def normalize_text(s):
    """ล้างข้อความให้แมพ keyword ภาษาไทยได้ถูกต้อง (คู่มือหน้า 3)

    - NFC: ทำให้ 'ำ' ที่พิมพ์ได้ 2 แบบ (ำ เดี่ยว กับ ‌ + า) กลายเป็นรูปเดียวกัน
    - ลบ zero-width space (U+200B) กับ soft hyphen ที่เว็บไทยชอบแทรก
    - ยุบช่องว่างซ้ำและตัดหัวท้าย
    ถ้าไม่ทำขั้นนี้ เว็บไทยบางเว็บจะแมพ keyword ไม่เจอเลย
    """
    if not s:
        return s
    s = unicodedata.normalize("NFC", s)
    s = s.replace("​", "").replace("‌", "").replace("‍", "").replace("­", "")
    return re.sub(r"\s+", " ", s).strip()


def fetch(client, url, host_last_hit):
    """ยิง request แบบหน่วง 1 วิต่อ host ตามมารยาทในคู่มือ"""
    host = urlparse(url).netloc
    prev = host_last_hit.get(host)
    if prev is not None and time.monotonic() - prev < 1.0:
        time.sleep(1.0 - (time.monotonic() - prev))
    host_last_hit[host] = time.monotonic()
    return client.get(url)


def parse_feed(text, limit):
    """อ่าน RSS/Atom แล้วคืนรายการข่าว (ยังไม่มีเนื้อเต็ม)"""
    soup = BeautifulSoup(text, "xml")
    items = soup.find_all("item") or soup.find_all("entry")
    out = []
    for it in items[:limit]:
        def pick(*names):
            for n in names:
                el = it.find(n)
                if el:
                    return el.get_text(strip=True) or el.get("href") or None
            return None

        link = pick("link", "guid")
        summary_raw = pick("description", "summary", "content")
        summary = normalize_text(BeautifulSoup(summary_raw, "lxml").get_text(" ", strip=True)) if summary_raw else None
        out.append({
            "url": link,
            "title": normalize_text(pick("title")),
            "published_at": pick("pubDate", "published", "updated"),
            "summary": summary,
        })
    return out


def extract_article(html):
    """ลบส่วนที่ไม่ใช่เนื้อข่าวออก แล้วดึงข้อความ (คู่มือหน้า 3 ขั้น 2)"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["nav", "header", "footer", "script", "style", "aside", "form", "noscript"]):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body
    if body is None:
        return None
    return normalize_text(body.get_text(" ", strip=True))


def main():
    cfg = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    by_name = {s["name"]: s for s in cfg["sources"]}

    # เลือก 2 แหล่งที่ยืนยันแล้วว่าใช้ได้ และตรงกับ keyword เรื่องเรียกคืนยามากที่สุด
    picked = [by_name["US FDA - Recalls & Alerts"], by_name["UK MHRA - Drug Device Alerts"]]

    host_last_hit = {}
    sources_out = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=20.0,
                      follow_redirects=True, verify=ssl_context()) as client:
        for src in picked:
            entry = {
                "name": src["name"],
                "url": src["url"],
                "kind": src["kind"],
                "group": src["group"],
                "status": "ok",
                "error": None,
                "articles": [],
            }
            try:
                r = fetch(client, src["url"], host_last_hit)
                r.raise_for_status()
                for item in parse_feed(r.text, ARTICLES_PER_SOURCE):
                    art = dict(item)
                    art["text"] = None
                    art["text_source"] = "rss_summary"
                    art["fetched_at"] = datetime.now(BKK).isoformat(timespec="seconds")

                    # เว็บหน่วยงานรัฐ = ตามเข้าไปดึงเนื้อเต็ม
                    # เว็บข่าวเชิงพาณิชย์ = หยุดแค่ title+summary
                    if src.get("extract_full_text") and art["url"]:
                        try:
                            ar = fetch(client, art["url"], host_last_hit)
                            if ar.status_code == 200:
                                art["text"] = extract_article(ar.text)
                                art["text_source"] = "article"
                        except Exception as e:
                            art["text_error"] = f"{type(e).__name__}"
                    entry["articles"].append(art)
            except Exception as e:
                entry["status"] = "error"
                entry["error"] = f"{type(e).__name__}: {e}"
            sources_out.append(entry)

    total = sum(len(s["articles"]) for s in sources_out)
    report = {
        "run_at": datetime.now(BKK).isoformat(timespec="seconds"),
        "normalized": True,
        "_normalized_note": "title/summary/text ผ่าน NFC และลบ zero-width space แล้ว แมพ keyword ได้เลย",
        "summary": {
            "sources_total": len(sources_out),
            "ok": sum(1 for s in sources_out if s["status"] == "ok"),
            "blocked": 0,
            "error": sum(1 for s in sources_out if s["status"] == "error"),
            "articles_total": total,
        },
        "sources": sources_out,
    }

    out = OUTPUT_DIR / "sample_output.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"เขียนไฟล์ตัวอย่างที่ {out}")
    print(f"ได้ข่าว {total} ชิ้น จาก {len(sources_out)} แหล่ง")
    for s in sources_out:
        print(f"  - {s['name']}: {len(s['articles'])} ชิ้น ({s['status']})")


if __name__ == "__main__":
    main()
