
import pathlib
import sys

# สคริปต์ในโฟลเดอร์นี้รันตรง ๆ ได้ จึงต้องเติมรากโปรเจ็กต์เข้า sys.path เอง
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCES_FILE = ROOT / "workers" / "crawler" / "sources.json"
OUTPUT_DIR = ROOT / "data" / "crawl"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
import json, re, time
from collections import Counter
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup

from workers.crawler.net import ssl_context

TARGETS = {
 "Health Canada":"https://recalls-rappels.canada.ca/en",
 "HSA Singapore":"https://www.hsa.gov.sg/announcements",
 "FDA Device Safety":"https://www.fda.gov/medical-devices/medical-device-safety",
 "สบส. MOPH":"https://hss.moph.go.th/info_act/",
 "UNODC":"https://www.unodc.org/",
 "INCB":"https://www.incb.org/incb/index.html",
 "NIDA news":"https://nida.nih.gov/news-events/news-releases",
 "ONDCP":"https://www.whitehouse.gov/ondcp/",
 "drugfree.org":"https://drugfree.org/",
 "HK MDD":"https://www.mdd.gov.hk/en/home/index.html",
 "EMA home":"https://www.ema.europa.eu/en",
}
with httpx.Client(headers={"User-Agent":"HerbRiskBot/1.0"},timeout=30,follow_redirects=True,verify=ssl_context()) as c:
    for name,url in TARGETS.items():
        try:
            r=c.get(url); time.sleep(1)
            if r.status_code!=200: print(f"\n## {name}: HTTP {r.status_code}"); continue
            s=BeautifulSoup(r.text,"lxml"); host=urlparse(str(r.url)).netloc
            paths=[]
            for a in s.find_all("a",href=True):
                u=urljoin(str(r.url),a["href"]); p=urlparse(u)
                if p.netloc!=host or p.scheme not in("http","https"): continue
                if len(p.path)<=20: continue
                paths.append(p.path)
            # นับ prefix 2 ระดับแรก เพื่อหาแพตเทิร์นที่ลิงก์บทความชอบอยู่
            pref=Counter("/".join(p.split("/")[:3]) for p in paths)
            print(f"\n## {name}  (ลิงก์ผ่านเกณฑ์ {len(paths)})")
            for k,v in pref.most_common(6): print(f"   {v:4}x  {k}/...")
        except Exception as e:
            print(f"\n## {name}: ERR {type(e).__name__}")
