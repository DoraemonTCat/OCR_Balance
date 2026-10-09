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


def is_thai(text):
    return bool(_THAI_RE.search(text or ""))


def _compile_one(term):
    """สร้าง regex สำหรับคำเดียว เลือกกติกาตามภาษาของคำนั้น"""
    term = term.strip()
    if not term:
        return None
    if is_thai(term):
        # ไทยไม่มีช่องว่างระหว่างคำ ขอบเขตคำแบบ \b ใช้ไม่ได้ จึงจับแบบ substring
        return re.compile(re.escape(term), re.IGNORECASE)
    # อังกฤษ: ตรงทั้งคำ ยอมให้ลงท้าย s หรือ es และยอมให้ช่องว่างกลางวลีเป็นกี่ตัวก็ได้
    #
    # ใช้ (?<!\w) กับ (?!\w) แทน \b เพราะ \b ต้องการให้ขอบของคำเป็นตัวอักษร
    # คำที่ขึ้นต้นหรือลงท้ายด้วยเครื่องหมาย เช่น "INS: 101(iii)" จะหาไม่เจอถ้าใช้ \b
    body = r"\s+".join(re.escape(word) for word in term.split())
    return re.compile(rf"(?<!\w){body}(?:es|s)?(?!\w)", re.IGNORECASE)


def compile_keyword(keyword):
    """สร้างรายการ regex สำหรับ keyword หนึ่งคำ

    keyword เป็นสตริง หรือ dict ที่มีคีย์ ``keyword`` ก็ได้
    dict อาจมี ``full_form`` หรือ ``aliases`` เพิ่มเพื่อให้จับคำที่เขียนได้หลายแบบ

    คืน (ชื่อที่ใช้รายงาน, รายการ regex)
    """
    if isinstance(keyword, dict):
        name = keyword.get("keyword") or keyword.get("keysword") or ""
        extras = [keyword.get("full_form"), *(keyword.get("aliases") or [])]
    else:
        name, extras = str(keyword), []

    terms = [name]
    match = _PARENTHETICAL_RE.match(name.strip())
    if match:
        terms = [match.group("phrase"), match.group("inner")]
    terms.extend(e for e in extras if e)

    patterns = [p for p in (_compile_one(t) for t in terms) if p is not None]
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
    """
    parts = [article.get("title"), article.get("summary"), article.get("text")]
    return " ".join(p for p in parts if p)


def count_keywords(articles, keywords):
    """นับทุก keyword กับบทความทั้งชุด คืน {ชื่อ keyword: จำนวนครั้ง}

    ดึงเว็บครั้งเดียวแล้วนับทุกคำในรอบเดียว ไม่วนดึงใหม่ทีละคำ
    การดึงเว็บคือส่วนที่ช้า ส่วนการนับทำบนข้อความที่อยู่ในหน่วยความจำแล้ว
    """
    compiled = [compile_keyword(k) for k in keywords]
    totals = {name: 0 for name, _ in compiled}

    for article in articles:
        text = normalize_text(article_text(article))
        if not text:
            continue
        for name, patterns in compiled:
            totals[name] += count_in(text, patterns)

    return totals
