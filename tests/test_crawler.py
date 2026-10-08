"""เทสต์ชั้นดึงเว็บ โดยไม่ยิงเว็บจริงสักครั้ง (คู่มือหน้า 5 ข้อ 3)

ทำได้เพราะ crawl_source() รับ fetcher เข้ามาเป็นอาร์กิวเมนต์
เราจึงส่ง FakeFetcher ที่คืน HTML ที่เตรียมไว้แทนได้

รันด้วย:  python -m pytest tests/test_crawler.py -v
หรือถ้าไม่มี pytest:  python tests/test_crawler.py
"""
import pathlib
import sys

# ไฟล์นี้รันตรง ๆ ได้ด้วย (python tests/test_crawler.py) ไม่ต้องผ่าน pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from workers.crawler import (BlockedError, crawl_source, extract_article,
                             date_from_url, extract_links,
                             extract_published_at, extract_title,
                             normalize_source, normalize_text, parse_feed)


class FakeResponse:
    def __init__(self, text, url, content_type="text/html; charset=utf-8"):
        self.text = text
        self.url = url
        self.headers = {"content-type": content_type}


class FakeFetcher:
    """แทน Fetcher จริง — คืนเนื้อหาจาก dict ที่เตรียมไว้

    ถ้า URL ไหนไม่มีใน pages ให้ขึ้น BlockedError เหมือนเว็บปฏิเสธ
    ค่าใน pages เป็นสตริง หรือ (เนื้อหา, content-type) ก็ได้
    """

    def __init__(self, pages, blocked=()):
        self.pages = pages
        self.blocked = set(blocked)
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        if url in self.blocked:
            raise BlockedError("เว็บปฏิเสธด้วย HTTP 403")
        if url not in self.pages:
            raise ValueError(f"ไม่มีหน้านี้ใน FakeFetcher: {url}")
        page = self.pages[url]
        if isinstance(page, tuple):
            return FakeResponse(page[0], url, page[1])
        return FakeResponse(page, url)


LISTING = """
<html><body>
  <nav><a href="/about">เกี่ยวกับเรา</a></nav>
  <a href="/en/alert-recall/first-story">ข่าวแรก</a>
  <a href="/en/alert-recall/second-story">ข่าวสอง</a>
  <a href="/en/alert-recall/first-story#top">ข่าวแรก (ลิงก์ซ้ำ มี fragment)</a>
  <a href="https://other.example.com/en/alert-recall/x">คนละโดเมน</a>
  <a href="/en/help">หน้าช่วยเหลือ</a>
</body></html>
"""

#: เว็บที่โหลดรายการข่าวด้วย JavaScript ลิงก์จึงไม่อยู่ใน <a href>
#: แต่ฝังเป็นข้อความใน JSON ที่ติดมากับหน้า (คู่มือหน้า 3 ขั้น 1)
LISTING_JS = """
<html><body>
  <a href="/en/help">หน้าช่วยเหลือ</a>
  <div id="root"></div>
  <script type="application/json">
    {"items":[
      {"url":"\\/en\\/alert-recall\\/from-json-one","title":"ข่าวจาก JSON หนึ่ง"},
      {"url":"https:\\/\\/example.org\\/en\\/alert-recall\\/from-json-two"},
      {"logo":"\\/wp-content\\/uploads\\/en\\/alert-recall\\/logo-banner.png"},
      {"bundle":"\\/static\\/en\\/alert-recall\\/app-chunk.js"}
    ]}
  </script>
</body></html>
"""

ARTICLE = """
<html><body>
  <nav>เมนู ที่ ไม่ ใช่ ข่าว</nav>
  <header>หัวเว็บ</header>
  <article><h1>เรียกคืนยา</h1><p>พบสารปนเปื้อนในยา ล็อตที่ระบุ</p></article>
  <footer>ท้ายเว็บ</footer>
  <script>var x = 1;</script>
</body></html>
"""

FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Voluntary Recall of Example Tablets</title>
    <link>https://example.org/news/1</link>
    <pubDate>Tue, 06 Oct 2026 13:09:00 GMT</pubDate>
    <description>&lt;p&gt;Company recalls one lot.&lt;/p&gt;</description>
  </item>
  <item>
    <title>Second Item</title>
    <link>https://example.org/news/2</link>
    <pubDate>Mon, 05 Oct 2026 09:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""


def test_extract_links_ใช้_pattern_คัดและตัดลิงก์ซ้ำ():
    links = extract_links(LISTING, "https://example.org/en", r"/en/alert-recall/")
    urls = [l["url"] for l in links]
    assert urls == ["https://example.org/en/alert-recall/first-story",
                    "https://example.org/en/alert-recall/second-story"], urls
    # ลิงก์ที่มี #top ชี้หน้าเดียวกับข่าวแรก ต้องไม่ถูกนับซ้ำ
    assert len(urls) == len(set(urls))


def test_extract_links_ไม่มี_pattern_ใช้ความยาว_path():
    links = extract_links(LISTING, "https://example.org/en")
    for link in links:
        assert "other.example.com" not in link["url"]      # ต้องเป็นโดเมนเดียวกัน
    assert any("first-story" in l["url"] for l in links)
    assert not any(l["url"].endswith("/en/help") for l in links)   # path สั้นเกินไป


def test_extract_links_หา_url_ที่ซ่อนอยู่ใน_script():
    links = extract_links(LISTING_JS, "https://example.org/en", r"/en/alert-recall/")
    urls = [l["url"] for l in links]

    # ลิงก์ทั้งสองอยู่ใน JSON ไม่ได้อยู่ใน <a href> เลย
    assert "https://example.org/en/alert-recall/from-json-one" in urls
    assert "https://example.org/en/alert-recall/from-json-two" in urls
    assert all(l["found_in"] == "script" for l in links)

    # ไฟล์ประกอบหน้าเว็บต้องไม่ถูกนับเป็นบทความ แม้ path จะตรง pattern
    assert not any("logo-banner.png" in u for u in urls)
    assert not any("app-chunk.js" in u for u in urls)


def test_extract_links_ปิดการค้น_script_ได้():
    links = extract_links(LISTING_JS, "https://example.org/en", r"/en/alert-recall/",
                          search_scripts=False)
    assert links == []


def test_extract_links_บอกได้ว่าเจอลิงก์จากไหน():
    links = extract_links(LISTING, "https://example.org/en", r"/en/alert-recall/")
    assert all(l["found_in"] == "anchor" for l in links)


def test_extract_article_ตัดเมนูและสคริปต์ออก():
    text = extract_article(ARTICLE)
    assert "เรียกคืนยา" in text
    assert "สารปนเปื้อนในยา" in text
    assert "เมนู" not in text
    assert "หัวเว็บ" not in text and "ท้ายเว็บ" not in text
    assert "var x" not in text


#: <article> เป็นกล่องเกริ่นเล็ก ๆ ส่วนเนื้อข่าวจริงอยู่ใน <main> (เจอที่ EMA)
TEASER_PAGE = """
<html><body>
  <h1>ชื่อข่าวจริงอยู่ตรงนี้</h1>
  <article>อ่านต่อ</article>
  <main>
    <p>เนื้อข่าวจริงยาวกว่ามาก พบสารปนเปื้อนในยาล็อตที่ระบุ และมีการเรียกเก็บคืนยา
    จากร้านขายยาทั่วประเทศ ผู้ที่ซื้อไปแล้วให้นำกลับไปคืนที่ร้าน</p>
    <p>หน่วยงานกำลังตรวจสอบเพิ่มเติมว่ามีล็อตอื่นได้รับผลกระทบหรือไม่</p>
  </main>
</body></html>
"""


def test_extract_article_ข้ามกล่องที่เนื้อหาน้อยผิดปกติ():
    text = extract_article(TEASER_PAGE)
    # ต้องไม่ได้แค่ "อ่านต่อ" จาก <article> ซึ่งสั้นผิดปกติ
    assert "เนื้อข่าวจริงยาวกว่ามาก" in text
    assert "เรียกเก็บคืนยา" in text
    assert len(text) > 100


def test_fetch_article_ข้ามลิงก์ที่ชี้ไปไฟล์_pdf():
    source = {"name": "มีลิงก์ PDF", "url": "https://example.org/feed",
              "kind": "rss", "extract_full_text": True}
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>รายงานประจำปี</title>
      <link>https://example.org/docs/annual-report.pdf</link>
    </item></channel></rss>"""
    fetcher = FakeFetcher({"https://example.org/feed": feed})
    result = crawl_source(source, fetcher)

    article = result["articles"][0]
    assert article["text"] is None
    assert article["text_source"] == "skipped_document"
    # ต้องไม่ดาวน์โหลดไฟล์เลย ยิงแค่ feed อย่างเดียว
    assert fetcher.calls == ["https://example.org/feed"]


def test_fetch_article_ข้ามเมื่อ_content_type_ไม่ใช่หน้าเว็บ():
    """URL ไม่มีนามสกุลไฟล์ แต่เซิร์ฟเวอร์ตอบกลับมาเป็น PDF"""
    source = {"name": "ลิงก์หลอก", "url": "https://example.org/feed",
              "kind": "rss", "extract_full_text": True}
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel><item>
      <title>เอกสารแนบ</title><link>https://example.org/download/12345</link>
    </item></channel></rss>"""
    fetcher = FakeFetcher({
        "https://example.org/feed": feed,
        "https://example.org/download/12345": ("%PDF-1.6 ...ไบต์ขยะ...", "application/pdf"),
    })
    result = crawl_source(source, fetcher)

    article = result["articles"][0]
    assert article["text"] is None
    assert article["text_source"] == "skipped_document"
    assert "application/pdf" in article["error"]


def test_html_ใช้_h1_ของหน้าบทความแทนข้อความในลิงก์():
    base = "https://example.org/en"
    listing = """
    <html><body>
      <a href="/en/alert-recall/story-one">2 ตุลาคม 2026 2 ตุลาคม 2026 อ่านต่อ</a>
    </body></html>
    """
    pages = {base: listing, "https://example.org/en/alert-recall/story-one": TEASER_PAGE}
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True}
    result = crawl_source(source, FakeFetcher(pages))

    # ข้อความในแท็ก <a> มีวันที่ซ้ำและคำว่า "อ่านต่อ" ต้องถูกแทนด้วย <h1>
    assert result["articles"][0]["title"] == "ชื่อข่าวจริงอยู่ตรงนี้"


def test_rss_เก็บ_title_จาก_feed_ไม่ไปเอา_h1():
    """feed เขียนหัวข้อมาถูกแล้ว ไม่ควรไปทับด้วย <h1> ของหน้า"""
    source = {"name": "ทดสอบ", "url": "https://example.org/feed",
              "kind": "rss", "extract_full_text": True}
    pages = {"https://example.org/feed": FEED,
             "https://example.org/news/1": TEASER_PAGE,
             "https://example.org/news/2": TEASER_PAGE}
    result = crawl_source(source, FakeFetcher(pages))

    assert result["articles"][0]["title"] == "Voluntary Recall of Example Tablets"


DATED_PAGES = {
    "meta article:published_time": (
        '<html><head><meta property="article:published_time" content="2026-09-30T11:32:50+0200">'
        '</head><body><main>เนื้อข่าว</main></body></html>', "2026-09-30"),
    "meta dcterms.issued": (
        '<html><head><meta name="dcterms.issued" content="2026-10-07">'
        '</head><body><main>เนื้อข่าว</main></body></html>', "2026-10-07"),
    "JSON-LD dateModified": (
        '<html><head><script type="application/ld+json">'
        '{"@type":"WebPage","dateModified":"2026-09-30T06:31:43.672Z"}</script>'
        '</head><body><main>เนื้อข่าว</main></body></html>', "2026-09-30"),
    "time datetime": (
        '<html><body><time datetime="2026-10-07T12:00:00Z">7 ต.ค.</time>'
        '<main>เนื้อข่าว</main></body></html>', "2026-10-07"),
    "รูปแบบเฉพาะของ FDA": (
        '<html><head><meta property="article:published_time" content="Mon, 09/29/2025 - 15:03">'
        '</head><body><main>เนื้อข่าว</main></body></html>', "2025-09-29"),
}


def test_extract_published_at_รองรับทุกรูปแบบที่เจอจริง():
    for label, (html, expected) in DATED_PAGES.items():
        got = extract_published_at(html)
        assert got and got.startswith(expected), f"{label}: ได้ {got!r} ไม่ขึ้นต้นด้วย {expected}"


def test_extract_published_at_ไม่มีวันที่คืน_none():
    assert extract_published_at("<html><body><main>ไม่มีวันที่</main></body></html>") is None


def test_html_เติมวันที่จากหน้าบทความ():
    """หน้ารวมข่าวไม่ได้บอกวันที่ ต้องไปอ่านจากหน้าบทความเอง"""
    base = "https://example.org/en"
    listing = '<html><body><a href="/en/alert-recall/story">ข่าว</a></body></html>'
    article = ('<html><head><meta name="dcterms.issued" content="2026-10-07"></head>'
               '<body><h1>หัวข้อข่าว</h1><main>' + "เนื้อข่าวยาวพอสมควรสำหรับการทดสอบ " * 5
               + '</main></body></html>')
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True}
    result = crawl_source(source, FakeFetcher({base: listing,
                                              "https://example.org/en/alert-recall/story": article}))
    assert result["articles"][0]["published_at"].startswith("2026-10-07")


def test_date_from_url_เดาวันที่จากเส้นทาง():
    assert date_from_url("https://e.org/press/2026/October/story.html").startswith("2026-10-01")
    assert date_from_url("https://e.org/news/2026/6/story").startswith("2026-06-01")
    assert date_from_url("https://e.org/news/2026/10/07/story").startswith("2026-10-07")
    # ปีอย่างเดียวคลาดเคลื่อนเกินไป ไม่รับ
    assert date_from_url("https://e.org/press-releases/2026/story.html") is None
    assert date_from_url("https://e.org/no-date/story") is None
    # เดือนที่เป็นไปไม่ได้ต้องไม่ถูกรับ
    assert date_from_url("https://e.org/news/2026/13/story") is None


def test_extract_title_ข้ามชื่อเว็บที่อยู่ใน_h1_ตัวแรก():
    html = ("<html><head><title>หัวข้อข่าวฉบับเต็มที่ยาวพอสมควร | ชื่อเว็บ</title></head>"
            "<body><h1>United Nations</h1>"
            "<h1>หัวข้อข่าวจริงที่ยาวกว่าและเจาะจงกว่ามาก</h1></body></html>")
    assert extract_title(html) == "หัวข้อข่าวจริงที่ยาวกว่าและเจาะจงกว่ามาก"


def test_extract_title_ใช้_title_เมื่อ_h1_สั้นเกินไป():
    html = ("<html><head><title>หัวข้อข่าวฉบับเต็มที่ยาวพอสมควร | ชื่อเว็บ</title></head>"
            "<body><h1>ชื่อเว็บ</h1></body></html>")
    assert extract_title(html) == "หัวข้อข่าวฉบับเต็มที่ยาวพอสมควร"


def test_published_at_source_บอกที่มาของวันที่():
    base = "https://example.org/en"
    listing = '<html><body><a href="/en/alert-recall/2026/October/story">ข่าว</a></body></html>'
    page = ('<html><body><h1>หัวข้อข่าวที่ยาวพอจะถือว่าเป็นหัวข้อจริง</h1>'
            '<main>' + "เนื้อข่าวสำหรับการทดสอบ " * 8 + '</main></body></html>')
    url = "https://example.org/en/alert-recall/2026/October/story"
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True}
    article = crawl_source(source, FakeFetcher({base: listing, url: page}))["articles"][0]

    # หน้าไม่มี meta วันที่ จึงต้องตกมาเดาจาก URL และต้องบอกไว้ว่าเดามา
    assert article["published_at"].startswith("2026-10-01")
    assert article["published_at_source"] == "url_path"


def test_normalize_source_เติมค่าปริยายและตรวจฟิลด์บังคับ():
    filled = normalize_source({"name": "ทดสอบ", "url": "https://example.org"})
    assert filled["kind"] == "html"
    assert filled["max_articles"] == 30
    assert filled["extract_full_text"] is False

    for bad, reason in [({"url": "https://example.org"}, "name"),
                        ({"name": "x"}, "url"),
                        ({"name": "x", "url": "https://e.org", "kind": "json"}, "kind")]:
        try:
            normalize_source(bad)
        except ValueError as exc:
            assert reason in str(exc)
        else:
            raise AssertionError(f"ควรขึ้น ValueError เพราะ {reason}")


def test_parse_feed_อ่านรายการและแปลงวันที่():
    items = parse_feed(FEED)
    assert len(items) == 2
    assert items[0]["title"] == "Voluntary Recall of Example Tablets"
    assert items[0]["url"] == "https://example.org/news/1"
    assert items[0]["published_at"].startswith("2026-10-06")
    assert items[0]["summary"] == "Company recalls one lot."    # ถอดแท็ก HTML ออกแล้ว
    assert items[1]["summary"] is None


def test_normalize_รวม_sara_am_และลบ_zero_width():
    แยกส่วน = "สารท" + "ํา" + "ให้คงตัว"
    รวม = "สารทำให้คงตัว"
    assert normalize_text(แยกส่วน) == normalize_text(รวม)
    # zero-width space ต้องหายไป ไม่ใช่กลายเป็นช่องว่าง ไม่งั้น keyword ไทยจะพัง
    assert normalize_text("เรียก​เก็บคืนยา") == "เรียกเก็บคืนยา"


def test_crawl_source_แบบ_html_ดึงลิงก์แล้วตามเข้าไปอ่าน():
    base = "https://example.org/en"
    pages = {
        base: LISTING,
        "https://example.org/en/alert-recall/first-story": ARTICLE,
        "https://example.org/en/alert-recall/second-story": ARTICLE,
    }
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True}
    result = crawl_source(source, FakeFetcher(pages))

    assert result["status"] == "ok"
    assert len(result["articles"]) == 2
    assert all(a["text_source"] == "article" for a in result["articles"])
    assert "สารปนเปื้อนในยา" in result["articles"][0]["text"]


def test_crawl_source_แบบ_rss_ที่ไม่ดึงเนื้อเต็ม():
    source = {"name": "ข่าวเชิงพาณิชย์", "url": "https://example.org/feed",
              "kind": "rss", "extract_full_text": False}
    fetcher = FakeFetcher({"https://example.org/feed": FEED})
    result = crawl_source(source, fetcher)

    assert result["status"] == "ok"
    assert len(result["articles"]) == 2
    assert result["articles"][0]["text"] is None
    assert result["articles"][0]["text_source"] == "rss_summary"
    # ต้องยิงแค่ feed อย่างเดียว ไม่ตามเข้าไปในบทความ
    assert fetcher.calls == ["https://example.org/feed"]


def test_crawl_source_เว็บปฏิเสธได้สถานะ_blocked_ไม่ใช่_error():
    source = {"name": "เว็บที่บล็อกเรา", "url": "https://blocked.example.org/", "kind": "html"}
    fetcher = FakeFetcher({}, blocked={"https://blocked.example.org/"})
    result = crawl_source(source, fetcher)

    assert result["status"] == "blocked"
    assert result["articles"] == []


def test_crawl_source_จำกัดจำนวนบทความตาม_max_articles():
    base = "https://example.org/en"
    pages = {base: LISTING,
             "https://example.org/en/alert-recall/first-story": ARTICLE,
             "https://example.org/en/alert-recall/second-story": ARTICLE}
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True,
              "max_articles": 1}
    result = crawl_source(source, FakeFetcher(pages))
    assert len(result["articles"]) == 1


def test_crawl_source_หยุดได้เมื่อผู้ใช้สั่งยกเลิก():
    base = "https://example.org/en"
    pages = {base: LISTING,
             "https://example.org/en/alert-recall/first-story": ARTICLE,
             "https://example.org/en/alert-recall/second-story": ARTICLE}
    source = {"name": "ทดสอบ", "url": base, "kind": "html",
              "link_pattern": r"/en/alert-recall/", "extract_full_text": True}
    result = crawl_source(source, FakeFetcher(pages), should_stop=lambda: True)

    assert result["cancelled"] is True
    assert result["articles"] == []


if __name__ == "__main__":
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            passed += 1
            print(f"  ผ่าน   {name}")
        except AssertionError as exc:
            failed += 1
            print(f"  ไม่ผ่าน {name}: {exc}")
    print(f"\nผ่าน {passed} ไม่ผ่าน {failed}")
    raise SystemExit(1 if failed else 0)
