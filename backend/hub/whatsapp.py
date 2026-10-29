"""WhatsApp through Evolution Go (upstream, runs as a container next to the app).

What it does
  * link / status / unlink the WhatsApp number (QR code scanned from the phone: Linked devices);
  * import phone numbers from a PDF, Word, Excel, CSV or text file (optionally with a name next to each number);
  * send each number a personal message with your resume attached, with the safety rules below;
  * watch replies: "stop" / "not interested" opts the number out for good.

Safety rules for sending (WhatsApp bans numbers that send unwanted messages in bulk):
  * preview (dry run) is the default; live sending needs the profile's live-actions switch AND your explicit
    confirmation that the people expect your message (settings.recipients_expect_my_message);
  * daily cap (default 20, never more than 50), random 40-110 s pause between messages, send window honoured;
  * a number is contacted at most once per 30 days, numbers that are not on WhatsApp are skipped, opt-outs are final;
  * every message ends with a one-line way to say stop.
Evolution Go is an unofficial client; using it can get the linked number restricted by WhatsApp. Use a separate number.
"""
from __future__ import annotations

import base64
import io
import json
import os
import random
import re
import secrets
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests

import core

TIMEOUT = 30
CONNECT_EVERY = 60  # seconds between /instance/connect calls while waiting for a QR scan
HARD_DAILY_CAP = 50
REPEAT_DAYS = 30
MAX_FILE_BYTES = 8 * 1024 * 1024
STOP_RX = re.compile(r"^\W*(stop|unsubscribe|remove me|do not contact|don'?t contact|not interested|opt[ -]?out)\b", re.I)
DEFAULT_TEMPLATE = ("Hi {name}, I'm {candidate}, a {role}. I'm looking for opportunities with {company} and my resume is "
                    "attached. I'd be glad to share anything else you need.")
FOOTER = "\n\nReply STOP if you'd rather not hear from me."
DEFAULTS = {"template": DEFAULT_TEMPLATE, "daily_cap": 20, "delay_sec": [40, 110], "recipients_expect_my_message": False,
            "default_country_code": "91", "watch_groups": [], "mode": "ask"}


class EvoError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- settings
def settings() -> dict:
    s = dict(DEFAULTS)
    s.update(core.kv_get("whatsapp:settings", {}) or {})
    s["daily_cap"] = max(1, min(int(s["daily_cap"]), HARD_DAILY_CAP))
    return s


def save_settings(patch: dict) -> dict:
    cur = settings()
    for k in DEFAULTS:
        if k in patch and patch[k] is not None:
            cur[k] = patch[k]
    if str(cur["mode"]) not in ("ask", "auto"):
        raise ValueError("mode must be ask or auto")
    if not (isinstance(cur["delay_sec"], list) and len(cur["delay_sec"]) == 2 and 5 <= int(cur["delay_sec"][0]) <= int(cur["delay_sec"][1])):
        raise ValueError("delay_sec must be [min, max] seconds, at least 5")
    cur["daily_cap"] = max(1, min(int(cur["daily_cap"]), HARD_DAILY_CAP))
    cur["recipients_expect_my_message"] = bool(cur["recipients_expect_my_message"])
    cur["default_country_code"] = re.sub(r"\D", "", str(cur["default_country_code"])) or "91"
    if "{" not in str(cur["template"]) and len(str(cur["template"])) < 5:
        raise ValueError("template is too short")
    core.kv_set("whatsapp:settings", cur)
    return cur


# ---------------------------------------------------------------- gateway client
def base_url() -> str:
    return os.environ.get("JOBHUB_EVOLUTION_URL", "").rstrip("/")


def admin_key() -> str:
    k = os.environ.get("JOBHUB_EVOLUTION_KEY", "").strip()
    return "" if k in ("", "UNSET") else k


def configured() -> bool:
    return bool(base_url() and admin_key())


def _secret_file(name: str) -> Path:
    return core.SECRETS / name


def _read_or_make_secret(name: str) -> str:
    f = _secret_file(name)
    if f.exists():
        return f.read_text().strip()
    v = secrets.token_hex(24)
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(v)
    os.chmod(f, 0o600)
    return v


def webhook_secret() -> str:
    return _read_or_make_secret("whatsapp_webhook_secret")


def instance_token() -> str:
    return _read_or_make_secret("whatsapp_instance_token")


class Evo:
    """Minimal Evolution Go client. `http` is injectable for tests: http(method, url, **kw) -> response-like object."""

    def __init__(self, base: str | None = None, admin: str | None = None, token: str | None = None, http=None):
        self.base = (base or base_url()).rstrip("/")
        self.admin = admin or admin_key()
        self.token = token or instance_token()
        self.http = http or requests.request

    def _call(self, method: str, path: str, *, admin: bool = False, **kw):
        if not self.base:
            raise EvoError("WhatsApp gateway is not configured (JOBHUB_EVOLUTION_URL)")
        headers = {"apikey": self.admin if admin else self.token}
        try:
            r = self.http(method, self.base + path, headers=headers, timeout=TIMEOUT, **kw)
        except requests.RequestException as e:
            raise EvoError(f"cannot reach the WhatsApp gateway: {type(e).__name__}") from e
        try:
            body = r.json()
        except ValueError:
            body = {"raw": (r.text or "")[:200]}
        if r.status_code >= 400:
            msg = body.get("error") if isinstance(body, dict) else None
            raise EvoError(str(msg or body)[:200], r.status_code)
        return body.get("data", body) if isinstance(body, dict) else body

    def ensure_instance(self) -> None:
        """Create the instance once (admin key) and point its webhook at this app."""
        if core.kv_get("whatsapp:instance"):
            return
        iid = str(uuid.uuid4())   # the gateway keys instances by UUID (a plain word is rejected by its database)
        try:
            self._call("POST", "/instance/create", admin=True, json={"instanceId": iid, "name": "jobseeker", "token": self.token})
        except EvoError as e:
            if "already" not in str(e).lower() and e.status not in (409,):
                raise
        core.kv_set("whatsapp:instance", {"id": iid, "name": "jobseeker", "created": core.now_iso()})

    def connect(self, app_url: str = "http://jobhub:8000") -> None:
        self._call("POST", "/instance/connect", json={
            "webhookUrl": f"{app_url}/api/v1/whatsapp/webhook?s={webhook_secret()}", "subscribe": ["MESSAGE"], "immediate": True})

    def status(self) -> dict:
        d = self._call("GET", "/instance/status") or {}
        return {"connected": bool(_g(d, "Connected", "connected")), "logged_in": bool(_g(d, "LoggedIn", "loggedIn", "logged_in")),
                "name": _g(d, "Name", "name") or ""}

    def qr(self) -> dict:
        d = self._call("GET", "/instance/qr") or {}
        raw = _g(d, "qrcode", "Qrcode") or ""
        img = raw.split("|")[0] if raw else ""
        return {"image": img if img.startswith("data:image") else "", "code": _g(d, "code", "Code") or "",
                "passkey": _g(d, "passkeyStage", "PasskeyStage") or ""}

    def logout(self) -> None:
        self._call("DELETE", "/instance/logout")

    def check(self, numbers: list) -> dict:
        d = self._call("POST", "/user/check", json={"number": numbers, "formatJid": True}) or {}
        users = d.get("Users") if isinstance(d, dict) else d
        out = {}
        for u in users or []:
            out[re.sub(r"\D", "", str(_g(u, "Query", "query") or ""))] = bool(_g(u, "IsInWhatsapp", "isInWhatsapp"))
        return out

    def send_document(self, number: str, path: str, filename: str, caption: str) -> dict:
        with open(path, "rb") as fh:
            files = {"file": (filename, fh, "application/pdf")}
            data = {"number": number, "type": "document", "filename": filename, "caption": caption}
            return self._call("POST", "/send/media", files=files, data=data) or {}

    def send_text(self, number: str, text: str) -> dict:
        return self._call("POST", "/send/text", json={"number": number, "text": text, "formatJid": True}) or {}

    def groups(self) -> list:
        d = self._call("GET", "/group/list") or []
        items = d if isinstance(d, list) else (d.get("groups") or [])
        out = []
        for g in items:
            gn = _g(g, "GroupName", "groupName")
            name = _g(g, "Name", "name") or (gn.get("Name") if isinstance(gn, dict) else gn) or ""
            out.append({"jid": _g(g, "JID", "jid", "id") or "", "name": str(name)})
        return [g for g in out if g["jid"]]


def _g(d, *names):
    """Case-tolerant dict lookup (the gateway returns Go-style capitalised keys in places)."""
    if not isinstance(d, dict):
        return None
    for n in names:
        if n in d:
            return d[n]
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


# ---------------------------------------------------------------- link / status
def status(evo: Evo | None = None) -> dict:
    s = settings()
    out = {"configured": configured(), "linked": False, "name": "", "settings": {k: s[k] for k in s if k != "template"} | {"template": s["template"]},
           "contacts": counts(), "sent_today": sent_today(), "daily_cap": s["daily_cap"]}
    if not out["configured"]:
        out["hint"] = "The WhatsApp gateway is not running. It needs COMPOSE_PROFILES=whatsapp and the generated admin key (deploy/bootstrap-env.sh)."
        return out
    try:
        st = (evo or Evo()).status()
        out.update(linked=st["logged_in"], connected=st["connected"], name=st["name"])
    except EvoError as e:
        out["gateway_error"] = str(e)
    return out


def link(evo: Evo | None = None) -> dict:
    """Idempotent: creates the instance, starts the connection and returns the current QR image until linked."""
    if not configured():
        return {"error": "The WhatsApp gateway is not configured. It needs COMPOSE_PROFILES=whatsapp and JOBHUB_EVOLUTION_KEY."}
    evo = evo or Evo()
    try:
        evo.ensure_instance()
        st = evo.status()
        if st["logged_in"]:
            return {"state": "linked", "name": st["name"]}
        # The app polls this every few seconds. Each /instance/connect makes the gateway start a fresh client, which
        # throws away the QR the user is scanning and leaks database connections (it ran Postgres out of clients).
        # So connect only when the gateway has no live connection, and at most once per CONNECT_EVERY seconds.
        last = float(core.kv_get("whatsapp:last_connect", 0) or 0)
        if not st["connected"] and time.time() - last >= CONNECT_EVERY:
            core.kv_set("whatsapp:last_connect", time.time())
            try:
                evo.connect()
            except EvoError:
                pass  # already connecting
        try:
            q = evo.qr()
        except EvoError as e:
            if e.status in (400, 404):  # no QR generated yet
                return {"state": "waiting", "qr": "", "note": "Starting the connection, ask again in a few seconds."}
            raise
        if q["passkey"]:
            return {"state": "passkey", "note": "WhatsApp asks for a passkey for this account. Use another account or open the Manager page."}
        return {"state": "scan" if q["image"] else "waiting", "qr": q["image"], "note":
                "Open WhatsApp on the phone: Settings > Linked devices > Link a device, then scan the code." if q["image"]
                else "Starting the connection, ask again in a few seconds."}
    except EvoError as e:
        return {"error": str(e), "hint": "If this says 503, finish the one-time license registration in the Evolution Go Manager page."
                if e.status == 503 else ""}


def unlink(evo: Evo | None = None) -> dict:
    try:
        (evo or Evo()).logout()
    except EvoError as e:
        return {"error": str(e)}
    return {"unlinked": True}


# ---------------------------------------------------------------- numbers
def normalize(raw: str, cc: str = "91") -> str | None:
    """-> digits in international form (no +) or None when it does not look like a phone number."""
    s = str(raw).strip()
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if s.startswith("+"):
        n = digits
    elif digits.startswith("00"):
        n = digits[2:]
    elif len(digits) == 10:
        n = cc + digits
    elif len(digits) == 11 and digits.startswith("0"):
        n = cc + digits[1:]
    else:
        n = digits
    if not 9 <= len(n) <= 15:
        return None
    if n.startswith("91") and len(n) == 12 and n[2] not in "6789":
        return None  # Indian mobile numbers start with 6-9
    if len(set(n)) <= 2:
        return None
    return n


PHONE_RX = re.compile(r"(?<![\w.])(\+?\d[\d\s().\-]{7,16}\d)(?![\w])")


def extract_numbers(text: str, cc: str = "91") -> list:
    """[{number, name}] from free text, a CSV or a table dump. A name is the text on the same line, when there is one."""
    found, seen = [], set()
    for line in text.splitlines():
        for m in PHONE_RX.finditer(line):
            n = normalize(m.group(1), cc)
            if not n or n in seen:
                continue
            seen.add(n)
            rest = (line[:m.start()] + " " + line[m.end():])
            rest = re.sub(r"[|,;:\t]+", " ", rest)
            rest = re.sub(r"\S+@\S+|\d+", " ", rest)
            name = re.sub(r"\s+", " ", rest).strip(" -.()")
            found.append({"number": n, "name": name[:60] if re.search(r"[A-Za-z]", name) else ""})
    return found


def _docx_text(data: bytes) -> str:
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > 30 * 1024 * 1024:
            raise ValueError("document is too large")
        xml = z.read("word/document.xml")
    if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
        raise ValueError("unsafe document")
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    W = "{%s}" % ns["w"]
    root = ET.fromstring(xml)
    body = root.find("w:body", ns)
    lines = []

    def para(p):
        return "".join(t.text or "" for t in p.iter(W + "t"))

    for el in list(body):
        if el.tag == W + "p":
            lines.append(para(el))
        elif el.tag == W + "tbl":
            for tr in el.iter(W + "tr"):
                lines.append(" | ".join(" ".join(para(p) for p in tc.iter(W + "p")) for tc in tr.findall("w:tc", ns)))
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    import fitz
    doc = fitz.open(stream=data, filetype="pdf")
    return "\n".join(page.get_text() for page in list(doc)[:100])


def _xlsx_text(data: bytes) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets[:5]:
        for row in ws.iter_rows(values_only=True):
            lines.append(" | ".join("" if c is None else (str(int(c)) if isinstance(c, float) and c == int(c) else str(c)) for c in row))
    return "\n".join(lines)


def file_to_text(data: bytes, filename: str) -> str:
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("file is larger than 8 MB")
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return _pdf_text(data)
    if ext == ".docx":
        return _docx_text(data)
    if ext in (".xlsx", ".xlsm"):
        return _xlsx_text(data)
    if ext in (".csv", ".txt", ".tsv", ".vcf", ""):
        for enc in ("utf-8-sig", "utf-16", "latin-1"):
            try:
                return data.decode(enc)
            except UnicodeError:
                continue
    raise ValueError("use a PDF, Word (.docx), Excel (.xlsx), CSV or text file (old .doc is not supported: save it as .docx)")


def import_numbers(text: str, source: str = "text", default_cc: str | None = None, company: str = "") -> dict:
    cc = default_cc or settings()["default_country_code"]
    items = extract_numbers(text, cc)
    new = dup = blocked = 0
    with core.db() as c:
        for it in items:
            row = c.execute("SELECT status FROM wa_contacts WHERE number=?", (it["number"],)).fetchone()
            if row:
                dup += 1
                continue
            if _suppressed(it["number"]):
                blocked += 1
                continue
            c.execute("INSERT INTO wa_contacts(number,name,company,source,status,added_at) VALUES(?,?,?,?,?,?)",
                      (it["number"], it["name"], company, source[:80], "new", core.now_iso()))
            new += 1
    return {"found": len(items), "new": new, "already_known": dup, "blocked": blocked, "sample": items[:5],
            "note": "Nothing is sent by importing. Use whatsapp_send to preview and send." if items else "No phone numbers found in that file."}


def _suppressed(number: str) -> bool:
    with core.db() as c:
        return c.execute("SELECT 1 FROM suppression WHERE key=?", ("wa:" + number,)).fetchone() is not None


def counts() -> dict:
    with core.db() as c:
        return {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM wa_contacts GROUP BY status")}


def list_contacts(status_filter: str = "", limit: int = 100) -> list:
    q, a = "SELECT number,name,company,status,note,added_at,sent_at,last_in_at FROM wa_contacts", []
    if status_filter:
        q += " WHERE status=?"
        a.append(status_filter)
    q += " ORDER BY added_at DESC LIMIT ?"
    a.append(max(1, min(int(limit), 500)))
    with core.db() as c:
        return [dict(r) for r in c.execute(q, a)]


# ---------------------------------------------------------------- sending
def sent_today() -> int:
    with core.db() as c:
        return c.execute("SELECT COUNT(*) FROM wa_log WHERE result='sent' AND at>=?", (core.start_of_today_iso(),)).fetchone()[0]


def render(template: str, contact: dict, profile: dict) -> str:
    a = profile["applicant"]
    role = (profile["search"].get("keywords") or ["engineer"])[0]
    first = (contact.get("name") or "").split(" ")[0] if contact.get("name") else ""
    vals = {"name": first or "there", "candidate": a.get("name", ""), "role": role, "company": contact.get("company") or "your team",
            "email": a.get("email", ""), "phone": a.get("phone", "")}
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), template).strip()


def _eligible(limit: int, only: list | None = None) -> list:
    cutoff = (datetime.now() - timedelta(days=REPEAT_DAYS)).isoformat()
    with core.db() as c:
        if only:
            q = ",".join("?" * len(only))
            rows = c.execute(f"SELECT * FROM wa_contacts WHERE number IN ({q})", only).fetchall()
        else:
            rows = c.execute("SELECT * FROM wa_contacts WHERE status='new' ORDER BY added_at LIMIT ?", (limit * 3,)).fetchall()
    out = [dict(r) for r in rows if r["status"] in ("new", "ready") and (not r["sent_at"] or r["sent_at"] < cutoff) and not _suppressed(r["number"])]
    return out[:limit]


def preview(limit: int = 5) -> dict:
    p = core.load_profile()
    s = settings()
    rows = _eligible(limit)
    return {"dry_run": True, "would_send": len(rows), "remaining_today": max(0, s["daily_cap"] - sent_today()),
            "drafts": [{"to": r["number"], "name": r["name"], "message": render(s["template"], r, p) + FOOTER,
                        "attachment": core.load_profile()["applicant"]["resume_path"]} for r in rows],
            "note": "Preview only: nothing was sent. Numbers are checked for WhatsApp just before a real send."}


def send_batch(limit: int = 10, only: list | None = None, evo: Evo | None = None, sleep=time.sleep, log=print) -> dict:
    """Really send. Guards: live actions allowed, recipients confirmed, window, daily cap, 30-day repeat, opt-outs."""
    import autopilot
    import resume
    p = core.load_profile()
    s = settings()
    ok, why = core.real_actions_allowed(p)
    if not ok:
        return {"error": "live sending blocked: " + why}
    if not s["recipients_expect_my_message"]:
        return {"error": "confirm first: set recipients_expect_my_message=true in WhatsApp settings (the people on the list expect to hear from you)"}
    if autopilot.stopped():
        return {"error": "kill-switch is on"}
    if not autopilot.in_send_window(autopilot.cfg(p)):
        return {"error": "outside the send window: " + autopilot.send_window_text(autopilot.cfg(p))}
    left = s["daily_cap"] - sent_today()
    if left <= 0:
        return {"error": f"daily cap of {s['daily_cap']} reached"}
    evo = evo or Evo()
    if not evo.status()["logged_in"]:
        return {"error": "WhatsApp is not linked. Link it first."}
    rp = Path(p["applicant"]["resume_path"])
    if not rp.exists():
        return {"error": "no resume uploaded"}
    want = min(limit, left)
    rows = _eligible(min(want * 3, 60) if not only else want, only)   # a larger pool: numbers not on WhatsApp do not use up the allowance
    if not rows:
        return {"sent": 0, "note": "nobody eligible (new numbers only, none contacted in the last 30 days, no opt-outs)"}
    on_wa = evo.check([r["number"] for r in rows])
    out = {"sent": 0, "skipped_not_on_whatsapp": 0, "failed": 0, "results": []}
    name = resume.attachment_name(p)
    for i, r in enumerate(rows):
        if autopilot.stopped() or out["sent"] + out["failed"] >= want:
            break
        if not on_wa.get(r["number"], False):
            _mark(r["number"], "no_whatsapp")
            out["skipped_not_on_whatsapp"] += 1
            continue
        msg = render(s["template"], r, p) + FOOTER
        try:
            evo.send_document(r["number"], str(rp), name, msg)
            res = "sent"
        except EvoError as e:
            res = f"error: {str(e)[:100]}"
        with core.db() as c:
            c.execute("INSERT INTO wa_log(number,at,result) VALUES(?,?,?)", (r["number"], core.now_iso(), res))
        if res == "sent":
            _mark(r["number"], "sent", sent_at=core.now_iso())
            out["sent"] += 1
        else:
            _mark(r["number"], "failed", note=res)
            out["failed"] += 1
        out["results"].append({"to": r["number"], "result": res})
        log(f"[whatsapp] {res}: {r['number']}")
        if out["sent"] + out["failed"] < want and i < len(rows) - 1:
            lo, hi = s["delay_sec"]
            sleep(random.uniform(lo, hi))
    return out


def _mark(number: str, status_: str, sent_at: str | None = None, note: str | None = None) -> None:
    with core.db() as c:
        c.execute("UPDATE wa_contacts SET status=?, sent_at=COALESCE(?, sent_at), note=COALESCE(?, note) WHERE number=?",
                  (status_, sent_at, note, number))


# ---------------------------------------------------------------- incoming events (webhook)
def handle_event(payload: dict) -> dict:
    """Replies from people we messaged. 'stop' opts them out for good; any other reply is recorded."""
    if not isinstance(payload, dict) or str(payload.get("event", "")).lower() != "message":
        return {"ignored": True}
    data = payload.get("data") or {}
    info = _g(data, "Info", "info") or {}
    if _g(info, "IsFromMe", "isFromMe") or _g(info, "IsGroup", "isGroup"):
        return {"ignored": True}
    sender = str(_g(info, "Sender", "sender", "Chat", "chat") or "")
    number = re.sub(r"\D", "", sender.split("@")[0].split(":")[0])
    msg = _g(data, "Message", "message") or {}
    text = _g(msg, "conversation") or (_g(msg, "extendedTextMessage") or {}).get("text", "") or ""
    with core.db() as c:
        row = c.execute("SELECT status FROM wa_contacts WHERE number=?", (number,)).fetchone()
    if not row:
        return {"ignored": True}
    if STOP_RX.search(text):
        _mark(number, "optout", note="asked to stop")
        with core.db() as c:
            c.execute("INSERT OR REPLACE INTO suppression(key,reason,at) VALUES(?,?,?)", ("wa:" + number, "asked to stop", core.now_iso()))
        return {"optout": number}
    with core.db() as c:
        c.execute("UPDATE wa_contacts SET status=CASE WHEN status='optout' THEN status ELSE 'replied' END, last_in_at=?, note=? WHERE number=?",
                  (core.now_iso(), text[:200], number))
    try:
        import notify
        notify.telegram(core.load_profile(), f"WhatsApp reply from +{number}: {text[:200]}")
    except Exception:  # noqa: BLE001 - an alert failure must not lose the reply
        pass
    return {"replied": number}


if __name__ == "__main__":  # background run: python hub/whatsapp.py send '{"limit": 10}'
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    opts = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if cmd == "send":
        res = send_batch(int(opts.get("limit", 10)), opts.get("only"))
        print("RESULT " + json.dumps(res, indent=2, default=str), flush=True)
        sys.exit(1 if "error" in res else 0)
    sys.exit(2)
