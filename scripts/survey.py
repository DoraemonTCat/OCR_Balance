"""
เฟส 0 - สำรวจแหล่งข่าว (ยังไม่ใช่ตัว crawler จริง)

เช็ก 4 อย่างต่อ 1 เว็บ:
  1. robots.txt อนุญาตให้ HerbRiskBot เข้าไหม
  2. ยิง GET ได้ไหม / โดน 401-403-429 (BLOCKED) หรือ error อื่น
  3. มี RSS/Atom feed ให้ใช้แทนการแกะ HTML ไหม
  4. HTML ที่ได้มา เห็นลิงก์บทความเลย หรือต้องรอ JavaScript โหลด

มารยาทตามคู่มือหน้า 3: เช็ก robots.txt ก่อน, หน่วง 1 วิ/host, timeout 20 วิ, UA = HerbRiskBot/1.0
"""

import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, re, sys, time, threading, warnings
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import pathlib
import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from workers.crawler.net import ssl_context

# เราเลือก parser เองตาม kind อยู่แล้ว (xml สำหรับ feed, lxml สำหรับ HTML)
# เตือนซ้ำเฉพาะกรณีเว็บส่ง content-type ไม่ตรงกับ kind ที่ตั้งไว้ ซึ่งเรารายงานใน parser_mismatch แทน
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


UA = "HerbRiskBot/1.0"
UA_BROWSER = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = 20.0
HOST_DELAY = 1.0
BKK = timezone(timedelta(hours=7))

_host_lock = defaultdict(threading.Lock)
_last_hit = {}

def polite_wait(host):
    """หน่วง 1 วินาทีระหว่าง request ที่ไป host เดียวกัน"""
    with _host_lock[host]:
        prev = _last_hit.get(host)
        if prev is not None:
            gap = time.monotonic() - prev
            if gap < HOST_DELAY:
                time.sleep(HOST_DELAY - gap)
        _last_hit[host] = time.monotonic()

def check_robots(url, client):
    host = urlparse(url).netloc
    robots_url = f"{urlparse(url).scheme}://{host}/robots.txt"
    try:
        polite_wait(host)
        r = client.get(robots_url)
        if r.status_code != 200:
            return {"allowed": True, "note": f"ไม่มี robots.txt (HTTP {r.status_code}) = ถือว่าอนุญาต"}
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        allowed = rp.can_fetch(UA, url)
        delay = rp.crawl_delay(UA)
        return {"allowed": allowed, "crawl_delay": delay,
                "note": "อนุญาต" if allowed else "robots.txt ห้ามดึง URL นี้"}
    except Exception as e:
        return {"allowed": True, "note": f"อ่าน robots.txt ไม่ได้: {type(e).__name__} = ถือว่าอนุญาต"}

JS_MARKERS = [
    ("__NEXT_DATA__", "Next.js"), ("window.__NUXT__", "Nuxt"),
    ("data-reactroot", "React"), ("ng-version", "Angular"),
    ("drupal-settings-json", "Drupal (อาจโหลดผลค้นหาด้วย JS)"),
    ("__INITIAL_STATE__", "SPA state"),
]

def find_feeds(soup, base_url, html):
    feeds = []
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in " ".join(v).lower()):
        t = (link.get("type") or "").lower()
        if "rss" in t or "atom" in t or "xml" in t:
            feeds.append({"url": urljoin(base_url, link.get("href", "")),
                          "title": link.get("title") or "", "how": "autodiscovery"})
    # เผื่อเว็บไม่ประกาศใน <link> แต่มีลิงก์ feed อยู่ในหน้า
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if re.search(r"(/rss|/feed|\.rss$|\.xml$|rss\.xml|atom\.xml)", h, re.I):
            u = urljoin(base_url, h)
            if u not in [f["url"] for f in feeds]:
                feeds.append({"url": u, "title": a.get_text(" ", strip=True)[:60], "how": "ลิงก์ในหน้า"})
    return feeds[:6]

def analyse_feed(text, final_url):
    """อ่าน RSS/Atom — นับ <item> หรือ <entry> แล้วดึงตัวอย่างรายการแรกมาดู"""
    soup = BeautifulSoup(text, "xml")
    items = soup.find_all("item") or soup.find_all("entry")
    ch = soup.find("channel") or soup.find("feed")
    out = {
        "feed_bytes": len(text),
        "channel_title": (ch.find("title").get_text(strip=True)
                          if ch and ch.find("title") else None),
        "feed_format": "rss" if soup.find_all("item") else ("atom" if items else None),
        "items": len(items),
        "sample_titles": [],
        "has_summary": False,
    }
    for it in items[:3]:
        t = it.find("title")
        if t:
            out["sample_titles"].append(t.get_text(strip=True)[:90])
    if items:
        first = items[0]
        out["has_summary"] = bool(first.find("description")
                                  or first.find("summary")
                                  or first.find("content"))
    return out

def analyse_html(url, html, final_url, link_pattern=None):
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(final_url).netloc
    anchors = soup.find_all("a", href=True)

    # กติกาเดาลิงก์บทความตามคู่มือ: โดเมนเดียวกัน + path ยาวเกิน 20 ตัวอักษร
    cands, seen = [], set()
    for a in anchors:
        u = urljoin(final_url, a["href"])
        p = urlparse(u)
        if p.scheme not in ("http", "https") or p.netloc != host:
            continue
        if len(p.path) <= 20 or u in seen:
            continue
        seen.add(u)
        cands.append({"url": u, "text": a.get_text(" ", strip=True)[:80]})

    # ถ้า config ตั้ง link_pattern ไว้ ให้เช็กด้วยว่ามันคัดได้กี่ลิงก์จริง
    matched = None
    if link_pattern:
        rx = re.compile(link_pattern)
        matched = [c for c in cands if rx.search(urlparse(c["url"]).path)]

    js = [name for marker, name in JS_MARKERS if marker in html]
    res = {
        "html_bytes": len(html),
        "title": (soup.title.get_text(strip=True) if soup.title else None),
        "anchors_total": len(anchors),
        "candidate_links": len(cands),
        "sample_links": [c["url"] for c in cands[:5]],
        "sample_titles": [c["text"] for c in cands[:5] if c["text"]][:3],
        "js_framework": js,
        "feeds": find_feeds(soup, final_url, html),
    }
    if matched is not None:
        res["link_pattern"] = link_pattern
        res["links_matching_pattern"] = len(matched)
        res["sample_matched"] = [c["url"] for c in matched[:3]]
    return res

def survey_one(src, client_bot, client_browser):
    url = src["url"]
    host = urlparse(url).netloc
    kind = src.get("kind", "html")
    out = {"name": src["name"], "url": url, "group": src["group"], "host": host,
           "kind": kind, "config_status": src.get("status")}

    # เว็บที่ config บอกว่า blocked ไว้แล้ว ยังยิงซ้ำทุกรอบ เพื่อดูว่าเขาเปิดให้เข้าหรือยัง
    out["robots"] = check_robots(url, client_bot)

    try:
        polite_wait(host)
        t0 = time.monotonic()
        r = client_bot.get(url)
        out["http_status"] = r.status_code
        out["elapsed_s"] = round(time.monotonic() - t0, 2)
        out["final_url"] = str(r.url)
        out["content_type"] = r.headers.get("content-type", "")

        if r.status_code in (401, 403, 429):
            out["verdict"] = "blocked"
            # เช็กว่าบล็อกบอททุกตัว หรือบล็อกเฉพาะ UA ของเรา
            try:
                polite_wait(host)
                r2 = client_browser.get(url)
                out["browser_ua_status"] = r2.status_code
                out["blocked_reason"] = ("บล็อกเฉพาะ User-Agent ของเรา (UA ปลอมเป็น browser ผ่าน)"
                                         if r2.status_code == 200
                                         else "บล็อกทุก UA (น่าจะมีระบบกันบอท)")
            except Exception as e:
                out["blocked_reason"] = f"ลอง browser UA ไม่สำเร็จ: {type(e).__name__}"
            return out

        if r.status_code >= 400:
            out["verdict"] = "error"
            out["error"] = f"HTTP {r.status_code}"
            return out

        ctype = out["content_type"].lower()
        looks_xml = ("xml" in ctype) or r.text.lstrip()[:200].startswith("<?xml")

        if kind == "rss":
            out.update(analyse_feed(r.text, str(r.url)))
            # เว็บส่ง HTML กลับมาทั้งที่ config บอกว่าเป็น feed = feed ตายหรือ redirect ไปหน้าแรก
            if not looks_xml or out.get("items", 0) == 0:
                out["parser_mismatch"] = ("config ตั้งเป็น rss แต่ปลายทางไม่ใช่ feed ที่มีรายการ "
                                          f"(content-type: {ctype or 'ไม่ระบุ'}, items: {out.get('items', 0)})")
            out["verdict"] = "ok" if out.get("items", 0) > 0 else "error"
            if out["verdict"] == "error":
                out["error"] = "feed ว่างเปล่าหรือไม่ใช่ XML"
        else:
            out.update(analyse_html(url, r.text, str(r.url), src.get("link_pattern")))
            if looks_xml:
                out["parser_mismatch"] = "config ตั้งเป็น html แต่ปลายทางส่ง XML กลับมา"
            out["verdict"] = "ok"
    except Exception as e:
        out["verdict"] = "error"
        out["error"] = f"{type(e).__name__}: {e}"
    return out

def main():
    cfg = json.load(open(SOURCES_FILE, encoding="utf-8"))
    sources = cfg["sources"]

    hdr_bot = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
    hdr_br = {"User-Agent": UA_BROWSER, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
              "Accept-Language": "en-US,en;q=0.9"}

    with httpx.Client(headers=hdr_bot, timeout=TIMEOUT, follow_redirects=True, verify=ssl_context()) as cb, \
         httpx.Client(headers=hdr_br, timeout=TIMEOUT, follow_redirects=True, verify=ssl_context()) as cbr:
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(lambda s: survey_one(s, cb, cbr), sources))

    report = {
        "surveyed_at": datetime.now(BKK).isoformat(timespec="seconds"),
        "user_agent": UA,
        "total": len(results),
        "summary": {
            "ok": sum(1 for r in results if r["verdict"] == "ok"),
            "blocked": sum(1 for r in results if r["verdict"] == "blocked"),
            "error": sum(1 for r in results if r["verdict"] == "error"),
            "robots_disallow": sum(1 for r in results if not r["robots"]["allowed"]),
            "feed_sources_ok": sum(1 for r in results
                                   if r.get("kind") == "rss" and r["verdict"] == "ok"),
            "feed_items_total": sum(r.get("items", 0) for r in results),
            "parser_mismatch": sum(1 for r in results if r.get("parser_mismatch")),
        },
        "results": results,
    }
    path = OUTPUT_DIR / "survey_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # พิมพ์สรุปให้อ่านง่ายบนหน้าจอ
    # คอนโซล Windows ไทยใช้ cp874 ซึ่งไม่มีอักขระพิเศษหลายตัว ถ้าเจอตัวที่พิมพ์ไม่ได้ให้แทนด้วย ? แทนที่จะพัง
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    print(f"\nบันทึกผลดิบที่ {path}\n")
    print(f"{'สถานะ':<9} {'ชนิด':<5} {'ชื่อแหล่งข่าว':<34} รายละเอียด")
    print("-" * 96)
    mark = {"ok": "OK", "blocked": "BLOCK", "error": "ERROR"}
    for r in sorted(results, key=lambda x: (x["verdict"] != "ok", x["name"])):
        if r["verdict"] == "ok" and r.get("kind") == "rss":
            detail = f"{r.get('items', 0)} รายการ"
        elif r["verdict"] == "ok":
            detail = f"ลิงก์เข้าเกณฑ์ {r.get('candidate_links', 0)}"
            if "links_matching_pattern" in r:
                detail += f" / ตรง pattern {r['links_matching_pattern']}"
        elif r["verdict"] == "blocked":
            detail = f"HTTP {r.get('http_status')} - {r.get('blocked_reason', '')}"
        else:
            detail = r.get("error", "")
        # เว็บที่ยิงผ่าน (200) แต่ config ยังไม่พร้อมใช้ ให้ติดป้ายไว้ จะได้ไม่เข้าใจผิดว่าใช้ได้แล้ว
        if r["verdict"] == "ok" and r.get("config_status") != "ready":
            detail += f"   <- config: {r.get('config_status')}"
        flag = "  [!] " + r["parser_mismatch"] if r.get("parser_mismatch") else ""
        print(f"{mark[r['verdict']]:<9} {r.get('kind', ''):<5} {r['name'][:33]:<34} {detail}{flag}")

    s = report["summary"]
    print("-" * 96)
    print(f"ใช้ได้ {s['ok']} | ถูกบล็อก {s['blocked']} | ผิดพลาด {s['error']} "
          f"| robots.txt ห้าม {s['robots_disallow']} | รายการข่าวจาก feed รวม {s['feed_items_total']}")
    if s["parser_mismatch"]:
        print(f"เตือน: มี {s['parser_mismatch']} แหล่งที่ชนิดใน config ไม่ตรงกับของจริง")

if __name__ == "__main__":
    main()
