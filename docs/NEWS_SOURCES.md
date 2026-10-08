# เฟส 0 — รายงานผลสำรวจแหล่งข่าว

สำรวจเมื่อ **8 ต.ค. 2026** · แหล่งข่าว **31 รายการ** จากไฟล์ `ลิงค์ข่าว.docx`

---

## สรุปผลใน 1 บรรทัด

> **21 เว็บพร้อมใช้ / 1 เว็บต้องตัดสินใจ / 9 เว็บเข้าไม่ได้**
> ในจำนวนที่ใช้ได้ **12 เว็บใช้ RSS** ซึ่งทำให้งานง่ายลงมาก อีก 9 เว็บต้องแกะ HTML

> อัปเดตหลังแก้แหล่งข่าวรอบที่สอง: UNODC กับ INCB เปลี่ยนไปใช้หน้า press release
> โดยตรงจึงใช้งานได้แล้ว ส่วน Hong Kong MDD, สบส. และ Erowid ย้ายไปกลุ่มเข้าไม่ได้
> ดูเหตุผลรายแหล่งใน `workers/crawler/sources.json` ช่อง `note`

---

## สิ่งที่สำรวจ

ยิง request จริงไปทุกเว็บ เช็ก 4 อย่าง ตามมารยาทที่คู่มือกำหนด (เช็ก `robots.txt` ก่อน, หน่วง 1 วิ/host, timeout 20 วิ, `User-Agent: HerbRiskBot/1.0`)

1. `robots.txt` อนุญาตให้เราเข้าไหม
2. ยิง GET แล้วได้อะไรกลับมา — 200, หรือโดน 401/403/429 (BLOCKED)
3. มี RSS/Atom feed ให้ใช้แทนการแกะ HTML ไหม (ลองทั้ง autodiscovery และเดา URL 35 เส้น)
4. HTML ที่ได้มา เห็นลิงก์บทความเลย หรือต้องรอ JavaScript โหลด

---

## ผลแยกเป็น 3 กลุ่ม

### กลุ่ม 1 — พร้อมใช้ (19 เว็บ)

**ใช้ RSS ได้เลย — 12 เว็บ**

| เว็บ | feed | จำนวนรายการ |
|---|---|---|
| US FDA – Recalls & Alerts | `.../rss-feeds/recalls/rss.xml` | 20 |
| US FDA – MedWatch | `.../rss-feeds/medwatch/rss.xml` | 20 |
| US FDA – Drug Safety | `.../rss-feeds/drugs/rss.xml` | 20 |
| US FDA – Biologics Recalls | `.../rss-feeds/biologics/rss.xml` | 20 |
| US FDA – Press Releases | `.../rss-feeds/press-releases/rss.xml` | 20 |
| UK MHRA | `gov.uk/drug-device-alerts.atom` | 50 |
| PMDA Japan | `pmda.go.jp/rss_008.xml` | 45 |
| BBC Health | `feeds.bbci.co.uk/news/health/rss.xml` | 52 |
| CNN Health | `rss.cnn.com/rss/cnn_health.rss` | 29 |
| ABC News Health | `feeds.abcnews.com/abcnews/healthheadlines` | 25 |
| NY Times Health | `rss.nytimes.com/.../nyt/Health.xml` | 20 |
| InfoQuest | `infoquest.co.th/feed` | 10 |

**ต้องแกะ HTML — 7 เว็บ** (หา `link_pattern` ได้แล้วทุกตัว)

ตัวเลขคือจำนวนลิงก์ที่ `link_pattern` คัดได้จริง (ยืนยันด้วย `survey.py` แล้ว)

| เว็บ | link_pattern | ลิงก์ที่ตรง pattern |
|---|---|---|
| Health Canada – Recalls | `/en/alert-recall/` | 11 จาก 11 — สะอาดที่สุด |
| US FDA – Device Safety | `/medical-devices/medical-device-recalls-and-early-alerts/` | 26 จาก 68 |
| NIDA – News Releases | `/news-events/news-releases/` | 30 จาก 91 |
| drugfree.org | `/article/` | 13 จาก 33 |
| HSA Singapore | `/announcements/[^/]+/?$` | 10 จาก 12 |
| US FDA – Letters to HCP | `/medical-devices/letters-health-care-providers/` | 10 จาก 41 |
| EMA – News | `/en/news/` | 6 จาก 94 — น้อยสุด เพราะดึงจากหน้าแรก |

### กลุ่ม 2 — ต้องแก้ URL ก่อน (6 เว็บ)

เข้าเว็บได้ปกติ แต่ URL ที่ให้มาเป็นหน้าแรกหรือหน้าเมนู ไม่ใช่หน้ารวมข่าว ถ้าดึงตามนี้จะได้แต่ลิงก์ขยะ

| เว็บ | ปัญหา | ทางแก้ |
|---|---|---|
| Hong Kong MDD | หน้าแรกมีลิงก์แค่ 4 ตัว เป็นเมนูล้วน | เปลี่ยนไป `/en/whats-new/index.html` |
| UNODC | หน้าแรกให้ลิงก์ 263 ตัว ปนทุกภูมิภาค/ภาษา | หาหน้า press release เฉพาะ |
| INCB | ลิงก์ 178 ตัว ส่วนใหญ่เป็นหน้าสถาบัน | หาหน้า news/press |
| ONDCP | หน้า ondcp แทบไม่มีข่าว | ข่าวจริงอยู่ `/releases/<ปี>/` แต่เป็นข่าวทำเนียบรวม |
| สบส. | ลิงก์ 15 ตัวเป็น `page/*.php` ดูเหมือนหน้าเมนู | ต้องหาหน้ารวมข่าวจริง |
| Erowid | หน้าแรกมีแต่ลิงก์บริจาค | เว็บนี้เป็นคลังข้อมูล ไม่ใช่สำนักข่าว — ควรถามพี่ว่าต้องการส่วนไหน |

### กลุ่ม 3 — เข้าไม่ได้ (6 เว็บ)

| เว็บ | อาการ | หมายเหตุ |
|---|---|---|
| **Reuters** | `robots.txt` **ห้ามดึง** + ตอบ 401 ทุก UA + RSS 404 | ⛔ **ต้องไม่ดึง** นี่ไม่ใช่ปัญหาเทคนิค แต่เป็นการที่เจ้าของเว็บบอกว่าไม่อนุญาต |
| EMA – News Search | 403 ด้วย UA เรา, 401 ด้วย browser UA | ใช้ EMA – News แทนได้ |
| US DEA | 403 ทุก UA ทั้งหน้าเว็บและ RSS | เป็นเว็บรัฐ น่าจะติด WAF — ลองขอ whitelist ได้ |
| EIN News | 403 ทุก UA | เป็นบริการสมัครสมาชิก อาจต้องใช้ API ที่เสียเงิน |
| TGA – Alerts | timeout / connection error ทุก UA | น่าจะบล็อกระดับเครือข่าย ลองใหม่จาก IP อื่น |
| TGA – DRAC | 200 แต่มีแท็ก `<a>` แค่ 2 ตัว | เป็นฟอร์มค้นหา ASP.NET ต้อง POST พร้อม ViewState — ไม่ใช่หน้ารวมข่าว |

---

## 5 เรื่องที่ค้นพบแล้วเปลี่ยนแผน

**1. NY Times — หน้าเว็บ 403 แต่ RSS เปิดปกติ**
ถ้าไม่ลอง RSS จะตัดทิ้งไปเลยทั้งที่ใช้ได้ บทเรียนคือ **เว็บบล็อก HTML ไม่ได้แปลว่าบล็อก feed**

**2. GOV.UK — HTML ใช้ไม่ได้เลย แต่ Atom ให้ 50 รายการ**
หน้า `gov.uk/drug-device-alerts` คืนลิงก์เมนูรวมของทั้ง GOV.UK (`/browse/childcare-parenting` ฯลฯ) ไม่มีลิงก์ alert เลย ถ้าเขียน HTML scraper จะงงมากว่าทำไมไม่เจอข่าว

**3. BBC เป็น Next.js — ลิงก์ข่าวไม่อยู่ใน HTML ดิบ**
BeautifulSoup อ่านไม่เห็น แต่ RSS แก้ปัญหานี้ได้หมด

**4. FDA มี RSS 5 feed แต่ไม่ประกาศใน `<link rel="alternate">`**
autodiscovery หาไม่เจอสักอัน ต้องเดา URL เอง — ส่วน Device Safety กับ Letters to HCP ไม่มี feed จริงๆ (404) ต้องแกะ HTML

**5. HSA Singapore หน้าเดียว 3.5 MB**
ใหญ่กว่าเว็บอื่นเกือบ 100 เท่า ต้องเผื่อเวลาและหน่วยความจำตอนรันจริง

---

## ไฟล์ที่ได้

```
D:\Web_scraping\
├── sources.json              ← config แหล่งข่าว 31 รายการ (ไฟล์หลัก ใช้ต่อในเฟสถัดไป)
├── SURVEY.md                 ← รายงานฉบับนี้
├── survey.py                 ← สคริปต์สำรวจ: robots/HTTP/RSS/JS
├── probe_feeds.py            ← ไล่ทดสอบ URL ที่คาดว่าเป็น feed 35 เส้น
├── probe2.py                 ← ตามเก็บ feed ที่ยังหาไม่เจอ
├── probe_links.py            ← วิเคราะห์โครงสร้างลิงก์เพื่อหา link_pattern
└── output/
    ├── survey_report.json    ← ผลดิบรายเว็บ
    └── feed_probe.json       ← ผลดิบการทดสอบ feed
```

`sources.json` คือไฟล์สำคัญที่สุด ทุกฟิลด์ตรงกับตาราง `CrawlSource` ในคู่มือหน้า 2 (`url`, `kind`, `link_pattern`, `max_articles`) วันที่ย้ายเข้า DB จริงจะ map ได้ตรงๆ ไม่ต้องรื้อ

---

## วิธีรัน

ติดตั้ง library ก่อน (เครื่องนี้มีครบแล้ว):

```bash
pip install httpx beautifulsoup4 lxml
```

รันสำรวจใหม่ทั้งหมด — ใช้เวลาประมาณ 2-3 นาที:

```bash
python survey.py
```

สคริปต์อ่าน `sources.json` แล้วเช็กตาม `kind` ของแต่ละแหล่ง — ถ้าเป็น `rss` จะนับจำนวนรายการใน feed ถ้าเป็น `html` จะนับลิงก์ที่ `link_pattern` คัดได้ แล้วพิมพ์ตารางสรุปออกหน้าจอ

สิ่งที่ต้องดูในผลลัพธ์:

- **`[!] parser_mismatch`** — `kind` ใน config ไม่ตรงกับของจริง เช่นตั้งว่าเป็น `rss` แต่เว็บส่ง HTML กลับมา แปลว่า feed นั้นตายหรือย้าย URL แล้ว
- **`<- config: needs_fix`** — เข้าเว็บได้ (HTTP 200) แต่ยังไม่พร้อมใช้ อย่าเข้าใจผิดว่าใช้ได้แล้ว
- **จำนวนลิงก์ที่ตรง pattern ลดลงผิดปกติ** — เว็บอาจเปลี่ยนโครงสร้าง URL ต้องแก้ `link_pattern`

ผลรันล่าสุด: ใช้ได้ 26 · ถูกบล็อก 4 · ผิดพลาด 1 · รายการข่าวจาก feed รวม 331 รายการ

ไล่ทดสอบ RSS feed:

```bash
python probe_feeds.py
```

ดูโครงสร้างลิงก์ของเว็บที่ต้องแกะ HTML (ใช้ตอนหา `link_pattern` ของเว็บใหม่):

```bash
python probe_links.py
```

> สคริปต์ทุกตัวยิงเว็บจริง มีหน่วง 1 วินาทีต่อ host อยู่แล้ว **อย่าลบการหน่วงออก** ไม่งั้นเสี่ยงโดนแบน IP

---

## ขั้นต่อไป

**เฟส 0.5 — เคาะหน้าตา JSON แล้วส่งตัวอย่างให้พี่** (ควรทำก่อนเขียนโค้ด)
ทำไฟล์ JSON ปลอม 1 ไฟล์ตามสเปกที่ตกลงกัน ส่งให้พี่เริ่มงานฝั่งแมพ keyword ขนานไปได้เลย ถ้าหน้าตาผิดจะรู้ตั้งแต่วันแรก

**เฟส 1** — ดึง 1 บทความ → ข้อความสะอาด (httpx + BeautifulSoup + `normalize_text`)
**เฟส 2** — รองรับ RSS (12 เว็บ) + หาลิงก์จากหน้ารวม (7 เว็บ)
**เฟส 3** — มารยาท: robots.txt, หน่วง, timeout, UA
**เฟส 4** — รันทุกเว็บพร้อมกัน + สถานะ ok/error/blocked + เขียนไฟล์ JSON
**เฟส 5** — เทสต์ด้วย FakeFetcher

---

## 4 เรื่องที่ต้องถามพี่

1. **Reuters** — `robots.txt` ห้ามดึง ผมไม่ดึงให้ ต้องให้พี่ตัดสินใจว่าจะตัดทิ้งหรือไปขออนุญาต/ซื้อ feed
2. **DEA / EIN News / TGA** — 3 เว็บนี้บล็อกเรา จะตัดทิ้ง หรือจะลองขอ whitelist จากหน่วยงาน
3. **6 เว็บกลุ่ม needs_fix** — ช่วยยืนยันว่าต้องการข่าวส่วนไหนของเว็บนั้น ผมจะได้หา URL ที่ถูก
4. **Erowid** — เป็นคลังข้อมูลยาเสพติด ไม่มีหน้าข่าว อยากได้ส่วนไหนกันแน่
