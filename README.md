# Surgical Clinic WhatsApp AI Assistant (starter)

This is a starter backend for a bilingual (Hindi/English) clinic receptionist.
It accepts WhatsApp Cloud API webhooks, answers from an OpenAI File Search
knowledge base, collects appointment REQUESTS (not confirmed bookings), and
escalates obvious emergency phrases.

## Important limitations before patient launch

- This is a development starter, not a clinically validated or production-hardened system.
- Have the surgeon/clinic owner approve every knowledge-base document and safety rule.
- The keyword emergency detector is deliberately simple and cannot reliably identify every emergency.
  Add human monitoring and test a broad set of Hindi, English, and Hinglish scenarios.
- The current appointment flow is a basic conversational collector, not a live calendar.
- Email notifications are optional. If not configured, requests are saved to SQLite and logged.
- Add consent, privacy notice, retention/deletion process, access controls, backups, rate limiting,
  monitoring, HTTPS, and security review before handling real patient data.
- Use the official WhatsApp Business Platform and comply with Meta's current messaging/template rules.
- Never place API keys in source code or commit `.env`.

## 1. Install

Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Fill `.env` with your OpenAI and Meta credentials.

## 2. Add clinic knowledge documents

Put approved PDFs, DOCX, or TXT files in `knowledge/`. Do not upload identifiable patient
records, case sheets, scans, or reports to the public-facing clinic FAQ knowledge base.

Create a vector store and upload approved documents:

```bash
python scripts/upload_knowledge.py knowledge/
```

Copy the printed vector store ID into `OPENAI_VECTOR_STORE_ID` in `.env`.

## 3. Run locally

```bash
uvicorn app:app --reload --port 8000
```

Check `http://localhost:8000/health`.

Meta needs a publicly reachable HTTPS webhook. For local testing, use a secure tunnel
such as Cloudflare Tunnel or ngrok, then configure:
- Callback URL: `https://YOUR-PUBLIC-HOST/webhook`
- Verify token: exactly the value of `WHATSAPP_VERIFY_TOKEN`
- Subscribe to the `messages` webhook field.

Set the Meta App Secret in `WHATSAPP_APP_SECRET` to enable signature verification.
Use a long random verify token. Never share access tokens.

## 4. OpenAI knowledge upload

The upload script uses OpenAI vector stores and the Responses API File Search tool.
Only upload clinic-approved educational content and operational FAQs.

## 5. Appointment workflow

Patients can type APPOINTMENT / appointment / अपॉइंटमेंट. The bot collects:
1. Patient name
2. Reason for visit (brief)
3. Preferred date/time
4. Optional note

It saves a pending request in `clinic_bot.db`. It clearly tells the patient the appointment
is NOT confirmed and requires receptionist confirmation.

## 6. Before go-live checklist

- [ ] Confirm clinic name, address, hours, phone, and emergency contact procedure.
- [ ] Add the clinic's privacy notice and consent language.
- [ ] Review every document before uploading.
- [ ] Configure a receptionist notification channel and test delivery.
- [ ] Test English, Hindi, Hinglish, typos, voice-note handling, and emergency scenarios.
- [ ] Add secure staff dashboard/authentication for viewing appointment requests.
- [ ] Set database backups, encryption at rest, access restrictions, retention/deletion policy.
- [ ] Add Meta webhook signature enforcement (required in production).
- [ ] Add rate limiting, error monitoring, retries, and message delivery logging.
- [ ] Verify Meta's current WhatsApp opt-in, 24-hour customer service window, and template rules.
- [ ] Complete a privacy/security and clinical safety review before real-patient use.

## 7. View pending appointment requests

Use SQLite with an appropriately restricted admin environment:

```sql
SELECT id, phone, patient_name, reason, preferred_datetime, status, created_at
FROM appointment_requests
ORDER BY id DESC;
```
