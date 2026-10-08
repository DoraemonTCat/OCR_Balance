"""ชั้นดึงเว็บ — ไม่ยุ่งกับฐานข้อมูลและไม่รู้จัก framework ใด ๆ

ตั้งใจให้เป็นแบบนี้ตามคู่มือหน้า 5 ข้อ 4: เมื่อไฟล์นี้ไม่แตะ DB เลย
ฝั่ง backend จะเอาไปวางใน workers/ แล้วเรียกใช้ได้ตรง ๆ และเทสต์ได้โดยไม่ต้องมี DB

สิ่งที่ไฟล์นี้ทำ (คู่มือหน้า 3 ขั้น 1-2):
  1. ดึงหน้าหลักของแหล่งข่าว แล้วหาลิงก์บทความ (HTML) หรืออ่านรายการ (RSS/Atom)
  2. ตามเข้าไปดึงบทความแต่ละชิ้น แล้วแยกเอาเฉพาะเนื้อข่าว

สิ่งที่ไฟล์นี้ *ไม่* ทำ: นับ keyword, บันทึกฐานข้อมูล, ตั้งเวลา — เป็นงานฝั่ง backend
"""
from __future__ import annotations

import json
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .net import HOST_DELAY, TIMEOUT, USER_AGENT, ssl_context
from .normalize import normalize_text

#: แท็กที่ไม่ใช่เนื้อข่าว ตัดทิ้งก่อนดึงข้อความ (คู่มือหน้า 3 ขั้น 2)
_BOILERPLATE_TAGS = ("nav", "header", "footer", "script", "style", "aside",
                     "form", "noscript", "iframe", "svg", "button")

#: ลิงก์ที่ยาวเกิน 20 ตัวอักษรบนโดเมนเดียวกัน ถือว่าน่าจะเป็นบทความ
#: ใช้เมื่อแหล่งข่าวไม่ได้ตั้ง link_pattern ไว้ (คู่มือหน้า 3 ขั้น 1)
_MIN_ARTICLE_PATH_LEN = 20

#: เวลารอก่อนลองใหม่เมื่อเว็บตอบ 429 โดยไม่ได้บอก Retry-After มาด้วย
DEFAULT_RETRY_WAIT = 5.0
#: เพดานการรอ กันเว็บที่ตอบ Retry-After มาเป็นชั่วโมงแล้วทำให้ทั้งรอบค้าง
MAX_RETRY_WAIT = 30.0

#: ลิงก์ที่ชี้ไปไฟล์เอกสาร ไม่ใช่หน้าเว็บ — ข้ามไปเลยไม่ต้องดาวน์โหลด
#: feed ของ PMDA และ FDA มีลิงก์แบบนี้ปนมา
_DOCUMENT_EXT_RE = re.compile(
    r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|csv|rtf)(?:$|[?#])", re.I)

#: ชนิดเนื้อหาที่แกะด้วย BeautifulSoup ได้
_MARKUP_TYPES = ("text/html", "application/xhtml", "text/xml", "application/xml",
                 "application/rss", "application/atom", "text/plain")


def _is_markup(content_type):
    return any(t in content_type for t in _MARKUP_TYPES)


class BlockedError(Exception):
    """เว็บปลายทางปฏิเสธเรา — ไม่ใช่ความผิดพลาดของเรา

    แยกจาก error ทั่วไปเพื่อให้ฝั่ง UI บอกผู้ใช้ได้ว่า "เว็บไม่อนุญาต"
    ไม่ใช่ "ระบบพัง" (คู่มือหน้า 3)
    """


class Fetcher:
    """คุมทุก request ให้สุภาพต่อเว็บปลายทาง (คู่มือหน้า 3)

    - ตรวจ robots.txt ก่อนทุก URL และ cache ผลต่อ host
    - รอ 1 วินาทีระหว่าง request ที่ไป host เดียวกัน (เว็บต่างกันดึงพร้อมกันได้)
    - 401 / 403 / 429 = BlockedError ไม่ใช่ error ธรรมดา
    """

    def __init__(self, user_agent=USER_AGENT, timeout=TIMEOUT,
                 host_delay=HOST_DELAY, respect_robots=True):
        self.user_agent = user_agent
        self.host_delay = host_delay
        self.respect_robots = respect_robots
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=timeout,
            follow_redirects=True,
            verify=ssl_context(),
        )
        self._robots = {}                      # host -> RobotFileParser | None
        self._robots_lock = threading.Lock()
        self._host_locks = defaultdict(threading.Lock)
        self._last_hit = {}

    # ---------- มารยาท ----------

    def _wait_for_host(self, host):
        """กันไม่ให้ยิง host เดียวกันถี่เกินไป"""
        with self._host_locks[host]:
            prev = self._last_hit.get(host)
            if prev is not None:
                elapsed = time.monotonic() - prev
                if elapsed < self.host_delay:
                    time.sleep(self.host_delay - elapsed)
            self._last_hit[host] = time.monotonic()

    def _robots_for(self, host, scheme):
        """อ่าน robots.txt ครั้งเดียวต่อ host แล้วจำไว้

        อ่านไม่ได้หรือไม่มีไฟล์ = ถือว่าอนุญาต ซึ่งเป็นธรรมเนียมปกติของ robots.txt
        """
        with self._robots_lock:
            if host in self._robots:
                return self._robots[host]
        parser = None
        try:
            self._wait_for_host(host)
            resp = self._client.get(f"{scheme}://{host}/robots.txt")
            if resp.status_code == 200:
                parser = RobotFileParser()
                parser.parse(resp.text.splitlines())
        except Exception:
            parser = None
        with self._robots_lock:
            self._robots[host] = parser
        return parser

    def allowed(self, url):
        if not self.respect_robots:
            return True
        parts = urlparse(url)
        parser = self._robots_for(parts.netloc, parts.scheme or "https")
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # ---------- การดึง ----------

    def _retry_after(self, resp):
        """อ่านว่าเว็บขอให้รอกี่วินาทีก่อนลองใหม่

        429 แปลว่า "ช้าลงหน่อย" ไม่ใช่ "ห้ามเข้า" การรอตามที่เขาบอกแล้วลองใหม่
        จึงสุภาพกว่าการยอมแพ้ทันที — บางเว็บอย่าง EMA หน่วง 1 วินาทีไม่พอ
        """
        raw = resp.headers.get("retry-after", "")
        try:
            return min(float(raw), MAX_RETRY_WAIT)
        except ValueError:
            return DEFAULT_RETRY_WAIT

    def get(self, url):
        """ดึง URL หนึ่งเส้น คืน httpx.Response

        ขึ้น BlockedError เมื่อ robots.txt ห้าม หรือเว็บตอบ 401/403/429
        กรณี 429 จะรอตามที่เว็บบอกแล้วลองใหม่หนึ่งครั้งก่อน
        """
        if not self.allowed(url):
            raise BlockedError(f"robots.txt ห้ามดึง {url}")

        host = urlparse(url).netloc
        self._wait_for_host(host)
        resp = self._client.get(url)

        if resp.status_code == 429:
            time.sleep(self._retry_after(resp))
            self._wait_for_host(host)
            resp = self._client.get(url)

        if resp.status_code in (401, 403, 429):
            raise BlockedError(f"เว็บปฏิเสธด้วย HTTP {resp.status_code}")
        resp.raise_for_status()
        return resp

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------- แยกลิงก์ออกจากหน้ารวมข่าว ----------

def _clean_url(base, href):
    """ทำลิงก์ให้เป็น absolute และตัด fragment ทิ้ง

    ตัด #main-content ออก เพราะไม่งั้นลิงก์ข้ามไปหัวข้อในหน้าเดิม
    จะถูกนับเป็นบทความคนละชิ้น ทั้งที่เป็นหน้าเดียวกัน
    """
    parts = urlparse(urljoin(base, href))
    if parts.scheme not in ("http", "https"):
        return None
    return urlunparse(parts._replace(fragment=""))


#: ข้อความในเครื่องหมายคำพูดที่อยู่ใน <script> — ใช้หาลิงก์ที่ซ่อนอยู่ใน JSON
#: จำกัดความยาวไว้ไม่ให้ไปจับสคริปต์ทั้งก้อนที่ไม่มีเครื่องหมายคำพูดปิด
_QUOTED_RE = re.compile(r'"((?:[^"\\\n]|\\.){6,600})"')

#: JSON ที่ฝังในหน้าเว็บมักหนี / เป็น \/ หรือ / ต้องคลายก่อนถึงจะใช้ได้
_JSON_ESCAPES = (("\\/", "/"), ("\\u002F", "/"), ("\\u002f", "/"), ("\\&", "&"))

#: นามสกุลไฟล์ประกอบหน้าเว็บ ไม่ใช่บทความแน่นอน
_ASSET_EXT_RE = re.compile(
    r"\.(?:js|mjs|css|png|jpe?g|gif|svg|webp|avif|woff2?|ttf|eot|ico|mp[34]|zip|pdf|docx?|xlsx?)"
    r"(?:$|[?#])", re.I)

#: โฟลเดอร์ของธีม ปลั๊กอิน ไฟล์อัปโหลด ฯลฯ ที่สคริปต์ชอบอ้างถึง
#: ถ้าไม่กรอง เว็บที่ไม่ได้ตั้ง link_pattern จะได้ขยะพวกนี้ปนมาเพียบ
_ASSET_PATH_RE = re.compile(
    r"/(?:wp-content|wp-includes|wp-json|plugins?|themes?|assets?|static|dist|build"
    r"|node_modules|_next|sites/default/files|media|fonts?|img|images)/", re.I)


def _candidate_from_script(raw, base_url):
    """แปลงข้อความจาก <script> เป็น URL ถ้ามันหน้าตาเหมือนลิงก์บทความ"""
    for escaped, plain in _JSON_ESCAPES:
        raw = raw.replace(escaped, plain)
    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("/")):
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    if _ASSET_EXT_RE.search(raw) or _ASSET_PATH_RE.search(raw):
        return None
    return _clean_url(base_url, raw)


def extract_links(html, base_url, link_pattern=None, search_scripts=True):
    """หาลิงก์บทความจากหน้ารวมข่าว (คู่มือหน้า 3 ขั้น 1)

    มี link_pattern: เลือกเฉพาะลิงก์ที่ path ตรงกับ regex นั้น
    ไม่มี: เลือกลิงก์โดเมนเดียวกันที่ path ยาวเกิน 20 ตัวอักษร

    search_scripts: ค้น URL ที่อยู่ใน <script>/JSON ด้วย
        บางเว็บโหลดรายการข่าวผ่าน JavaScript ลิงก์จึงไม่อยู่ใน <a href>
        แต่ฝังเป็นข้อความอยู่ใน JSON ที่ติดมากับหน้า

    แต่ละรายการมี found_in บอกว่าเจอจาก "anchor" หรือ "script"
    """
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(base_url).netloc
    rx = re.compile(link_pattern) if link_pattern else None

    found, seen = [], set()

    def accept(url, title, source):
        """เก็บลิงก์ถ้าผ่านเกณฑ์และยังไม่เคยเจอ"""
        if not url or url in seen:
            return
        parts = urlparse(url)
        if parts.netloc != host or url.rstrip("/") == base_url.rstrip("/"):
            return
        if rx is not None:
            if not rx.search(parts.path):
                return
        elif len(parts.path) <= _MIN_ARTICLE_PATH_LEN:
            return
        seen.add(url)
        found.append({"url": url, "title": title or None, "found_in": source})

    for anchor in soup.find_all("a", href=True):
        accept(_clean_url(base_url, anchor["href"]),
               normalize_text(anchor.get_text(" ", strip=True)),
               "anchor")

    if search_scripts:
        for script in soup.find_all("script"):
            body = script.string or script.get_text() or ""
            if not body:
                continue
            for match in _QUOTED_RE.finditer(body):
                accept(_candidate_from_script(match.group(1), base_url), None, "script")

    return found


# ---------- อ่าน RSS / Atom ----------

#: ออฟเซ็ตเขตเวลาแบบไม่มีโคลอน (+0200) ซึ่ง fromisoformat ของ Python 3.10 ไม่รับ
_TZ_NO_COLON_RE = re.compile(r"([+-]\d{2})(\d{2})$")


def _parse_date(value):
    """แปลงวันที่เป็น ISO 8601 — คืน None ถ้าแปลงไม่ได้

    รองรับทั้ง RSS (Tue, 06 Oct 2026 13:09:00 EDT), Atom, meta tag ของหน้าเว็บ
    และรูปแบบเฉพาะกิจที่บางเว็บใช้
    """
    if not value:
        return None
    value = value.strip()

    try:
        return parsedate_to_datetime(value).isoformat()          # RFC 2822 (RSS)
    except (TypeError, ValueError):
        pass

    iso = value.replace("Z", "+00:00")
    iso = _TZ_NO_COLON_RE.sub(r"\1:\2", iso)
    try:
        return datetime.fromisoformat(iso).isoformat()           # ISO 8601 (Atom, meta)
    except ValueError:
        pass

    for fmt in ("%a, %m/%d/%Y - %H:%M",    # FDA: Mon, 09/29/2025 - 15:03
                "%d %B %Y",                # 30 September 2026
                "%B %d, %Y",               # September 30, 2026
                "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).isoformat()
        except ValueError:
            continue
    return None


#: meta tag ที่เว็บต่าง ๆ ใช้บอกวันที่เผยแพร่ เรียงจากน่าเชื่อถือที่สุด
#: og:updated_time กับ dateModified อยู่ท้ายสุด เพราะเป็นวันที่ *แก้ไข* ไม่ใช่วันที่เผยแพร่
_DATE_META = (
    ("property", "article:published_time"),
    ("name", "dcterms.issued"),
    ("name", "dcterms.date"),
    ("itemprop", "datePublished"),
    ("name", "date"),
    ("name", "pubdate"),
    ("property", "og:published_time"),
)
_DATE_META_FALLBACK = (
    ("property", "og:updated_time"),
    ("name", "dcterms.modified"),
)


def _jsonld_dates(soup):
    """ดึงวันที่จาก JSON-LD ที่ฝังอยู่ในหน้า (บางเว็บมีแค่ที่นี่ เช่น HSA)"""
    published, modified = [], []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, str):
                    if key == "datePublished":
                        published.append(value)
                    elif key == "dateModified":
                        modified.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(script.string or "{}"))
        except (ValueError, TypeError):
            continue
    return published, modified


def extract_published_at(html):
    """หาวันที่เผยแพร่ของบทความ คืนสตริง ISO 8601 หรือ None

    หน้ารวมข่าวมักไม่ได้บอกวันที่มาด้วย จึงต้องมาอ่านจากหน้าบทความเอง
    ไล่หาตามลำดับ meta tag -> JSON-LD -> <time datetime> -> วันที่แก้ไขล่าสุด
    """
    soup = BeautifulSoup(html, "lxml")
    published_ld, modified_ld = _jsonld_dates(soup)

    def meta_values(table):
        for attr, key in table:
            tag = soup.find("meta", attrs={attr: key})
            if tag and tag.get("content"):
                yield tag["content"]

    candidates = [
        *meta_values(_DATE_META),
        *published_ld,
        *[t.get("datetime") for t in soup.find_all("time") if t.get("datetime")],
        *meta_values(_DATE_META_FALLBACK),
        *modified_ld,
    ]
    for value in candidates:
        parsed = _parse_date(value)
        if parsed:
            return parsed
    return None


def parse_feed(xml_text, limit=None):
    """อ่าน <item> (RSS) หรือ <entry> (Atom) คืนรายการข่าว (คู่มือหน้า 3 ขั้น 1)"""
    soup = BeautifulSoup(xml_text, "xml")
    entries = soup.find_all("item") or soup.find_all("entry")
    if limit is not None:
        entries = entries[:limit]

    items = []
    for node in entries:
        def text_of(*names):
            for name in names:
                el = node.find(name)
                if el is None:
                    continue
                return el.get_text(strip=True) or el.get("href") or None
            return None

        summary_raw = text_of("description", "summary", "content")
        summary = None
        if summary_raw:
            # description ของหลาย feed เป็น HTML ย่อ ต้องถอดแท็กก่อน
            summary = normalize_text(BeautifulSoup(summary_raw, "lxml").get_text(" ", strip=True))

        raw_date = text_of("pubDate", "published", "updated", "dc:date")
        items.append({
            "url": text_of("link", "guid"),
            "title": normalize_text(text_of("title")) or None,
            "published_at": _parse_date(raw_date),
            "published_raw": raw_date,
            "summary": summary or None,
        })
    return items


# ---------- แยกเนื้อข่าวออกจากหน้าบทความ ----------

#: สัดส่วนขั้นต่ำที่กล่องเนื้อหาต้องมี เทียบกับกล่องที่มีข้อความมากที่สุด
#:
#: เดิมเลือก <article> ก่อนเสมอตามลำดับในคู่มือ แต่บางเว็บ (เช่น EMA) ใช้ <article>
#: เป็นกล่องเกริ่นเล็ก ๆ 222 ตัวอักษร ขณะที่เนื้อข่าวจริง 4,293 ตัวอักษรอยู่ใน <main>
#: จึงยังเรียงตามลำดับเดิม แต่ข้ามกล่องที่เนื้อหาน้อยผิดปกติไป
_CONTENT_RATIO = 0.6


def extract_article(html):
    """ลบส่วนที่ไม่ใช่เนื้อข่าวออก แล้วดึงข้อความ (คู่มือหน้า 3 ขั้น 2)

    ไล่หาตามลำดับ <article> -> <main> -> <body> เพราะ <article> เจาะจงที่สุด
    แต่ถ้ากล่องที่เจาะจงกว่ามีข้อความน้อยกว่า 60% ของกล่องที่มากที่สุด ให้ข้ามไป
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_BOILERPLATE_TAGS):
        tag.decompose()

    candidates = []
    for finder in (lambda: soup.find("article"), lambda: soup.find("main"), lambda: soup.body):
        node = finder()
        if node is None:
            continue
        text = normalize_text(node.get_text(" ", strip=True))
        if text:
            candidates.append(text)
    if not candidates:
        return None

    longest = max(len(t) for t in candidates)
    for text in candidates:                      # ยังเรียงตามลำดับความเจาะจงเดิม
        if len(text) >= longest * _CONTENT_RATIO:
            return text
    return candidates[-1]


#: ความยาวขั้นต่ำที่ถือว่า <h1> เป็นหัวข้อข่าวจริง ไม่ใช่ชื่อเว็บ
#:
#: บางเว็บใส่ชื่อองค์กรไว้ใน <h1> ตัวแรก แล้วค่อยใส่หัวข้อข่าวใน <h1> ตัวถัดไป
#: เช่น UNODC มี <h1>United Nations</h1> นำหน้าหัวข้อจริงเสมอ
_MIN_TITLE_LEN = 25


def extract_title(html):
    """ดึงหัวข้อข่าวจากหน้าบทความ

    เลือก <h1> ที่ยาวที่สุด เพราะหน้าที่มีหลาย <h1> มักเอาชื่อเว็บขึ้นก่อน
    ถ้ายังสั้นผิดปกติให้ใช้ <title> แทน
    """
    soup = BeautifulSoup(html, "lxml")

    headings = [normalize_text(h.get_text(" ", strip=True)) for h in soup.find_all("h1")]
    headings = [h for h in headings if h]
    best = max(headings, key=len) if headings else None
    if best and len(best) >= _MIN_TITLE_LEN:
        return best

    if soup.title:
        # <title> มักมีชื่อเว็บต่อท้ายหลังขีดคั่น ตัดออกถ้าส่วนหน้ายาวพอ
        raw = normalize_text(soup.title.get_text(strip=True))
        head = re.split(r"\s+[|–—]\s+", raw)[0] if raw else ""
        page_title = head if len(head) >= _MIN_TITLE_LEN else raw
        if page_title:
            return page_title

    return best


#: ชื่อเดือนภาษาอังกฤษที่โผล่ใน URL ของบางเว็บ เช่น /2026/October/
_URL_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], start=1)}

_URL_DATE_RES = (
    re.compile(r"/(?P<y>20\d{2})/(?P<m>\d{1,2})/(?P<d>\d{1,2})(?:/|$|[.-])"),
    re.compile(r"/(?P<y>20\d{2})/(?P<mon>[A-Za-z]+)/"),
    re.compile(r"/(?P<y>20\d{2})/(?P<m>\d{1,2})(?:/|$)"),
)


def date_from_url(url):
    """เดาวันที่จากเส้นทางใน URL เช่น /2026/October/ หรือ /2026/10/07/

    ใช้เป็นทางเลือกสุดท้ายเมื่อหน้าเว็บไม่ได้บอกวันที่ไว้เลย
    ยอมรับเฉพาะกรณีที่ได้อย่างน้อยปีกับเดือน — ปีอย่างเดียวคลาดเคลื่อนเกินไป
    ค่าที่ได้จะถูกกำกับว่ามาจาก URL ไว้ในช่อง published_at_source
    """
    path = urlparse(url).path
    for pattern in _URL_DATE_RES:
        match = pattern.search(path)
        if not match:
            continue
        parts = match.groupdict()
        month = (_URL_MONTHS.get(parts["mon"].lower())
                 if parts.get("mon") else int(parts["m"]))
        if not month or not 1 <= month <= 12:
            continue
        day = int(parts["d"]) if parts.get("d") else 1
        try:
            return datetime(int(parts["y"]), month, day).isoformat()
        except ValueError:
            continue
    return None


# ---------- ดึงแหล่งข่าวหนึ่งแหล่งจนจบ ----------

def crawl_source(source, fetcher, should_stop=None, max_articles=None):
    """ดึงแหล่งข่าวหนึ่งแหล่ง คืน dict พร้อมบทความทั้งหมด

    source: dict จาก sources.json (ต้องมี name, url, kind อย่างน้อย)
    should_stop: ฟังก์ชันไม่รับอาร์กิวเมนต์ คืน True เมื่อผู้ใช้สั่งยกเลิก
                 ตรวจก่อนบทความแต่ละชิ้น (คู่มือหน้า 4)

    status มีได้ 3 ค่า: ok / blocked / error
    """
    limit = max_articles or source.get("max_articles") or 30
    full_text = bool(source.get("extract_full_text"))
    stop = should_stop or (lambda: False)

    result = {
        "name": source["name"],
        "url": source["url"],
        "kind": source.get("kind", "html"),
        "group": source.get("group"),
        "status": "ok",
        "error": None,
        "cancelled": False,
        "articles": [],
    }

    try:
        response = fetcher.get(source["url"])
        base_url = str(response.url)

        if result["kind"] == "rss":
            items = parse_feed(response.text, limit)
            for item in items:
                item["title_from"] = "feed"
        else:
            links = extract_links(response.text, base_url, source.get("link_pattern"))[:limit]
            # title ที่ได้ตรงนี้คือข้อความในแท็ก <a> ซึ่งมักเป็นปุ่ม ("Read highlights")
            # หรือมีวันที่ซ้ำติดมา จึงถือเป็นแค่ตัวสำรอง ของจริงเอาจาก <h1> ในหน้าบทความ
            items = [{"url": l["url"], "title": l["title"], "published_at": None,
                      "published_raw": None, "summary": None, "title_from": "anchor"}
                     for l in links]

        for item in items:
            if stop():
                result["cancelled"] = True
                break
            result["articles"].append(_fetch_article(item, fetcher, full_text))

    except BlockedError as exc:
        result["status"] = "blocked"
        result["error"] = str(exc)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result


def _fetch_article(item, fetcher, full_text):
    """เติมเนื้อข่าวให้รายการหนึ่งชิ้น

    text_source บอกว่าข้อความที่ได้มาจากไหน ฝั่งที่เอาไปแมพ keyword ต้องใช้ค่านี้
    ตีความผล เพราะ rss_summary มีข้อความสั้นกว่าเนื้อเต็มมาก
    """
    article = {
        "url": item["url"],
        "title": item.get("title"),
        "published_at": item.get("published_at"),
        "published_at_source": "feed" if item.get("published_at") else None,
        "summary": item.get("summary"),
        "text": None,
        "text_source": "rss_summary" if item.get("summary") else "none",
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "error": None,
    }

    if not full_text or not item["url"]:
        return article

    # ลิงก์ที่ชี้ไปไฟล์โดยตรง ไม่ต้องเสียเวลาดาวน์โหลด
    # ถ้าปล่อยผ่าน BeautifulSoup จะเอาไบต์ของ PDF มาแกะเป็นข้อความ ได้ขยะล้วน
    if _DOCUMENT_EXT_RE.search(item["url"]):
        article["text_source"] = "skipped_document"
        article["error"] = "ข้ามเพราะลิงก์ชี้ไปไฟล์เอกสาร ไม่ใช่หน้าเว็บ"
        return article

    try:
        response = fetcher.get(item["url"])

        # เช็กอีกชั้นจากสิ่งที่เซิร์ฟเวอร์ตอบมา เพราะบาง URL ไม่มีนามสกุลไฟล์
        content_type = response.headers.get("content-type", "").lower()
        if content_type and not _is_markup(content_type):
            article["text_source"] = "skipped_document"
            article["error"] = f"ข้ามเพราะ content-type เป็น {content_type.split(';')[0]}"
            return article

        article["text"] = extract_article(response.text)
        if article["text"]:
            article["text_source"] = "article"

        # title จากข้อความในแท็ก <a> เชื่อถือไม่ได้ ใช้ <h1> ของหน้าบทความแทน
        if item.get("title_from") == "anchor" or not article["title"]:
            page_title = extract_title(response.text)
            if page_title:
                article["title"] = page_title

        # หน้ารวมข่าวไม่ได้บอกวันที่มาด้วย จึงอ่านจากหน้าบทความเอง
        if not article["published_at"]:
            page_date = extract_published_at(response.text)
            if page_date:
                article["published_at"] = page_date
                article["published_at_source"] = "page"

        # บางเว็บไม่บอกวันที่ไว้ในหน้าเลย เหลือทางเดียวคือเดาจาก URL
        if not article["published_at"]:
            url_date = date_from_url(item["url"])
            if url_date:
                article["published_at"] = url_date
                article["published_at_source"] = "url_path"
    except BlockedError as exc:
        article["error"] = f"blocked: {exc}"
    except Exception as exc:
        article["error"] = f"{type(exc).__name__}: {exc}"

    return article
