import os, hmac, hashlib, json, sqlite3, logging
from datetime import datetime, timezone
from typing import Optional

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("clinic-bot")

app = FastAPI(title="Surgical Clinic WhatsApp Assistant")
@app.get("/")
async def home():
    return {"status": "Webhook server is running"}
DB_PATH = os.getenv("DB_PATH", "clinic_bot.db")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
VECTOR_STORE_ID = os.getenv("OPENAI_VECTOR_STORE_ID", "")
WA_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
WA_PHONE_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WA_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
WA_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")
CLINIC_NAME = os.getenv("CLINIC_NAME", "Surgical Clinic")
CLINIC_ADDRESS = os.getenv("CLINIC_ADDRESS", "")
CLINIC_HOURS = os.getenv("CLINIC_HOURS", "")
CLINIC_PHONE = os.getenv("CLINIC_PHONE", "")
STAFF_EMAIL = os.getenv("STAFF_EMAIL", "")
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SYSTEM_PROMPT = f"""
You are the WhatsApp receptionist for {CLINIC_NAME}, a surgeon's clinic in India.
Communicate naturally in the language used by the patient: Hindi, English, or simple Hinglish.
Keep replies concise, warm, respectful, and easy to understand on WhatsApp.

SCOPE:
- Clinic reception, general clinic information, appointment REQUESTS, and patient education
  grounded in the clinic's approved knowledge base.
- You may collect an appointment request, but NEVER claim it is booked or confirmed.
- A receptionist must review the request and contact the patient to confirm.
- If the patient asks for information not present in approved clinic documents, say that
  the clinic team will clarify; do not invent fees, timings, protocols, or outcomes.

MEDICAL SAFETY:
- Do not diagnose, prescribe, recommend starting/stopping/changing medicines, or provide
  individualised treatment decisions.
- Do not interpret reports as a substitute for the treating clinician.
- For potentially urgent symptoms (e.g. severe breathing difficulty, chest pain, fainting,
  heavy bleeding, rapidly worsening condition, severe allergic reaction, stroke symptoms,
  severe postoperative pain with fever or confusion), tell the patient not to wait for WhatsApp:
  seek emergency medical care immediately / call local emergency services. In India, 112 is
  the national emergency number. Also flag for human follow-up.
- Do not delay emergency care while collecting details.
- For wound photos, reports, or sensitive details, invite the patient to contact the clinic
  securely or speak to staff; do not make a definitive diagnosis from them.
- Never promise confidentiality beyond the clinic's published privacy policy.

PRIVACY:
- Ask only for information needed to handle the request.
- Do not ask for Aadhaar, card/bank details, passwords, or unnecessary identifiers.
- Avoid repeating sensitive health details in staff notifications.

LANGUAGE:
- Reply in Hindi when the patient writes in Hindi; English when they write in English.
  Use simple Hinglish if the patient does.
- Do not translate medical terms inaccurately. If needed, retain the English medical term
  and explain it simply.

Clinic details (only if configured):
Address: {CLINIC_ADDRESS}
Hours: {CLINIC_HOURS}
Phone: {CLINIC_PHONE}
"""

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY, sender TEXT, body TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY, state TEXT, data TEXT, updated_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS appointment_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT, patient_name TEXT,
            reason TEXT, preferred_datetime TEXT, notes TEXT, status TEXT DEFAULT 'pending',
            created_at TEXT)""")
init_db()

def already_seen(message_id: str) -> bool:
    with db() as c:
        return c.execute("SELECT 1 FROM messages WHERE message_id=?", (message_id,)).fetchone() is not None

def remember_message(message_id: str, sender: str, body: str):
    with db() as c:
        c.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?,?)",
                  (message_id, sender, body, datetime.now(timezone.utc).isoformat()))

def get_session(phone: str):
    with db() as c:
        row = c.execute("SELECT state,data FROM sessions WHERE phone=?", (phone,)).fetchone()
    return (row["state"], json.loads(row["data"])) if row else (None, {})

def set_session(phone: str, state: Optional[str], data: dict):
    with db() as c:
        if state is None:
            c.execute("DELETE FROM sessions WHERE phone=?", (phone,))
        else:
            c.execute("""INSERT INTO sessions(phone,state,data,updated_at) VALUES(?,?,?,?)
                ON CONFLICT(phone) DO UPDATE SET state=excluded.state,data=excluded.data,
                updated_at=excluded.updated_at""",
                (phone, state, json.dumps(data, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

def send_whatsapp(to: str, text: str):
    if not WA_TOKEN or not WA_PHONE_ID:
        log.info("WhatsApp credentials missing; would send to %s: %s", to, text)
        return
    url = f"https://graph.facebook.com/v22.0/{WA_PHONE_ID}/messages"
    response = requests.post(url, headers={"Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"}, json={
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": to, "type": "text", "text": {"preview_url": False, "body": text}
    }, timeout=20)
    response.raise_for_status()

def verify_signature(raw: bytes, signature: str) -> bool:
    if not WA_APP_SECRET:
        return False
    expected = "sha256=" + hmac.new(WA_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")

def notify_staff(subject: str, body: str):
    if not (SMTP_HOST and STAFF_EMAIL and SMTP_FROM):
        log.warning("Staff email not configured. %s | %s", subject, body)
        return
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, SMTP_FROM, STAFF_EMAIL
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
        server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)

def save_appointment(phone: str, data: dict):
    with db() as c:
        cur = c.execute("""INSERT INTO appointment_requests
            (phone,patient_name,reason,preferred_datetime,notes,created_at)
            VALUES(?,?,?,?,?,?)""",
            (phone, data.get("name",""), data.get("reason",""),
             data.get("when",""), data.get("notes",""),
             datetime.now(timezone.utc).isoformat()))
        request_id = cur.lastrowid
    # Keep the notification minimal; avoid unnecessary clinical details.
    notify_staff(f"New appointment request #{request_id}",
        f"Request #{request_id}\nPatient: {data.get('name','')}\nWhatsApp: {phone}\n"
        f"Preferred date/time: {data.get('when','')}\nReason (brief): {data.get('reason','')}\n"
        "Status: Pending receptionist confirmation.")
    return request_id

def appointment_flow(phone: str, text: str):
    state, data = get_session(phone)
    low = text.strip().lower()
    start_words = ["appointment", "book", "booking", "अपॉइंटमेंट", "मुलाकात", "समय लेना"]
    if state is None and any(w in low for w in start_words):
        set_session(phone, "name", {})
        return ("I can take an appointment request. The clinic receptionist will contact you "
                "to confirm; this is not a confirmed booking.\n\n"
                "Please tell me the patient's full name.\n\n"
                "मैं अपॉइंटमेंट का अनुरोध दर्ज कर सकता/सकती हूँ। कृपया मरीज का पूरा नाम बताएं।")
    if state == "name":
        data["name"] = text.strip()
        set_session(phone, "reason", data)
        return "Thank you. What is the main reason for the visit? A short description is enough.\nधन्यवाद। मिलने का मुख्य कारण बताएं।"
    if state == "reason":
        data["reason"] = text.strip()
        set_session(phone, "when", data)
        return "What date and approximate time would you prefer?\nआप किस तारीख और लगभग किस समय आना चाहेंगे?"
    if state == "when":
        data["when"] = text.strip()
        set_session(phone, "notes", data)
        return "Any brief note for the receptionist? You can reply 'No'.\nरिसेप्शनिस्ट के लिए कोई जरूरी जानकारी? नहीं हो तो 'No' लिखें।"
    if state == "notes":
        data["notes"] = "" if low in ("no", "नहीं", "नही") else text.strip()
        rid = save_appointment(phone, data)
        set_session(phone, None, {})
        return (f"Your appointment request has been sent to the clinic (reference #{rid}). "
                "It is NOT confirmed yet. Our receptionist will contact you to confirm the slot.\n\n"
                f"आपका अनुरोध क्लिनिक को भेज दिया गया है (नंबर #{rid})। अपॉइंटमेंट अभी पक्का नहीं है। "
                "रिसेप्शनिस्ट आपसे पुष्टि के लिए संपर्क करेंगे।")
    return None

def ai_reply(phone: str, text: str) -> str:
    if not client:
        return ("Thank you for messaging the clinic. Our team will assist you. "
                "For an appointment request, type APPOINTMENT.\n"
                "क्लिनिक को संदेश देने के लिए धन्यवाद। अपॉइंटमेंट के लिए APPOINTMENT लिखें।")
    tools = []
    if VECTOR_STORE_ID:
        tools = [{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID], "max_num_results": 5}]
    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=SYSTEM_PROMPT,
        input=f"Patient WhatsApp number: {phone}\nPatient message: {text}",
        tools=tools
    )
    return response.output_text.strip()

def is_urgent(text: str) -> bool:
    terms = ["chest pain", "can't breathe", "cannot breathe", "breathlessness", "heavy bleeding",
             "unconscious", "fainted", "stroke", "severe allergic", "high fever after surgery",
             "बहुत खून", "सांस नहीं", "सीने में दर्द", "बेहोश", "तेज बुखार"]
    t = text.lower()
    return any(term in t for term in terms)

@app.get("/health")
def health():
    return {"status": "ok", "service": "surgical-clinic-whatsapp-bot"}

@app.get("/webhook")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token and hmac.compare_digest(token, WA_VERIFY_TOKEN):
        return PlainTextResponse(challenge or "")
    raise HTTPException(status_code=403, detail="Webhook verification failed")

@app.post("/webhook")
async def receive_webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if WA_APP_SECRET and not verify_signature(raw, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    payload = json.loads(raw or b"{}")
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                msg_id = message.get("id", "")
                sender = message.get("from", "")
                if not msg_id or not sender or already_seen(msg_id):
                    continue
                msg_type = message.get("type")
                body = message.get("text", {}).get("body", "") if msg_type == "text" else ""
                remember_message(msg_id, sender, body or f"[{msg_type or 'unknown'}]")
                if not body:
                    send_whatsapp(sender, "Please send your question as text, or contact the clinic team for help.")
                    continue
                if is_urgent(body):
                    answer = ("Your symptoms may need urgent assessment. Please do not wait for a WhatsApp reply. "
                              "Go to the nearest emergency department or call 112 in India now. "
                              "The clinic team has been alerted.\n\n"
                              "आपके लक्षणों में तुरंत जांच की जरूरत हो सकती है। WhatsApp पर इंतज़ार न करें। "
                              "नज़दीकी इमरजेंसी जाएं या भारत में 112 पर कॉल करें।")
                    notify_staff("Urgent patient message - immediate human review",
                                 f"WhatsApp: {sender}\nMessage received. Please review the WhatsApp conversation immediately.")
                else:
                    answer = appointment_flow(sender, body)
                    if answer is None:
                        answer = ai_reply(sender, body)
                try:
                    send_whatsapp(sender, answer[:4000])
                except Exception:
                    log.exception("Failed to send WhatsApp reply")
    return {"status": "received"}
