
import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, httpx
from bs4 import BeautifulSoup

from workers.crawler.net import ssl_context
UA="HerbRiskBot/1.0"
BR=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TRY=[
 ("FDA RSS index","https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds",UA),
 ("FDA device safety a","https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medical-device-safety/rss.xml",UA),
 ("CNN health b","http://rss.cnn.com/rss/cnn_health.rss",UA),
 ("CNN health c","https://rss.cnn.com/rss/edition_health.rss",BR),
 ("NIDA news","https://nida.nih.gov/news-events/news-releases",UA),
 ("drugfree feed raw","https://drugfree.org/feed/",UA),
 ("TGA alerts browserUA","https://www.tga.gov.au/resources/alert",BR),
 ("TGA safety updates","https://www.tga.gov.au/news/safety-updates",BR),
 ("HealthCanada recalls p2","https://recalls-rappels.canada.ca/en/search/site?f%5B0%5D=category%3A108",UA),
 ("EMA news page","https://www.ema.europa.eu/en/news-events/rss-feeds",UA),
]
for name,url,ua in TRY:
    try:
        with httpx.Client(headers={"User-Agent":ua},timeout=30.0,follow_redirects=True,verify=ssl_context()) as c:
            r=c.get(url)
        info=f"{r.status_code} {len(r.text)}b"
        if r.status_code==200:
            if "xml" in (r.headers.get("content-type","")) or r.text.lstrip().startswith("<?xml"):
                s=BeautifulSoup(r.text,"xml"); n=len(s.find_all("item") or s.find_all("entry"))
                info+=f" FEED items={n}"
            else:
                s=BeautifulSoup(r.text,"lxml")
                feeds=[a["href"] for a in s.find_all("a",href=True) if ".xml" in a["href"] or "/rss" in a["href"].lower()]
                info+=f" HTML feedlinks={len(feeds)}"
                if feeds: info+=" :: "+" | ".join(feeds[:6])
        print(f"{name:26} {info}")
    except Exception as e:
        print(f"{name:26} ERR {type(e).__name__}")
