"""ล้างข้อความก่อนส่งออก เพื่อให้แมพ keyword ได้ถูกต้อง

ทำไมต้องมีไฟล์นี้ (คู่มือหน้า 3 ขั้น 3):
ข้อความภาษาไทยจากเว็บต่าง ๆ สะกดคำเดียวกันได้หลายแบบในระดับ Unicode
ถ้าไม่ล้างให้เป็นรูปเดียวกันก่อน ฝั่งที่เอาไปแมพ keyword จะหาไม่เจอทั้งที่ข้อความมีอยู่จริง

ฟังก์ชันนี้ทำงานให้ผลเหมือน workers/normalizer.py ในโปรเจ็กต์ backend ของพี่
(ocr-back-end) โดยตั้งใจ ยกเว้นเรื่องเดียวคือ zero-width space ดูหมายเหตุด้านล่าง
"""
import re
import unicodedata

#: นิคหิต (U+0E4D) + สระอา (U+0E32) คือการสะกด "ำ" แบบแยกส่วน
#: Unicode ไม่ถือว่าเทียบเท่ากับ "ำ" (U+0E33) ดังนั้น NFC เพียงอย่างเดียว *แก้ไม่ได้*
#: ต้องแทนที่เองเท่านั้น ไม่งั้น "สารทําให้คงตัว" กับ "สารทำให้คงตัว" จะเป็นคนละคำ
_SARA_AM_DECOMPOSED = "ํา"
_SARA_AM = "ำ"

#: อักขระความกว้างศูนย์ที่มองไม่เห็นแต่ทำให้การจับคู่พัง
#:
#: ตรงนี้ต่างจาก workers/normalizer.py ของพี่ ซึ่งแปลงอักขระพวกนี้เป็นช่องว่าง
#: เราเลือก "ลบทิ้ง" เพราะภาษาไทยไม่มีช่องว่างระหว่างคำ การแทรกช่องว่างเข้าไปกลางคำ
#: จะทำให้ keyword ไทยที่จับคู่แบบ substring หาไม่เจอ
#: เช่น "เรียก<ZWSP>เก็บคืนยา" ถ้าแปลงเป็นช่องว่างจะกลายเป็น "เรียก เก็บคืนยา"
#: แล้ว keyword "เรียกเก็บคืนยา" จะจับไม่ได้
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)

#: รวมช่องว่างปกติ แท็บ non-breaking space และช่องว่างกว้างเต็มตัวให้เป็นช่องว่างเดียว
_WHITESPACE_RE = re.compile(r"[ \t 　]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def normalize_thai(value: str) -> str:
    """รวมการสะกดภาษาไทยที่ Unicode ยังแยกเป็นคนละตัว"""
    return value.replace(_SARA_AM_DECOMPOSED, _SARA_AM)


def normalize_text(value):
    """ล้างข้อความให้อยู่ในรูปมาตรฐานเดียว

    ลำดับการทำงาน:
      1. NFC            - รวมสระ/วรรณยุกต์ที่แยกส่วนให้เป็นอักขระเดียว
      2. normalize_thai - รวม "ํา" กับ "ำ" ซึ่ง NFC ทำให้ไม่ได้
      3. ลบอักขระล่องหน  - zero-width space และพวกเดียวกัน
      4. ยุบช่องว่าง      - รวมช่องว่างซ้ำ ตัดหัวท้ายแต่ละบรรทัด
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFC", str(value))
    text = normalize_thai(text)
    text = text.translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()
