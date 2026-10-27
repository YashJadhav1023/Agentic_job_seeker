import csv
import io
import json
import sqlite3
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from openpyxl import Workbook

import finder
import mailer

ROOT = Path(__file__).parent
DB = ROOT / "leads.db"
SETTINGS = ROOT / "settings.json"
UPLOADS = ROOT / "uploads"
app = Flask(__name__, static_folder=str(ROOT / "static"))
jobs = {}


def db():
    con = sqlite3.connect(DB)
    con.execute("""CREATE TABLE IF NOT EXISTS companies (
        domain TEXT PRIMARY KEY, data TEXT, created REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS outreach (
        id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT, name TEXT, company TEXT,
        subject TEXT, status TEXT, error TEXT, created REAL)""")
    return con


def save_company(row):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO companies VALUES (?,?,?)",
                    (row["domain"], json.dumps(row), time.time()))


def all_companies():
    with db() as con:
        return [json.loads(r[0]) for r in
                con.execute("SELECT data FROM companies ORDER BY created DESC")]


# ---------- mail settings / CV / outreach ----------

DEFAULT_SETTINGS = {
    "provider": "gmail", "user": "", "password": "", "from_name": "", "signature": "",
    "smtp_host": "smtp.gmail.com", "smtp_port": 587, "imap_host": "imap.gmail.com",
    "greeting_fallback": "Hiring Team", "delay": 90, "daily_cap": 40,
    "subject": "{my_name} — interested in joining {company}",
    "body": ("Hi {first_name},\n\n"
             "I came across {company} and really like what you're building. I'm a [your role] with "
             "[X years] of experience in [skills], and I'd love to contribute to your team.\n\n"
             "Recently I [one concrete achievement with a number].\n\n"
             "I've attached my CV. Would you be open to a short call, or could you point me to the "
             "right person for hiring?\n\n"
             "Thanks for your time,\n{my_name}"),
}


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS.exists():
        try:
            s.update(json.loads(SETTINGS.read_text(encoding="utf-8")))
        except ValueError:
            pass
    return s


def resume_path():
    files = sorted(UPLOADS.glob("*")) if UPLOADS.exists() else []
    return files[0] if files else None


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    s["password"] = "••••••••" if s["password"] else ""
    r = resume_path()
    s["resume"] = r.name if r else ""
    return jsonify(s)


@app.post("/api/settings")
def set_settings():
    body = request.get_json(force=True)
    s = load_settings()
    for k in DEFAULT_SETTINGS:
        if k in body and not (k == "password" and (not body[k] or body[k].startswith("•"))):
            s[k] = body[k]
    if body.get("provider") in mailer.PRESETS:
        s.update(mailer.PRESETS[body["provider"]])
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return jsonify(ok=True)


@app.post("/api/settings/test")
def test_settings():
    s = load_settings()
    if not s["user"] or not s["password"]:
        return jsonify(ok=False, info="Add your email and app password first.")
    ok, info = mailer.test_connection(s)
    return jsonify(ok=ok, info=info)


@app.post("/api/resume")
def upload_resume():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="no file"), 400
    name = Path(f.filename).name
    if Path(name).suffix.lower() not in (".pdf", ".doc", ".docx"):
        return jsonify(error="CV must be PDF or Word"), 400
    UPLOADS.mkdir(exist_ok=True)
    for old in UPLOADS.glob("*"):
        old.unlink()
    f.save(UPLOADS / name)
    return jsonify(ok=True, name=name)


@app.delete("/api/resume")
def delete_resume():
    for old in UPLOADS.glob("*") if UPLOADS.exists() else []:
        old.unlink()
    return jsonify(ok=True)


def sent_today():
    start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM outreach WHERE status='sent' AND created>=?",
                           (start,)).fetchone()[0]


@app.get("/api/contacted")
def contacted():
    with db() as con:
        rows = con.execute("SELECT email, name, company, subject, status, error, created FROM outreach "
                           "ORDER BY created DESC").fetchall()
    keys = ("email", "name", "company", "subject", "status", "error", "created")
    return jsonify(rows=[dict(zip(keys, r)) for r in rows], sent_today=sent_today())


@app.post("/api/outreach")
def start_outreach():
    body = request.get_json(force=True)
    s = load_settings()
    if not s["user"] or not s["password"]:
        return jsonify(error="Connect your email account in Settings first."), 400
    recipients = [r for r in body.get("recipients", []) if "@" in r.get("email", "")]
    skipped = []
    if not body.get("resend"):
        with db() as con:
            done = {r[0] for r in con.execute(
                "SELECT email FROM outreach WHERE status IN ('sent','drafted')")}
        skipped = [r["email"] for r in recipients if r["email"].lower() in done]
        recipients = [r for r in recipients if r["email"].lower() not in done]
    if not recipients:
        return jsonify(error="Everyone selected was already contacted."), 400
    rp = resume_path() if body.get("attach") else None
    spec = {"recipients": recipients, "subject": body["subject"], "body": body["body"],
            "mode": "send" if body.get("mode") == "send" else "draft",
            "attachment": str(rp) if rp else None,
            "delay": body.get("delay") or s["delay"], "daily_cap": s["daily_cap"]}
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "status": "running", "log": [], "results": [], "stop": False}
    jobs[jid] = job

    def log(msg):
        job["log"].append(time.strftime("%H:%M:%S ") + msg)

    def record(r, subject, status, error):
        with db() as con:
            con.execute("INSERT INTO outreach (email,name,company,subject,status,error,created) "
                        "VALUES (?,?,?,?,?,?,?)", (r["email"].lower(), r.get("name", ""),
                                                   r.get("company", ""), subject, status, error, time.time()))
        job["results"].append({"email": r["email"], "status": status})

    if skipped:
        log(f"skipped {len(skipped)} already contacted: {', '.join(skipped[:5])}")
    log(f"{'sending' if spec['mode'] == 'send' else 'saving drafts for'} {len(recipients)} emails "
        f"from {s['user']}" + (f" with {rp.name} attached" if rp else ""))

    def work():
        try:
            mailer.run_outreach(s, spec, log, record, sent_today, lambda: job["stop"])
            job["status"] = "stopped" if job["stop"] else "done"
        except Exception as e:
            log(f"error: {e}")
            job["status"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return jsonify(id=jid, skipped=len(skipped))


@app.post("/api/preview")
def preview():
    body = request.get_json(force=True)
    s = load_settings()
    r = body.get("recipient", {})
    text = mailer.render(body.get("body", ""), r, s)
    if s.get("signature"):
        text = text.rstrip() + "\n\n" + s["signature"]
    return jsonify(subject=mailer.render(body.get("subject", ""), r, s), body=text)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/api/search")
def start_search():
    params = request.get_json(force=True)
    if not params.get("industry"):
        return jsonify(error="industry is required"), 400
    if params.get("skip_saved"):
        params["skip_domains"] = [c["domain"] for c in all_companies()]
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "status": "running", "log": [], "results": [], "stop": False}
    jobs[jid] = job

    def log(msg):
        job["log"].append(time.strftime("%H:%M:%S ") + msg)

    def emit(row):
        job["results"].append(row)
        save_company(row)

    def work():
        try:
            finder.run_search(params, emit, log, lambda: job["stop"])
            job["status"] = "stopped" if job["stop"] else "done"
        except Exception as e:
            log(f"error: {e}")
            job["status"] = "error"

    threading.Thread(target=work, daemon=True).start()
    return jsonify(id=jid)


@app.get("/api/job/<jid>")
def job_status(jid):
    job = jobs.get(jid)
    if not job:
        return jsonify(error="not found"), 404
    since = int(request.args.get("since", 0))
    return jsonify(status=job["status"], log=job["log"][since:], log_len=len(job["log"]),
                   results=job["results"])


@app.post("/api/job/<jid>/stop")
def job_stop(jid):
    if jid in jobs:
        jobs[jid]["stop"] = True
    return jsonify(ok=True)


@app.get("/api/saved")
def saved():
    return jsonify(results=all_companies())


@app.delete("/api/saved")
def clear_saved():
    with db() as con:
        con.execute("DELETE FROM companies")
    return jsonify(ok=True)


def flat_rows(companies, min_conf):
    order = {"high": 3, "medium": 2, "low": 1}
    rows = []
    for c in companies:
        leads = [l for l in c["leads"] if order[l["confidence"]] >= order.get(min_conf, 1)]
        base = {"Company": c["company"], "Website": c["domain"], "Size": c["size"],
                "Location": c["location"], "Industry": c["industry"],
                "Company emails": c["generic_emails"], "HR inbox": c.get("hr_emails", "")}
        if not leads:
            rows.append({**base, "Name": "", "Role": "", "Type": "", "Email": "", "Confidence": "",
                         "Source": "", "Alt emails": "", "LinkedIn": ""})
        for l in leads:
            rows.append({**base, "Name": l["name"], "Role": l["role"],
                         "Type": "HR" if l.get("kind") == "hr" else "Decision maker", "Email": l["email"],
                         "Confidence": l["confidence"], "Source": l["note"],
                         "Alt emails": l["alternatives"], "LinkedIn": l["linkedin"]})
    return rows


@app.post("/api/export/<fmt>")
def export(fmt):
    body = request.get_json(force=True)
    rows = flat_rows(body.get("results", []), body.get("min_conf", "low"))
    cols = list(rows[0].keys()) if rows else ["Company"]
    stamp = time.strftime("%Y%m%d-%H%M")
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        return send_file(io.BytesIO(buf.getvalue().encode("utf-8-sig")), mimetype="text/csv",
                         as_attachment=True, download_name=f"leads-{stamp}.csv")
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(cols)
    for r in rows:
        ws.append([r[c] for c in cols])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(
            45, max(10, *(len(str(c.value or "")) for c in col)))
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return send_file(out, as_attachment=True, download_name=f"leads-{stamp}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:5050")).start()
    app.run(host="127.0.0.1", port=5050, debug=False, threaded=True)
