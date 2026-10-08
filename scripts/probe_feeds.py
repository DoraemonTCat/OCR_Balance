"""เฟส 0 (ต่อ) - ไล่ทดสอบ URL ที่คาดว่าเป็น RSS/Atom feed
หลายเว็บมี feed จริงแต่ไม่ประกาศใน <link rel=alternate> เลยต้องเดา URL แล้วยิงดู
"""

import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, time, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
import pathlib
import httpx
from bs4 import BeautifulSoup

from workers.crawler.net import ssl_context


UA = "HerbRiskBot/1.0"

CANDIDATES = {
  "US FDA - Recalls & Alerts": ["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/recalls/rss.xml"],
  "US FDA - MedWatch":         ["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml"],
  "US FDA - Drug Safety":      ["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drugs/rss.xml",
                                "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/drug-safety-podcasts/rss.xml"],
  "US FDA - Device Safety":    ["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medical-devices/rss.xml"],
  "US FDA - Biologics Recalls":["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/biologics/rss.xml"],
  "US FDA - Home":             ["https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml"],
  "UK MHRA - Drug Device Alerts": ["https://www.gov.uk/drug-device-alerts.atom"],
  "Health Canada - Recalls":   ["https://recalls-rappels.canada.ca/en/feed/recalls.xml",
                                "https://recalls-rappels.canada.ca/rss/recalls/en",
                                "https://recalls-rappels.canada.ca/en/rss"],
  "EMA - News Search":         ["https://www.ema.europa.eu/en/rss.xml",
                                "https://www.ema.europa.eu/en/news/rss.xml"],
  "BBC - Health":              ["https://feeds.bbci.co.uk/news/health/rss.xml"],
  "CNN - Health":              ["http://rss.cnn.com/rss/edition_health.rss"],
  "ABC News - Health":         ["https://feeds.abcnews.com/abcnews/healthheadlines"],
  "NY Times - Health":         ["https://rss.nytimes.com/services/xml/rss/nyt/Health.xml"],
  "Reuters - Healthcare":      ["https://www.reuters.com/arc/outboundfeeds/rss/category/healthcare-pharmaceuticals/?outputType=xml"],
  "NIDA":                      ["https://nida.nih.gov/news-events/news-releases/feed",
                                "https://nida.nih.gov/rss/news.xml"],
  "US DEA":                    ["https://www.dea.gov/rss/press-releases.xml", "https://www.dea.gov/node/feed"],
  "TGA - Alerts":              ["https://www.tga.gov.au/rss/alerts.xml", "https://www.tga.gov.au/news/rss.xml"],
  "PMDA Japan - English":      ["https://www.pmda.go.jp/rss_008.xml", "https://www.pmda.go.jp/rss_011.xml",
                                "https://www.pmda.go.jp/rss_012.xml", "https://www.pmda.go.jp/rss_013.xml",
                                "https://www.pmda.go.jp/rss_014.xml", "https://www.pmda.go.jp/rss_009.xml"],
  "InfoQuest - World":         ["https://www.infoquest.co.th/feed", "https://www.infoquest.co.th/world/feed"],
  "Partnership to End Addiction": ["https://drugfree.org/feed/"],
  "HSA Singapore - Announcements": ["https://www.hsa.gov.sg/rss/announcements"],
  "UNODC":                     ["https://www.unodc.org/unodc/en/frontpage/rss.xml"],
}

_lock = defaultdict(threading.Lock); _last = {}
def wait(h):
    with _lock[h]:
        p = _last.get(h)
        if p and time.monotonic()-p < 1.0: time.sleep(1.0-(time.monotonic()-p))
        _last[h] = time.monotonic()

def probe(name, url, client):
    r = {"source": name, "feed_url": url}
    try:
        wait(urlparse(url).netloc)
        resp = client.get(url)
        r["status"] = resp.status_code
        if resp.status_code != 200:
            r["ok"] = False; return r
        soup = BeautifulSoup(resp.text, "xml")
        items = soup.find_all("item") or soup.find_all("entry")
        r["ok"] = len(items) > 0
        r["items"] = len(items)
        r["kind"] = "rss" if soup.find_all("item") else ("atom" if items else None)
        if items:
            it = items[0]
            def g(*names):
                for n in names:
                    e = it.find(n)
                    if e:
                        return (e.get_text(strip=True) or e.get("href") or "")[:120]
                return None
            r["sample_title"] = g("title")
            r["sample_link"] = g("link", "guid")
            r["sample_date"] = g("pubDate", "published", "updated", "dc:date")
            r["has_description"] = bool(it.find("description") or it.find("summary") or it.find("content"))
    except Exception as e:
        r["ok"] = False; r["error"] = f"{type(e).__name__}: {e}"
    return r

jobs = [(n, u) for n, us in CANDIDATES.items() for u in us]
with httpx.Client(headers={"User-Agent": UA, "Accept": "application/rss+xml,application/xml,text/xml,*/*"},
                  timeout=25.0, follow_redirects=True, verify=ssl_context()) as c:
    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(lambda j: probe(j[0], j[1], c), jobs))

json.dump(res, open(OUTPUT_DIR / "feed_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
good = [r for r in res if r.get("ok")]
print(f"พบ feed ใช้ได้ {len(good)} / ลองทั้งหมด {len(res)}")
for r in res:
    mark = "OK " if r.get("ok") else "-- "
    print(f"{mark}{r['source'][:34]:36} {r.get('status')} items={r.get('items','-')} {r['feed_url'][:72]}")
