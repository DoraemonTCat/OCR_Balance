# News Crawler — ชั้นดึงเว็บ

ดึงข่าวจากแหล่งที่ตั้งไว้ แล้วคืนข้อความที่ล้างแล้วเป็น JSON
**ไม่แตะฐานข้อมูล ไม่นับ keyword ไม่มี framework** — สองอย่างหลังเป็นงานฝั่ง backend

ตรงตามคู่มือ *News Crawler สำหรับ Junior Dev* ขั้น 1–2 (คู่มือหน้า 3)

---

## รันยังไง

```bash
pip install -r requirements.txt
```

```bash
python -m workers.crawler.cli
```

ดึงทุกแหล่งที่สถานะ `ready` (19 แหล่ง) แล้วเขียน `data/crawl/crawl_<เวลา>.json`

ตัวเลือกอื่น:

```bash
python -m workers.crawler.cli --max-articles 10
```

```bash
python -m workers.crawler.cli --only "US FDA"
```

```bash
python -m workers.crawler.cli --status all
```

เทสต์ (ไม่ยิงเว็บจริง) — รันได้เลยไม่ต้องมี pytest:

```bash
python tests/test_crawler.py
```

ถ้าติดตั้ง pytest ไว้แล้วจะใช้แบบนี้ก็ได้:

```bash
python -m pytest tests/test_crawler.py -v
```

---

## ผลรันล่าสุด

`python -m workers.crawler.cli` (ไม่จำกัดจำนวน) เมื่อ 8 ต.ค. 2026

| | |
|---|---|
| แหล่งข่าว | 19 สำเร็จ · 0 ถูกบล็อก · 0 ผิดพลาด |
| บทความ | 384 ชิ้น |
| มีเนื้อเต็ม | 252 (`text_source: article`) |
| มีแค่หัวข้อ+ย่อ | 112 (`rss_summary`) |
| ข้ามเพราะเป็นไฟล์เอกสาร | 1 (`skipped_document`) |
| ไม่มีข้อความเลย | 19 (`none`) |
| ดึงเนื้อไม่สำเร็จ | 0 |

**ตรวจสอบความถูกต้องแล้ว** สุ่ม 7 บทความจาก 7 แหล่ง ดึงสดมาเทียบ — `text` ตรงกันเป๊ะทุกตัว (ต่าง 0 ตัวอักษร)
และสุ่มข้อความจากกลางบทความไปหาในหน้าจริง เจอครบ

112 ชิ้นที่มีแค่หัวข้อคือเว็บข่าวเชิงพาณิชย์ ซึ่งตั้งใจไม่ดึงเนื้อเต็ม ดูเหตุผลใน [NEWS_SOURCES.md](NEWS_SOURCES.md)
ส่วน 19 ชิ้นที่ไม่มีข้อความเลยคือรายการที่ feed ต้นทางส่ง `<title/>` และ `<description/>` ว่างมาเอง
(CNN 2 ชิ้นเป็นคลิปวิดีโอ, drugfree.org 17 ชิ้นเป็นหน้าแนะนำองค์กร)

---

## โครงสร้างไฟล์

```
workers/crawler/          โมดูลหลัก — Python ล้วน ไม่ import django
├── __init__.py           จุดเข้าใช้งานของแพ็กเกจ
├── crawler.py            ดึงหน้าเว็บ แยก HTML/RSS และสกัดเนื้อหา
├── normalize.py          ปรับข้อความให้อยู่ในรูปมาตรฐานเดียว
├── net.py                จัดการใบรับรอง HTTPS และค่าคงที่การเชื่อมต่อ
├── runner.py             ควบคุมการรันหนึ่งรอบ ดึงหลายแหล่งพร้อมกัน
├── cli.py                ส่วนติดต่อบรรทัดคำสั่ง
└── sources.json          นิยามแหล่งข่าว 31 รายการ

tests/test_crawler.py     ชุดทดสอบ 18 รายการ ไม่เรียกเครือข่ายจริง

scripts/                  เครื่องมือเสริม ไม่ใช่ส่วนของระบบหลัก
├── survey.py             ตรวจความพร้อมของแหล่งข่าวทั้งหมด
├── probe_feeds.py        ค้นหา RSS/Atom feed ที่เว็บไม่ได้ประกาศไว้
├── probe2.py             ตรวจ feed ที่ยังค้นไม่พบในรอบแรก
├── probe_links.py        วิเคราะห์โครงสร้าง URL เพื่อกำหนด link_pattern
├── make_sample.py        สร้างไฟล์ผลลัพธ์ตัวอย่างจากแหล่งข่าวจำนวนน้อย
├── demo_match.py         สาธิตการจับคู่คำสำคัญกับผลลัพธ์
└── keywords.json         ชุดคำสำคัญสำหรับใช้ทดสอบ

docs/
├── NEWS_CRAWLER.md       เอกสารฉบับนี้
└── NEWS_SOURCES.md       รายงานผลสำรวจความพร้อมของแหล่งข่าว

data/crawl/               ไฟล์ผลลัพธ์รูปแบบ JSON (ไม่เข้า git)
```

### ฟังก์ชันหลักใน `crawler.py`

| ฟังก์ชัน | หน้าที่ |
|---|---|
| `Fetcher` | คุมมารยาทการเรียก — robots.txt, หน่วงต่อ host, timeout, User-Agent |
| `extract_links()` | หาลิงก์บทความจาก `<a href>` และจาก JSON ใน `<script>` |
| `parse_feed()` | อ่าน RSS/Atom |
| `extract_article()` | ตัดส่วนประกอบหน้าเว็บออก แล้วดึงเนื้อหา |
| `extract_title()` | ดึงหัวข้อจาก `<h1>` |
| `crawl_source()` | ดึงแหล่งข่าวหนึ่งแหล่งจนจบ |
| `BlockedError` | แยกกรณีเว็บปฏิเสธออกจากข้อผิดพลาดของระบบ |

---

## เรียกใช้จากโค้ด

โมดูลใน `workers/crawler/` ไม่ `import django` เลย เรียกจากชั้นใด ๆ ของโปรเจ็กต์ได้ทันที

**ทาง A — เรียกตรง (แนะนำ)**

```python
from workers.crawler import run, load_sources

_, sources = load_sources()
report = run(sources)          # ได้ dict ไม่ผ่านไฟล์
```

**ทาง B — อ่านไฟล์ JSON**

```bash
python -m workers.crawler.cli
```

แล้วอ่าน `data/crawl/crawl_*.json`

**เรียกทีละแหล่ง** (เช่นเมื่ออยากคุม transaction เอง)

```python
from workers.crawler import Fetcher, crawl_source

with Fetcher() as fetcher:
    result = crawl_source(source_dict, fetcher)
```

**ยกเลิกกลางรอบ** (คู่มือหน้า 4)

```python
from workers.crawler import Canceller, run

c = Canceller()
# ... อีกเธรดเรียก c.cancel() ...
report = run(sources, canceller=c)
```

`crawler` จะเช็กก่อนดึงบทความแต่ละชิ้น จึงหยุดได้โดยไม่ต้องรอจนจบ

---

## หน้าตาผลลัพธ์

```json
{
  "run_at": "2026-10-08T13:09:00+07:00",
  "normalized": true,
  "status": "SUCCESS",
  "cancelled": false,
  "summary": {
    "sources_total": 19, "ok": 19, "blocked": 0, "error": 0,
    "articles_total": 186, "articles_with_full_text": 126
  },
  "sources": [{
    "name": "US FDA - Recalls & Alerts",
    "url": "https://www.fda.gov/.../recalls/rss.xml",
    "kind": "rss",
    "group": "regulator",
    "status": "ok",
    "error": null,
    "cancelled": false,
    "articles": [{
      "url": "https://www.fda.gov/safety/recalls.../pacific-health-sciences...",
      "title": "Pacific Health Sciences Voluntarily Recalls One Lot of...",
      "published_at": "2026-10-06T13:09:00-04:00",
      "summary": "Pacific Health Sciences is voluntarily recalling one lot...",
      "text": "Summary Company Announcement Date: October 06, 2026 ...",
      "text_source": "article",
      "fetched_at": "2026-10-08T13:09:12+07:00",
      "error": null
    }]
  }]
}
```

### ช่องที่ต้องรู้

**`text_source`** บอกว่าข้อความมาจากไหน — ใช้ค่านี้ตีความผลการนับ keyword

| ค่า | หมายถึง |
|---|---|
| `article` | เนื้อข่าวเต็ม (เว็บหน่วยงานรัฐ) |
| `rss_summary` | แค่หัวข้อกับย่อข่าว (เว็บข่าวเชิงพาณิชย์) |
| `skipped_document` | ลิงก์ชี้ไปไฟล์ PDF/Word ไม่ได้ดึง — ดู `error` |
| `none` | ไม่มีข้อความเลย feed ต้นทางส่งมาว่าง |

ถ้าไม่ดูช่องนี้ จะงงว่าทำไมข่าว BBC เจอ keyword น้อยกว่าข่าว FDA — เพราะข้อความสั้นกว่ามาก ไม่ใช่เพราะเนื้อหาไม่เกี่ยว

**`status` ของแต่ละแหล่ง** มี 3 ค่า

| ค่า | หมายถึง |
|---|---|
| `ok` | ดึงสำเร็จ |
| `blocked` | เว็บปฏิเสธ (`robots.txt` ห้าม หรือ HTTP 401/403/429) — **ไม่ใช่ระบบพัง** |
| `error` | ผิดพลาดจริง เช่น timeout |

รอบไหนมีอย่างน้อยหนึ่งแหล่งสำเร็จ = `SUCCESS` ไม่มีเลย = `FAILED` (คู่มือหน้า 3 ขั้น 4)

**`normalized: true`** — `title`, `summary`, `text` ล้างมาแล้วทั้งหมด เอาไปแมพ keyword ได้เลย ไม่ต้องล้างซ้ำ

---

## การหาลิงก์บทความ

`extract_links()` ค้น 2 ที่ (คู่มือหน้า 3 ขั้น 1):

1. **`<a href>`** — ลิงก์ปกติในหน้า
2. **`<script>` / JSON** — บางเว็บโหลดรายการข่าวผ่าน JavaScript ลิงก์จึงไม่อยู่ใน `<a href>` แต่ฝังเป็นข้อความใน JSON ที่ติดมากับหน้า

แต่ละลิงก์มีช่อง `found_in` บอกว่ามาจาก `anchor` หรือ `script`

ตัวกรองที่ใส่ไว้: ตัดไฟล์ประกอบหน้าเว็บทิ้ง (`.js`, `.css`, รูป, ฟอนต์) และตัดโฟลเดอร์อย่าง
`/wp-content/`, `/static/`, `/_next/` เพราะเว็บที่ไม่ได้ตั้ง `link_pattern` จะได้ขยะพวกนี้ปนมา

> **สถานะปัจจุบัน:** ค้น `<script>` แล้ว **ยังไม่ได้ลิงก์เพิ่มจาก 13 แหล่ง HTML ที่ใช้อยู่** (429 ลิงก์เท่าเดิม)
> เพราะเว็บที่โหลดข่าวด้วย JavaScript จริง ๆ อย่าง BBC เราใช้ RSS แทนไปแล้ว ส่วน EMA ตรวจแล้วพบว่า
> หน้าแรกมีลิงก์ข่าวแค่ 6 ลิงก์จริง ไม่ได้ซ่อนไว้ในสคริปต์
>
> ฟีเจอร์นี้จึงเป็นตาข่ายรองรับสำหรับแหล่งข่าวที่จะเพิ่มเข้ามาทีหลัง มีเทสต์ยืนยันว่าทำงานได้จริง

## เรื่อง normalize ที่ต้องรู้

`normalize_text()` ทำ 4 อย่าง:

1. **NFC** — รวมสระ/วรรณยุกต์ที่แยกส่วน
2. **รวม "ํา" กับ "ำ"** — NFC ทำให้ไม่ได้ Unicode ไม่ถือว่าเทียบเท่า ต้องแทนที่เอง
3. **ลบ zero-width space** — ไม่ใช่แปลงเป็นช่องว่าง
4. **ยุบช่องว่างซ้ำ**

> **ต่างจาก `workers/normalizer.py` ตรงข้อ 3** ของเดิมแปลง zero-width space เป็นช่องว่าง
> ซึ่งเหมาะกับงานเทียบข้อความ OCR แต่ทำให้ keyword ไทยพัง เพราะภาษาไทยไม่มีช่องว่างระหว่างคำ
>
> `"เรียก<ZWSP>เก็บคืนยา"` → แปลงเป็นช่องว่างได้ `"เรียก เก็บคืนยา"` แล้ว keyword `"เรียกเก็บคืนยา"` จับไม่ได้

---

## มารยาทต่อเว็บปลายทาง

`Fetcher` คุมให้อัตโนมัติ (คู่มือหน้า 3):

- ตรวจ `robots.txt` ก่อนทุก URL แล้ว cache ผลต่อ host
- หน่วง 1 วินาทีระหว่าง request ที่ไป host เดียวกัน เว็บต่างกันดึงพร้อมกันได้
- timeout 20 วินาที · `User-Agent: HerbRiskBot/1.0`
- 401/403/429 = `BlockedError` ไม่ใช่ error ธรรมดา

มีแฟล็ก `--no-robots` ไว้ตอนทดสอบเท่านั้น **ห้ามใช้ตอนรันจริง**

---

## เรื่อง HTTPS บนเครื่อง Windows

เครื่องที่มีแอนตี้ไวรัสดักตรวจ HTTPS (เช่น Avast) จะสวมใบรับรองของตัวเองเข้ามา
ซึ่งไม่อยู่ใน `certifi` ที่ Python ใช้โดยปริยาย ทำให้ขึ้น `CERTIFICATE_VERIFY_FAILED`
ทั้งที่เว็บปลายทางปกติดี

แก้ด้วย `truststore` (อยู่ใน `requirements.txt`) ให้ Python อ่าน certificate store ของ Windows
**ไม่ได้แก้ด้วย `verify=False`** เพราะนั่นคือการปิดการตรวจใบรับรองทิ้งทั้งหมด

---

## งานที่ยังไม่ได้ทำ (เป็นของฝั่ง backend)

- ตาราง `SearchKeyword`, `CrawlSource`, `KeywordSource`, `AnalysisLog`
- การนับ keyword
- ตั้งเวลา 03:00 (APScheduler) · API endpoint · UI

## แหล่งข่าวที่ยังค้าง

- **ต้องแก้ URL ก่อน 6 แหล่ง** — UNODC, INCB, ONDCP, Hong Kong MDD, สบส., Erowid
- **เข้าไม่ได้ 6 แหล่ง** — Reuters (`robots.txt` ห้าม), EMA News Search, DEA, EIN News, TGA ×2

รายละเอียดทั้งหมดอยู่ใน [NEWS_SOURCES.md](NEWS_SOURCES.md)
