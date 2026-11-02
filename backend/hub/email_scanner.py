"""Recruiter inbound email scanner and AI-assisted reply drafting.

Connects to Gmail via IMAP (using profile hr_outreach.gmail_user and .secrets/gmail_app_password).
Identifies recruiter and HR responses to applications and outreach emails.
Classifies messages:
  - interview_invite: request for interview, call or schedule availability
  - ctc_inquiry: asking for Current CTC, Expected CTC, Notice Period, Location
  - status_update: application shortlisted or under review
  - rejection: polite rejection notice
  - generic: general reply or autoresponder

Generates smart AI reply drafts pre-filled with candidate facts from profile.yaml
(current/expected CTC, notice period, location, phone) so the user does not need
to re-type standard facts, and sends a Telegram notification for human review.
"""
from __future__ import annotations

import email as emaillib
from email.header import decode_header
import imaplib
import json
import os
import re
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import core
import keys
import llm

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS email_replies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,
    thread_id TEXT,
    from_name TEXT,
    from_email TEXT,
    to_email TEXT,
    subject TEXT,
    body TEXT,
    classification TEXT,
    confidence REAL DEFAULT 0.8,
    extracted_questions TEXT,
    draft_reply TEXT,
    status TEXT DEFAULT 'pending_review',
    created_at TEXT,
    reviewed_at TEXT,
    sent_at TEXT
);
"""

INTERVIEW_RX = re.compile(
    r"\b(interview|round|technical discussion|hiring manager|zoom|teams|google meet|meet\.google|calendar invite|"
    r"schedule a (call|discussion|chat|meeting)|shortlisted|availability for|connect with you)\b",
    re.I,
)
CTC_RX = re.compile(
    r"\b(current ctc|expected ctc|ctc expectation|salary expectation|compensation|notice period|"
    r"earliest start date|availability to join|serving notice|lpa|fixed ctc|variable ctc)\b",
    re.I,
)
REJECT_RX = re.compile(
    r"\b(regret to inform|pursuing other candidates|not moving forward|decided to move forward with other|"
    r"unfortunately|will keep your resume on file|future opportunities|not selected)\b",
    re.I,
)
BOUNCE_RX = re.compile(
    r"\b(mailer-daemon|postmaster|delivery status notification|undeliverable|returned mail|failure notice)\b",
    re.I,
)


def _init_db():
    with core.db() as c:
        c.execute(_TABLE_SQL)


def _decode_str(header_val: str) -> str:
    if not header_val:
        return ""
    decoded_fragments = decode_header(header_val)
    parts = []
    for text, enc in decoded_fragments:
        if isinstance(text, bytes):
            parts.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            parts.append(str(text))
    return " ".join(parts)


def extract_bounced_address(body: str, known_emails: set[str] | None = None) -> str:
    """Extract failed recipient address from bounce notification body."""
    body_lower = body.lower()
    if known_emails:
        for ke in known_emails:
            if ke and ke in body_lower:
                return ke
    m = re.search(r"(?:Final-Recipient|Original-Recipient):\s*(?:rfc822;)?\s*([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", body, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(
        r"(?:failed|undeliverable|delivery to the following recipient failed|couldn't be delivered to|mailbox unavailable)[^<>\n]{0,80}?<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)?>?",
        body,
        re.I,
    )
    if m and m.group(1):
        return m.group(1).lower()
    for cand in re.findall(r"[\w.-]+@[\w.-]+\.\w+", body):
        cand_l = cand.lower()
        if not any(cand_l.startswith(p) for p in ("mailer-daemon", "postmaster", "noreply", "no-reply")):
            return cand_l
    return ""


def classify_content(subject: str, body: str) -> str:
    combined = f"{subject}\n{body[:2000]}"
    if BOUNCE_RX.search(combined):
        return "bounce"
    if REJECT_RX.search(combined):
        return "rejection"
    if INTERVIEW_RX.search(combined):
        return "interview_invite"
    if CTC_RX.search(combined):
        return "ctc_inquiry"
    return "status_update"


def build_ai_reply_draft(profile: dict, from_name: str, subject: str, body: str, classification: str) -> str:
    """Draft a professional, contextual reply using candidate profile facts and AI."""
    a = profile.get("applicant") or {}
    comp = profile.get("compensation") or {}
    loc = a.get("location") or {}
    
    candidate_name = a.get("name") or "Candidate"
    cur_ctc = comp.get("current_ctc_inr") or 0
    exp_ctc = comp.get("expected_ctc_inr") or 0
    cur_ctc_str = f"{cur_ctc/100000:.1f} LPA" if cur_ctc >= 100000 else f"INR {cur_ctc}"
    exp_ctc_str = f"{exp_ctc/100000:.1f} LPA" if exp_ctc >= 100000 else f"INR {exp_ctc}"
    notice_days = comp.get("notice_period_days") or 30
    phone = a.get("phone") or ""
    city = loc.get("city") or a.get("current_location") or "Pune"

    # Quick deterministic fallback draft if LLM is offline or not configured
    greeting = f"Hi {from_name.split()[0]}," if from_name and len(from_name.split()) > 0 else "Hi,"
    if classification == "ctc_inquiry":
        deterministic_draft = (
            f"{greeting}\n\n"
            f"Thank you for getting in touch regarding the opportunity.\n\n"
            f"Here are my requested details:\n"
            f"- Current CTC: {cur_ctc_str}\n"
            f"- Expected CTC: {exp_ctc_str}\n"
            f"- Notice Period: {notice_days} days\n"
            f"- Current Location: {city}\n"
            f"- Contact Number: {phone}\n\n"
            f"Please let me know if you need any additional details. Looking forward to hearing from you.\n\n"
            f"Best regards,\n"
            f"{candidate_name}"
        )
    elif classification == "interview_invite":
        deterministic_draft = (
            f"{greeting}\n\n"
            f"Thank you for considering my profile for this role. I would be delighted to connect for an interview.\n\n"
            f"I am available on weekdays between 2:00 PM and 7:00 PM IST, or you can send over a convenient calendar invite.\n"
            f"You can also reach me directly at {phone}.\n\n"
            f"Looking forward to speaking with the team.\n\n"
            f"Best regards,\n"
            f"{candidate_name}"
        )
    else:
        deterministic_draft = (
            f"{greeting}\n\n"
            f"Thank you for the update. Please let me know if you need any further information from my side.\n\n"
            f"Best regards,\n"
            f"{candidate_name}"
        )

    # Enhance with LLM if available
    try:
        c = llm.cfg(profile)
        ok, _ = llm.available(c)
        if not ok:
            return deterministic_draft
        prompt = (
            "Write a concise, professional, warm email reply from a job candidate to a recruiter.\n"
            f"Candidate Name: {candidate_name}\n"
            f"Candidate Current CTC: {cur_ctc_str}\n"
            f"Candidate Expected CTC: {exp_ctc_str}\n"
            f"Candidate Notice Period: {notice_days} days\n"
            f"Candidate Location: {city}\n"
            f"Candidate Phone: {phone}\n\n"
            f"Recruiter Email Subject: {subject}\n"
            f"Recruiter Email Content:\n<<<{body[:1200]}>>>\n\n"
            "Rules:\n"
            "1. Answer all questions asked by the recruiter using the facts above.\n"
            "2. Never invent numbers, dates, or employers not given.\n"
            "3. Keep it professional, polite, and under 150 words.\n"
            "4. Output only the email body without subject line."
        )
        ai_draft = llm.chat(prompt, c)
        if ai_draft and len(ai_draft.strip()) > 20:
            return ai_draft.strip()
    except Exception:
        pass

    return deterministic_draft


def scan_inbox(profile: dict | None = None, max_messages: int = 50) -> dict:
    """Connect to Gmail IMAP, scan recent emails, detect recruiter replies, and draft responses."""
    _init_db()
    p = profile if profile is not None else core.load_profile()
    pw = core.read_secret("gmail_app_password")
    user = p.get("hr_outreach", {}).get("gmail_user", "")
    
    if not pw or not user:
        return {"scanned": 0, "replies_found": 0, "status": "no_credentials"}

    results = {
        "scanned": 0,
        "replies_found": 0,
        "interviews": 0,
        "ctc_inquiries": 0,
        "rejections": 0,
        "items": [],
    }

    try:
        m = imaplib.IMAP4_SSL("imap.gmail.com", timeout=25)
        m.login(user, pw.replace(" ", ""))
        m.select("INBOX", readonly=True)

        since_date = (datetime.now() - timedelta(days=14)).strftime("%d-%b-%Y")
        typ, data = m.search(None, f'(SINCE "{since_date}")')
        if typ != "OK" or not data or not data[0]:
            m.logout()
            return results

        msg_nums = data[0].split()
        to_process = msg_nums[-max_messages:]

        with core.db() as db:
            sent_emails = {r[0].lower() for r in db.execute("SELECT to_email FROM emails_sent WHERE result='sent'")}

        for num in reversed(to_process):
            results["scanned"] += 1
            typ, msg_data = m.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue

            msg = emaillib.message_from_bytes(msg_data[0][1])
            message_id = msg.get("Message-ID") or f"imap-{num.decode()}-{int(time.time())}"
            from_raw = _decode_str(msg.get("From", ""))
            subject = _decode_str(msg.get("Subject", ""))

            # Extract clean email and name
            email_match = re.search(r"[\w.-]+@[\w.-]+\.\w+", from_raw)
            from_email = email_match.group(0).lower() if email_match else ""
            from_name = re.sub(r"<[^>]+>", "", from_raw).strip().strip('"')

            # Ignore emails sent by ourselves
            if from_email == user.lower():
                continue

            # Extract body
            body = ""
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        body += payload.decode("utf-8", errors="replace")
                elif ctype == "text/html" and not body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_text = payload.decode("utf-8", errors="replace")
                        body = re.sub(r"<[^>]+>", " ", html_text)

            classification = classify_content(subject, body)
            if classification == "bounce":
                bounced = extract_bounced_address(body, sent_emails)
                if bounced:
                    import contacts
                    contacts.suppress(bounced, "bounced")
                    with core.db() as db:
                        db.execute("UPDATE hr_contacts SET status='bounced' WHERE LOWER(email)=LOWER(?)", (bounced,))
                    results["bounces"] = results.get("bounces", 0) + 1
                    try:
                        import notify
                        notify.push(p, "Email Bounced", f"Delivery failed for {bounced}. Contact auto-suppressed.")
                    except Exception:
                        pass
                continue

            # Is this from a recruiter we contacted, or mentions recruiting keywords?
            is_contacted = from_email in sent_emails
            is_job_related = classification in ("interview_invite", "ctc_inquiry", "rejection")

            if not (is_contacted or is_job_related):
                continue

            # Check if already processed
            with core.db() as db:
                existing = db.execute("SELECT id FROM email_replies WHERE message_id=?", (message_id,)).fetchone()
                if existing:
                    continue

            draft = build_ai_reply_draft(p, from_name, subject, body, classification)
            now_iso = core.now_iso()

            with core.db() as db:
                db.execute(
                    """INSERT INTO email_replies
                       (message_id, from_name, from_email, to_email, subject, body, classification, draft_reply, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (message_id, from_name, from_email, user, subject, body[:4000], classification, draft, now_iso),
                )
                # Flip contact status to replied
                if from_email:
                    db.execute("UPDATE hr_contacts SET status='replied' WHERE LOWER(email)=LOWER(?)", (from_email,))
                    db.execute("UPDATE jobs SET status='replied' WHERE LOWER(contact)=LOWER(?)", (from_email,))

            results["replies_found"] += 1
            if classification == "interview_invite":
                results["interviews"] += 1
            elif classification == "ctc_inquiry":
                results["ctc_inquiries"] += 1
            elif classification == "rejection":
                results["rejections"] += 1

            results["items"].append({
                "from": from_raw,
                "subject": subject,
                "classification": classification,
                "draft": draft[:150] + "...",
            })

            # Send dual alerts (ntfy + Telegram) for recruiter replies
            try:
                import notify
                label = "🎉 Interview Invite" if classification == "interview_invite" else (
                    "📋 Recruiter Inquiry (CTC/Details)" if classification == "ctc_inquiry" else "📬 Recruiter Reply"
                )
                notify.push(
                    p,
                    label,
                    f"From: {from_name} ({from_email})\n"
                    f"Subject: {subject}\n\n"
                    f"AI Drafted Reply:\n{draft}\n\n"
                    f"(Review and send via Job Seeker app)",
                )
            except Exception:
                pass

        m.logout()
    except Exception as e:
        results["error"] = f"{type(e).__name__}: {str(e)[:120]}"

    return results


def list_replies(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    _init_db()
    with core.db() as db:
        if status:
            rows = db.execute(
                "SELECT * FROM email_replies WHERE status=? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT * FROM email_replies ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def update_draft(reply_id: int, new_draft: str) -> bool:
    _init_db()
    with core.db() as db:
        res = db.execute(
            "UPDATE email_replies SET draft_reply=?, reviewed_at=? WHERE id=?",
            (new_draft.strip(), core.now_iso(), reply_id),
        )
        return res.rowcount > 0


def send_approved_reply(reply_id: int, profile: dict | None = None) -> dict:
    """Send the approved AI draft reply via Gmail SMTP."""
    _init_db()
    with core.db() as db:
        r = db.execute("SELECT * FROM email_replies WHERE id=?", (reply_id,)).fetchone()
    if not r:
        return {"ok": False, "error": f"Reply #{reply_id} not found"}
    
    item = dict(r)
    draft = item.get("draft_reply", "").strip()
    if not draft:
        return {"ok": False, "error": "Draft is empty"}

    p = profile if profile is not None else core.load_profile()
    subj = item["subject"]
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"

    try:
        core.send_via_gmail(p, to=item["from_email"], subject=subj, body=draft, attach_resume=True)
        with core.db() as db:
            db.execute("UPDATE email_replies SET status='sent', sent_at=? WHERE id=?", (core.now_iso(), reply_id))
        return {"ok": True, "sent_to": item["from_email"], "subject": subj}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)}"}
