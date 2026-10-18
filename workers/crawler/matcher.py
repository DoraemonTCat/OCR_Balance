"""นับจำนวนครั้งที่คำสำคัญปรากฏในข้อความ (คู่มือหน้า 3 ขั้น 3)

เป็น Python ล้วน ไม่แตะเครือข่ายและไม่แตะฐานข้อมูล จึงเทสต์แยกได้
รับข้อความที่ล้างแล้วจาก normalize.py เข้ามา คืนจำนวนครั้ง

กติกาตามคู่มือ:
  - ไม่สนตัวพิมพ์เล็กใหญ่
  - คำอังกฤษต้องตรงทั้งคำ และนับรูปพหูพจน์ -s / -es ด้วย
  - "วลี (ตัวย่อ)" นับทั้งวลีเต็มและตัวย่อ
  - ภาษาไทยไม่มีช่องว่างระหว่างคำ จึงจับคู่แบบ substring
"""
from __future__ import annotations

import re

from .normalize import normalize_text

#: คำสำคัญที่มีวงเล็บต่อท้าย เช่น
#:   "Herbal adverse reactions (HARs)"      วงเล็บเป็นตัวย่อ
#:   "Nitrosamines (กลุ่มสารไนโตรซามีน)"     วงเล็บเป็นคำแปล
#:
#: ทั้งสองแบบควรนับทั้งส่วนหน้าและส่วนในวงเล็บแยกกัน ไม่ใช่นับทั้งก้อน
#: เพราะข่าวจริงเขียนอย่างใดอย่างหนึ่ง ไม่มีใครเขียนวงเล็บติดมาด้วย
#:
#: บังคับให้มีช่องว่างหน้าวงเล็บ เพื่อไม่ให้ไปตัดคำที่วงเล็บเป็นส่วนหนึ่งของตัวมันเอง
#: อย่างรหัส "INS: 101(iii)" ซึ่งแยกแล้วจะเหลือ "iii" ที่ไปจับมั่วได้
_PARENTHETICAL_RE = re.compile(r"^(?P<phrase>.{2,}?)\s+\((?P<inner>[^)]{2,60})\)\s*$")

#: อักษรไทยหนึ่งตัวขึ้นไป
_THAI_RE = re.compile(r"[฀-๿]")

#: ตัวย่อตัวพิมพ์ใหญ่สั้น ๆ คำเดียว อย่าง NDMA, AZBT, HARs
#: สั้นพอที่จะไปโผล่เป็นส่วนหนึ่งของชื่อยาได้โดยบังเอิญ จึงต้องคุมขอบเขตให้แน่นกว่าปกติ
_ABBREV_RE = re.compile(r"^[A-Z0-9]{2,6}$")


def is_thai(text):
    return bool(_THAI_RE.search(text or ""))


def _guards(term, excludes):
    """lookaround ที่กันคำยกเว้น สร้างจากคำยกเว้นที่มี term อยู่ข้างใน

    ไทยจับแบบ substring จึงมีคำสั้นที่ไปโผล่กลางคำอื่นได้ ที่เจอจริงคือ
    "ไอซ์" (ชื่อเรียกเมทแอมเฟตามีน) ไปตรงกับ "ไอซ์แลนด์" ในข่าวการค้าเสรี
    และ "ยาอี" ไปตรงกับ "ยาอีก"

    คำยกเว้น "ไอซ์แลนด์" จะกลายเป็น  ไอซ์(?!แลนด์)
    คำยกเว้น "สารไอซ์"   จะกลายเป็น  (?<!สาร)ไอซ์
    ทั้งสองฝั่งเป็นข้อความคงที่ จึงใช้ lookbehind ของ Python ได้
    """
    before, after = "", ""
    for raw in excludes:
        index = raw.find(term)
        if index < 0:                 # ไม่ได้คลุม term จึงกันอะไรไม่ได้
            continue
        head, tail = raw[:index], raw[index + len(term):]
        if head:
            before += rf"(?<!{re.escape(head)})"
        if tail:
            after += rf"(?!{re.escape(tail)})"
    return before, after


def _compile_one(term, excludes=()):
    """สร้าง regex สำหรับคำเดียว เลือกกติกาตามภาษาของคำนั้น"""
    term = term.strip()
    if not term:
        return None
    guard_before, guard_after = _guards(term, excludes)
    if is_thai(term):
        # ไทยไม่มีช่องว่างระหว่างคำ ขอบเขตคำแบบ \b ใช้ไม่ได้ จึงจับแบบ substring
        return re.compile(rf"{guard_before}{re.escape(term)}{guard_after}", re.IGNORECASE)
    # อังกฤษ: ตรงทั้งคำ ยอมให้ลงท้าย s หรือ es
    #
    # เชื่อมคำในวลีด้วย [\s\-]+ เพราะข่าวเขียนทั้ง "drug recall" และ "drug-recall"
    #
    # ขอบเขตคำใช้ (?<![A-Za-z0-9]) ไม่ใช่ \b และไม่ใช่ (?<!\w) เพราะ
    #   \b  ต้องการให้ขอบของคำเป็นตัวอักษร คำที่ขึ้นหรือลงท้ายด้วยเครื่องหมาย
    #       อย่าง "INS: 101(iii)" จะหาไม่เจอ
    #   \w  นับอักษรไทยเป็นตัวอักษรด้วย ทำให้คำอังกฤษที่ติดกับคำไทย
    #       อย่าง "ตรวจพบสารNDMAในยา" หาไม่เจอ ซึ่งข่าวไทยเขียนแบบนี้เป็นปกติ
    body = r"[\s\-]+".join(re.escape(word) for word in term.split())
    before, after = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
    if _ABBREV_RE.match(term):
        # ตัวย่อสั้น ๆ ห้ามไปเกาะท้ายคำประสมที่มียัติภังค์ เพราะชื่อยาเขียนแบบนั้น
        # "Kisunla donanemab-azbt" ไม่ใช่การพูดถึงสาร AZBT
        # แต่ยังยอมให้ขึ้นต้นคำประสมได้ เพราะ "NDMA-contaminated" คือข่าวจริง
        before += r"(?<![A-Za-z0-9]-)"
    return re.compile(rf"{before}{guard_before}{body}(?:es|s)?{guard_after}{after}",
                      re.IGNORECASE)


def compile_keyword(keyword):
    """สร้างรายการ regex สำหรับ keyword หนึ่งคำ

    keyword เป็นสตริง หรือ dict ที่มีคีย์ ``keyword`` ก็ได้
    dict อาจมี ``full_form`` หรือ ``aliases`` เพิ่มเพื่อให้จับคำที่เขียนได้หลายแบบ
    และ ``exclude`` เป็นรายการคำที่ห้ามนับ เช่น
        {"keyword": "ไอซ์", "exclude": ["ไอซ์แลนด์"]}
    เพราะคำไทยสั้น ๆ ไปโผล่กลางคำอื่นได้ (ดู _guards)

    คืน (ชื่อที่ใช้รายงาน, รายการ regex)
    """
    if isinstance(keyword, dict):
        name = keyword.get("keyword") or keyword.get("keysword") or ""
        extras = [keyword.get("full_form"), *(keyword.get("aliases") or [])]
        excludes = tuple(keyword.get("exclude") or ())
    else:
        name, extras, excludes = str(keyword), [], ()

    terms = [name]
    match = _PARENTHETICAL_RE.match(name.strip())
    if match:
        terms = [match.group("phrase"), match.group("inner")]
    terms.extend(e for e in extras if e)

    patterns = [p for p in (_compile_one(t, excludes) for t in terms) if p is not None]
    return name, patterns


def count_in(text, patterns):
    """นับจำนวนครั้งที่ regex ชุดนี้เจอในข้อความ"""
    if not text:
        return 0
    return sum(len(p.findall(text)) for p in patterns)


def article_text(article):
    """รวมทุกช่องข้อความของบทความหนึ่งชิ้นเป็นก้อนเดียวเพื่อค้นหา

    ``text`` เป็น None ได้ (แหล่งที่ไม่ดึงเนื้อเต็ม) จึงต้องกรองออกก่อน
    ไม่งั้นจะพังตอน join

    ข้ามหัวข้อหรือเกริ่นที่มีอยู่ในเนื้อหาแล้ว เพราะเว็บส่วนใหญ่พิมพ์พาดหัว
    ไว้ต้นเนื้อหาด้วย ถ้าต่อเข้าไปอีกเท่ากับนับคำในพาดหัวสองรอบ
    """
    body = article.get("text") or ""
    parts = [p for p in (article.get("title"), article.get("summary"))
             if p and p not in body]
    parts.append(body)
    return " ".join(p for p in parts if p)


def find_spans(text, compiled):
    """ตำแหน่งที่เจอทั้งหมดในข้อความ เป็น (เริ่ม, จบ, ชื่อ keyword)

    เรียงตามตำแหน่งเริ่ม ตำแหน่งเดียวกันให้คำที่ยาวกว่ามาก่อน
    เพื่อให้ resolve_overlaps ตัดสินได้ด้วยการไล่อ่านครั้งเดียว
    """
    spans = [
        (match.start(), match.end(), name)
        for name, patterns in compiled
        for pattern in patterns
        for match in pattern.finditer(text)
    ]
    spans.sort(key=lambda span: (span[0], span[0] - span[1]))
    return spans


def resolve_overlaps(spans):
    """คำที่ซ้อนอยู่ในคำอื่น ให้นับเข้าคำที่ยาวที่สุดคำเดียว

    ไทยไม่เว้นวรรคจึงต้องจับแบบ substring (ดู _compile_one) ผลข้างเคียงคือ
    คำสำคัญที่ซ้อนกันในรายการเดียวจะนับข้อความชิ้นเดียวซ้ำหลายรอบ:

        "ยาเสพติดให้โทษ"  ⊃  "ยาเสพติด"  ⊃  "เสพติด"

    ข้อความหนึ่งครั้งจึงกลายเป็นสามครั้ง ในการทดลองจริงคำว่า "เสพติด" นับได้
    140 ครั้งโดยไม่มีครั้งไหนเป็นของตัวเองเลย ยอดรวมเลยเฟ้อจนใช้ตัดสินใจไม่ได้

    รายงานควรนับเข้าคำที่เจาะจงที่สุด ไม่ใช่กระจายไปทุกคำที่ซ้อนกันอยู่
    เงื่อนไขที่ใช้คือ "จบไม่เกินจุดที่คำก่อนหน้าคลุมถึงแล้ว" ซึ่งตัดเฉพาะคำที่
    ถูกคลุมทั้งคำ ส่วนคำที่เหลื่อมกันบางส่วน (คนละข้อความ) ยังนับทั้งคู่
    """
    kept, covered = [], -1
    for start, end, name in spans:
        if end <= covered:
            continue
        kept.append((start, end, name))
        covered = end
    return kept


def count_keywords(articles, keywords, compiled=None, resolve=True):
    """นับทุก keyword กับบทความทั้งชุด คืน {ชื่อ keyword: จำนวนครั้ง}

    ดึงเว็บครั้งเดียวแล้วนับทุกคำในรอบเดียว ไม่วนดึงใหม่ทีละคำ
    การดึงเว็บคือส่วนที่ช้า ส่วนการนับทำบนข้อความที่อยู่ในหน่วยความจำแล้ว

    compiled: ผลของ compile_keyword ที่เตรียมไว้แล้ว ส่งมาได้เมื่อต้องเรียก
              ฟังก์ชันนี้หลายรอบ จะได้ไม่คอมไพล์ regex ชุดเดิมซ้ำทุกรอบ
    resolve:  ตัดการนับซ้อน (ดู resolve_overlaps) ปิดได้เมื่ออยากได้ยอดดิบ
    """
    if compiled is None:
        compiled = [compile_keyword(k) for k in keywords]
    totals = {name: 0 for name, _ in compiled}

    for article in articles:
        text = normalize_text(article_text(article))
        if not text:
            continue
        spans = find_spans(text, compiled)
        if resolve:
            spans = resolve_overlaps(spans)
        for _, _, name in spans:
            totals[name] += 1

    return totals
