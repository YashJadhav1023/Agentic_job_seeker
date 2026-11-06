"""Find the verification code or link a career site emails after sign-up (read-only Gmail IMAP, inbox then spam)."""
from __future__ import annotations

import email as emaillib
import imaplib
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import core
from email_scanner import _decode_str

CODE_RX = re.compile(r"(?:code|otp|passcode|pin|verification|one[- ]time)\D{0,40}?\b(\d{4,8})\b", re.I)
LINK_RX = re.compile(r"https://[^\s\"'<>]+")
ACTION_RX = re.compile(r"verif|confirm|activat|token", re.I)
SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}
BOXES = ("INBOX", "[Gmail]/Spam")


def _site(host: str) -> str:
    """Registrable domain: careers.acme.com -> acme.com, jobs.acme.co.in -> acme.co.in."""
    parts = (host or "").lower().strip(".").split(".")
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def extract(subject: str, body: str, host: str) -> dict:
    """-> {"code": str|None, "link": str|None}. A link counts only on the site's own domain and when it looks like a
    verification action; a number counts only next to a word like code / OTP / verification."""
    text = f"{subject}\n{body}"
    m = CODE_RX.search(re.sub(r"<[^>]+>", " ", text))
    site, link = _site(host), None
    for url in LINK_RX.findall(text):
        url = url.rstrip(".,;)")
        if _site(urlparse(url).hostname or "") == site and ACTION_RX.search(url):
            link = url
            break
    return {"code": m.group(1) if m else None, "link": link}


def _body(msg) -> str:
    plain, html = "", ""
    for part in msg.walk():
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        if part.get_content_type() == "text/plain":
            plain += payload.decode("utf-8", errors="replace")
        elif part.get_content_type() == "text/html":
            html += payload.decode("utf-8", errors="replace")
    return plain or html


def _sent_at(msg) -> float:
    try:
        return parsedate_to_datetime(msg.get("Date", "")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _scan(m, host: str, since: float) -> dict:
    day = (datetime.fromtimestamp(since) - timedelta(days=1)).strftime("%d-%b-%Y")
    for box in BOXES:
        typ, _ = m.select(box, readonly=True)
        if typ != "OK":
            continue
        typ, data = m.search(None, f'(SINCE "{day}")')
        if typ != "OK" or not data or not data[0]:
            continue
        for num in reversed(data[0].split()[-20:]):
            typ, msg_data = m.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = emaillib.message_from_bytes(msg_data[0][1])
            if _sent_at(msg) < since - 60:  # older than this sign-up (one minute of clock skew allowed)
                continue
            got = extract(_decode_str(msg.get("Subject", "")), _body(msg), host)
            if got["code"] or got["link"]:
                return got
    return {}


def wait_for(profile: dict, host: str, since: float, timeout: float = 180, poll: float = 10, imap_factory=None,
             sleep=time.sleep, now=time.time) -> dict:
    """Poll Gmail until the site's verification mail arrives. -> {"code", "link"} or {} (timeout / Gmail not set up)."""
    pw = core.read_secret("gmail_app_password")
    user = (profile.get("hr_outreach") or {}).get("gmail_user", "")
    if not pw or not user:
        return {}
    factory = imap_factory or (lambda: imaplib.IMAP4_SSL("imap.gmail.com", timeout=25))
    start = now()
    while True:
        try:
            m = factory()
            m.login(user, pw.replace(" ", ""))
            try:
                got = _scan(m, host, since)
            finally:
                m.logout()
        except (imaplib.IMAP4.error, OSError):
            got = {}
        if got:
            return got
        if now() - start >= timeout:
            return {}
        sleep(poll)
