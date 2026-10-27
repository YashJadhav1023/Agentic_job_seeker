"""Send outreach emails or save them as drafts through the user's own mailbox (SMTP + IMAP)."""
import html
import imaplib
import mimetypes
import random
import re
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, formatdate
from pathlib import Path

PRESETS = {
    "gmail": {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "imap_host": "imap.gmail.com"},
    "outlook": {"smtp_host": "smtp.office365.com", "smtp_port": 587, "imap_host": "outlook.office365.com"},
    "zoho": {"smtp_host": "smtp.zoho.com", "smtp_port": 587, "imap_host": "imap.zoho.com"},
}


def first_name(full):
    parts = (full or "").split()
    return parts[0] if parts else ""


def render(template, r, settings):
    """Fill {first_name} {name} {company} {role} {domain} {my_name}; unknown keys are left as-is."""
    fallback = settings.get("greeting_fallback") or "Hiring Team"
    values = {
        "first_name": first_name(r.get("name")) or fallback,
        "name": r.get("name") or fallback,
        "company": r.get("company") or "your company",
        "role": r.get("role") or "",
        "domain": r.get("domain") or "",
        "my_name": settings.get("from_name") or "",
    }
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), template)


def build_message(settings, to, subject, body, attachment=None):
    msg = EmailMessage()
    msg["From"] = formataddr((settings.get("from_name") or "", settings["user"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=settings["user"].split("@")[-1])
    if settings.get("signature"):
        body = body.rstrip() + "\n\n" + settings["signature"]
    msg.set_content(body)
    linked = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', html.escape(body))
    msg.add_alternative(f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.55">'
                        f'{linked.replace(chr(10), "<br>")}</div>', subtype="html")
    if attachment and Path(attachment).exists():
        ctype = mimetypes.guess_type(attachment)[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        msg.add_attachment(Path(attachment).read_bytes(), maintype=main, subtype=sub,
                           filename=Path(attachment).name)
    return msg


class Mailbox:
    def __init__(self, settings):
        self.s = settings
        self.smtp = None
        self.imap = None
        self.drafts = None

    def open_smtp(self):
        if not self.smtp:
            port = int(self.s.get("smtp_port") or 587)
            if port == 465:
                self.smtp = smtplib.SMTP_SSL(self.s["smtp_host"], port, timeout=30)
            else:
                self.smtp = smtplib.SMTP(self.s["smtp_host"], port, timeout=30)
                self.smtp.starttls()
            self.smtp.login(self.s["user"], self.s["password"])
        return self.smtp

    def open_imap(self):
        if not self.imap:
            self.imap = imaplib.IMAP4_SSL(self.s["imap_host"], 993)
            self.imap.login(self.s["user"], self.s["password"])
            self.drafts = self._drafts_folder()
        return self.imap

    def _drafts_folder(self):
        _, folders = self.imap.list()
        for raw in folders or []:
            line = raw.decode(errors="ignore")
            if "\\Drafts" in line:
                return line.rsplit(' "/" ', 1)[-1].strip() if ' "/" ' in line else line.split()[-1]
        return "[Gmail]/Drafts" if "gmail" in self.s.get("imap_host", "") else "Drafts"

    def send(self, msg):
        self.open_smtp().send_message(msg)

    def draft(self, msg):
        imap = self.open_imap()
        typ, data = imap.append(self.drafts, "\\Draft", imaplib.Time2Internaldate(time.time()),
                                msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"could not save draft: {data}")

    def close(self):
        for fn in (lambda: self.smtp and self.smtp.quit(), lambda: self.imap and self.imap.logout()):
            try:
                fn()
            except Exception:
                pass


def test_connection(settings, need_imap=True):
    box = Mailbox(settings)
    try:
        box.open_smtp()
        info = "SMTP ok"
        if need_imap and settings.get("imap_host"):
            box.open_imap()
            info += f", drafts folder: {box.drafts}"
        return True, info
    except smtplib.SMTPAuthenticationError:
        return False, "Login rejected. For Gmail use an App Password, not your normal password."
    except Exception as e:
        return False, f"{e.__class__.__name__}: {e}"
    finally:
        box.close()


def run_outreach(settings, job, log, record, sent_today, should_stop):
    """job: recipients, subject, body, mode ('draft'|'send'), attachment, delay, daily_cap."""
    box = Mailbox(settings)
    mode = job["mode"]
    delay = max(20, int(job.get("delay") or 90))
    cap = int(job.get("daily_cap") or 40)
    done = 0
    try:
        for i, r in enumerate(job["recipients"]):
            if should_stop():
                log("stopped by you")
                break
            if mode == "send" and sent_today() >= cap:
                log(f"daily cap of {cap} sends reached — the rest were not sent. Try again tomorrow.")
                break
            subject = render(job["subject"], r, settings)
            body = render(job["body"], r, settings)
            msg = build_message(settings, r["email"], subject, body, job.get("attachment"))
            try:
                (box.send if mode == "send" else box.draft)(msg)
                record(r, subject, "sent" if mode == "send" else "drafted", "")
                done += 1
                log(f"{'sent to' if mode == 'send' else 'draft saved for'} {r['email']}")
            except Exception as e:
                record(r, subject, "failed", str(e))
                log(f"error for {r['email']}: {e}")
                box.close()
                box = Mailbox(settings)  # reconnect on next message
            if mode == "send" and i < len(job["recipients"]) - 1:
                wait = delay * random.uniform(0.7, 1.3)
                log(f"waiting {int(wait)}s before next send…")
                end = time.time() + wait
                while time.time() < end and not should_stop():
                    time.sleep(1)
    finally:
        box.close()
    log(f"done — {done} {'sent' if mode == 'send' else 'drafts saved'}")
