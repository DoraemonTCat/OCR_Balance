"""ตัวช่วยเรื่องการเชื่อมต่อ ใช้ร่วมกันทุกสคริปต์

เหตุผลที่ต้องมีไฟล์นี้:
เครื่องที่มีโปรแกรมแอนตี้ไวรัสดักตรวจ HTTPS (เช่น Avast) จะสวมใบรับรองของตัวเองเข้ามา
ใบรับรองนั้นอยู่ใน certificate store ของ Windows แต่ไม่อยู่ใน certifi ที่ Python ใช้โดยปริยาย
ผลคือ httpx จะขึ้น CERTIFICATE_VERIFY_FAILED ทั้งที่เว็บปลายทางปกติดี

วิธีแก้ที่ถูกต้องคือให้ Python อ่าน certificate store ของ Windows ผ่าน truststore
*ห้ามแก้ด้วย verify=False* เพราะนั่นคือการปิดการตรวจใบรับรองทิ้งทั้งหมด
แปลว่าถ้ามีใครดักกลางทางจริง เราจะไม่รู้เลย
"""
import ssl

USER_AGENT = "HerbRiskBot/1.0"
TIMEOUT = 20.0
HOST_DELAY = 1.0


def ssl_context():
    """คืน SSLContext ที่เชื่อถือ CA ของระบบปฏิบัติการ

    ถ้าไม่มี truststore ให้ใช้ค่าปริยายของ httpx (certifi) ซึ่งอาจพังบนเครื่องที่มี AV ดักตรวจ
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        return True  # httpx ตีความ True ว่า "ตรวจใบรับรองด้วย certifi"
