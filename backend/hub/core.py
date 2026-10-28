"""job-hub core: profile, tracker DB, background runner, Gmail mailer. Stdlib + PyYAML only."""
import json, os, random, re, smtplib, sqlite3, ssl, subprocess, sys, time, uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Every path can be overridden from the environment so the same code runs locally (MCP) and in the cloud container.
PROFILE_PATH = Path(os.environ.get("JOBHUB_PROFILE") or (ROOT / "profile.yaml"))
if not PROFILE_PATH.exists() and not os.environ.get("JOBHUB_PROFILE"):
    PROFILE_PATH = ROOT / "profile.example.yaml"
SECRETS = Path(os.environ.get("JOBHUB_SECRETS_DIR") or (ROOT / ".secrets"))
RUNS = Path(os.environ.get("JOBHUB_RUNS_DIR") or (ROOT / "runs"))
DB_PATH = Path(os.environ.get("JOBHUB_DB") or (ROOT / "data" / "hub.db"))
_VENV_PY = ROOT / ".venv" / "bin" / "python"
PY = os.environ.get("JOBHUB_PYTHON") or (str(_VENV_PY) if _VENV_PY.exists() else sys.executable)
TOOLS = ROOT / "tools"
RUNS.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
SECRETS.mkdir(mode=0o700, parents=True, exist_ok=True)
if str(ROOT / "hub") not in sys.path:
    sys.path.insert(0, str(ROOT / "hub"))


# ---------------------------------------------------------------- profile
def load_profile() -> dict:
    import yaml
    return yaml.safe_load(PROFILE_PATH.read_text())


def missing_profile_fields(p: dict) -> list:
    out = []
    if p["compensation"].get("current_ctc_inr") is None:
        out.append("compensation.current_ctc_inr")
    if p["compensation"].get("notice_period_days") is None:
        out.append("compensation.notice_period_days")
    if not Path(p["applicant"]["resume_path"]).exists():
        out.append("applicant.resume_path (file not found)")
    return out


def real_actions_allowed(p: dict) -> tuple:
    """Real applications/emails need: user confirmation flag + no missing mandatory profile fields."""
    if not p["safety"].get("confirmed_by_user"):
        return False, "profile.yaml safety.confirmed_by_user is false - review the profile and set it to true yourself"
    miss = missing_profile_fields(p)
    if miss:
        return False, "profile.yaml is missing: " + ", ".join(miss)
    return True, ""


def read_secret(name: str):
    f = SECRETS / name
    if f.exists():
        mode = f.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError(f"{f} must be chmod 600 (is {oct(mode)})")
        return f.read_text().strip()
    # Container fallback: JOBHUB_SECRET_<NAME> (e.g. JOBHUB_SECRET_GMAIL_APP_PASSWORD) from the .env file.
    return os.environ.get("JOBHUB_SECRET_" + re.sub(r"\W", "_", name).upper()) or None


def read_env_file(name: str) -> dict:
    txt = read_secret(name)
    env = {}
    for line in (txt or "").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


# ---------------------------------------------------------------- DB
def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript("""
    CREATE TABLE IF NOT EXISTS hr_contacts(
      id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, role TEXT, company TEXT, domain TEXT,
      city TEXT, confidence TEXT, note TEXT, kind TEXT, found_at TEXT, status TEXT DEFAULT 'new');
    CREATE TABLE IF NOT EXISTS emails_sent(
      id INTEGER PRIMARY KEY, to_email TEXT, domain TEXT, subject TEXT, sent_at TEXT, result TEXT);
    CREATE TABLE IF NOT EXISTS applications(
      id INTEGER PRIMARY KEY, platform TEXT, title TEXT, company TEXT, location TEXT, url TEXT UNIQUE,
      status TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS runs(
      id TEXT PRIMARY KEY, kind TEXT, args TEXT, started TEXT, log TEXT, pid INTEGER, dry_run INTEGER);
    CREATE TABLE IF NOT EXISTS jobs(
      id INTEGER PRIMARY KEY, source TEXT, ext_id TEXT, url TEXT UNIQUE, title TEXT, company TEXT, location TEXT,
      posted TEXT, description TEXT, company_site TEXT, recruiter TEXT, apply_type TEXT, jd_emails TEXT,
      score REAL, matched TEXT, status TEXT DEFAULT 'new', reason TEXT, contact TEXT, found_at TEXT, updated_at TEXT);
    CREATE TABLE IF NOT EXISTS suppression(
      key TEXT PRIMARY KEY, reason TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS email_checks(
      email TEXT PRIMARY KEY, result TEXT, checked_at TEXT);
    CREATE TABLE IF NOT EXISTS domains(
      company TEXT PRIMARY KEY, domain TEXT, source TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS platform_state(
      platform TEXT PRIMARY KEY, blocked_until TEXT, reason TEXT, at TEXT);
    CREATE TABLE IF NOT EXISTS kv(
      key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS posts_seen(
      fp TEXT PRIMARY KEY, at TEXT, verdict TEXT);
    """)
    return c


def kv_get(key: str, default=None):
    with db() as c:
        r = c.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else default


def kv_set(key: str, value) -> None:
    with db() as c:
        c.execute("INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))


def rotate(name: str, items: list, n: int) -> list:
    """Next `n` items of `items`, continuing where the previous call stopped, so over several runs every
    combination is visited while each run stays small."""
    if not items:
        return []
    n = max(1, min(n, len(items)))
    start = int(kv_get("rotate:" + name, 0) or 0) % len(items)
    kv_set("rotate:" + name, (start + n) % len(items))
    return [items[(start + i) % len(items)] for i in range(n)]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def start_of_today_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def sent_today() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM emails_sent WHERE result='sent' AND sent_at>=?",
                         (start_of_today_iso(),)).fetchone()[0]


def sync_applications() -> int:
    """Sync applications from bot logs into SQLite applications table."""
    added = 0
    bot_db = TOOLS / "job-automation" / "applied_jobs.db"
    if bot_db.exists():
        try:
            b = sqlite3.connect(bot_db)
            b.row_factory = sqlite3.Row
            rows = b.execute("SELECT url, title, company, location, platform, applied_at FROM applied").fetchall()
            b.close()
            with db() as c:
                for r in rows:
                    cur = c.execute(
                        "INSERT OR IGNORE INTO applications(platform, title, company, location, url, status, at) "
                        "VALUES(?,?,?,?,?,?,?)",
                        (r["platform"].lower(), r["title"], r["company"] or "", r["location"] or "", r["url"], "applied",
                         str(r["applied_at"]).replace(" ", "T"))  # "YYYY-MM-DD HH:MM:SS" sorts below "YYYY-MM-DDT..", so it was never counted as today
                    )
                    if cur.rowcount:
                        added += 1
        except Exception:
            pass
    with db() as c:  # rows stored before the fix above
        c.execute("UPDATE applications SET at = REPLACE(at, ' ', 'T') WHERE at LIKE '% %'")
    for txt in (TOOLS / "multi-platform-job-apply-bot").glob("applied_jobs_*.txt"):
        lines = txt.read_text(errors="replace").splitlines()[2:]
        with db() as c:
            for ln in lines:
                ln = ln.strip().lstrip("✅ ")
                if not ln:
                    continue
                parts = ln.split(" @ ")
                title = parts[0].strip()
                company = parts[1].strip() if len(parts) > 1 else ""
                url = f"linkedin://applied/{company.lower().replace(' ', '-')}/{title.lower().replace(' ', '-')}"
                cur = c.execute(
                    "INSERT OR IGNORE INTO applications(platform, title, company, location, url, status, at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    ("linkedin", title, company, "India", url, "applied", now_iso())
                )
                if cur.rowcount:
                    added += 1
    return added


def applied_today() -> int:
    """Count today's applications across the hub tracker and the vendored bot DB."""
    sync_applications()
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM applications WHERE status='applied' AND at>=?",
                         (start_of_today_iso(),)).fetchone()[0]



# ---------------------------------------------------------------- background runner
def _profile_groups(kind: str) -> set:
    """Browser profiles (or exclusive kinds) a run uses. Runs whose sets intersect must not overlap."""
    if kind.startswith("autopilot"):
        return {"autopilot", "linkedin", "naukri", "indeed"}
    out = {site for site in ("linkedin", "naukri", "indeed") if site in kind}
    if kind.startswith(("posts", "prepare-linkedin")):
        out.add("linkedin")
    return out or {kind}


def _profile_group(kind: str) -> str:  # kept for callers/tests that expect one name
    g = _profile_groups(kind)
    return sorted(g)[0] if len(g) == 1 else "+".join(sorted(g))


def running_conflict(kind: str) -> str:
    """Run id of an active run that would collide with `kind`, else ''."""
    groups = _profile_groups(kind)
    with db() as c:
        rows = c.execute("SELECT id, kind FROM runs ORDER BY started DESC LIMIT 30").fetchall()
    for r in rows:
        if _profile_groups(r["kind"]) & groups and run_status(r["id"], 1).get("running"):
            return r["id"]
    return ""


MAX_PARALLEL = int(os.environ.get("JOBHUB_MAX_PARALLEL", "4"))


def running_runs() -> list:
    with db() as c:
        rows = c.execute("SELECT id FROM runs ORDER BY started DESC LIMIT 50").fetchall()
    return [r["id"] for r in rows if run_status(r["id"], 1).get("running")]


def spawn(kind: str, argv: list, cwd: Path, env_extra: dict, dry_run: bool, args: dict) -> dict:
    """Start a background run through hub/launcher.py, which records the child's exit code in <log>.exit.
    Up to MAX_PARALLEL (default 4) runs at once; runs that share a browser profile never overlap."""
    busy = running_conflict(kind)
    if busy:  # two Chromium instances on one profile crash with "ProcessSingleton" and used to trigger false cooldowns
        return {"error": f"{kind} cannot start: {busy} is still running (wait for it or cancel_run)"}
    active = running_runs()
    if len(active) >= MAX_PARALLEL:
        return {"error": f"{kind} cannot start: {len(active)} runs are still running (limit {MAX_PARALLEL}): "
                         + ", ".join(active)}
    rid = f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    log = RUNS / f"{rid}.log"
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "DISPLAY", "WAYLAND_DISPLAY",
                                                         "XAUTHORITY", "XDG_RUNTIME_DIR", "LANG",
                                                         "KIRO_API_KEY", "LLM_API_KEY", "TELEGRAM_BOT_TOKEN")
           or k.startswith(("JOBHUB_", "PLAYWRIGHT_"))}
    env.update({k: str(v) for k, v in env_extra.items()})
    wrapped = [PY, str(ROOT / "hub" / "launcher.py"), str(log) + ".exit"] + list(argv)
    with open(log, "wb") as lf:
        proc = subprocess.Popen(wrapped, cwd=str(cwd), env=env, stdout=lf, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    with db() as c:
        c.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                  (rid, kind, json.dumps(args), now_iso(), str(log), proc.pid, int(dry_run)))
    return {"run_id": rid, "pid": proc.pid, "log": str(log), "dry_run": dry_run}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        if stat.exists() and stat.read_text().split(")")[-1].split()[0] == "Z":
            return False
        return True
    except OSError:
        return False


def run_status(rid: str, tail: int = 40) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if not r:
        return {"error": f"unknown run_id {rid}"}
    exitf = Path(str(r["log"]) + ".exit")
    code = None
    if exitf.exists():
        try:
            code = int(exitf.read_text().strip())
        except ValueError:
            code = None
    alive = _alive(r["pid"]) and code is None
    state = "running" if alive else ("cancelled" if Path(str(r["log"]) + ".cancel").exists()
                                      else "ok" if code == 0 else "failed" if code is not None else "unknown")
    lines = Path(r["log"]).read_text(errors="replace").splitlines()[-tail:] if Path(r["log"]).exists() else []
    return {"run_id": rid, "kind": r["kind"], "running": alive, "state": state, "exit_code": code,
            "dry_run": bool(r["dry_run"]), "started": r["started"], "log_tail": [redact(l) for l in lines]}


def cancel_run(rid: str) -> dict:
    with db() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (rid,)).fetchone()
    if not r:
        return {"error": f"unknown run_id {rid}"}
    if not _alive(r["pid"]):
        return {"cancelled": False, "note": "run is not running"}
    import signal
    Path(str(r["log"]) + ".cancel").write_text(now_iso())
    try:
        os.killpg(r["pid"], signal.SIGTERM)
    except OSError as e:
        return {"error": f"could not signal run: {e}"}
    return {"cancelled": True, "run_id": rid}


def redact(s: str) -> str:
    return re.sub(r"(password|passwd|token|secret)\s*[=:]\s*\S+", r"\1=<redacted>", s, flags=re.I)


# ---------------------------------------------------------------- mailer
CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def eligible_contacts(p: dict, limit: int) -> list:
    hr = p["hr_outreach"]
    minrank = CONF_RANK[hr["min_confidence"]]
    cutoff = (datetime.now() - timedelta(days=hr["one_email_per_company_days"])).isoformat()
    out = []
    with db() as c:
        rows = c.execute("SELECT * FROM hr_contacts WHERE status IN ('new','queued') ORDER BY "
                         "CASE confidence WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id").fetchall()
        for r in rows:
            if CONF_RANK.get(r["confidence"], 0) < minrank:
                continue
            # Suppression check
            if c.execute("SELECT 1 FROM suppression WHERE key IN (?,?)", (r["email"].lower(), r["domain"].lower())).fetchone():
                continue
            # 60-day company/domain deduplication
            if c.execute("SELECT 1 FROM emails_sent WHERE domain=? AND result='sent' AND sent_at>=?",
                         (r["domain"], cutoff)).fetchone():
                continue
            if r["domain"] in {x["domain"] for x in out}:
                continue
            # Recruiter contact must be verified (valid or catch_all)
            chk = c.execute("SELECT result FROM email_checks WHERE email=?", (r["email"].lower(),)).fetchone()
            if not chk or chk["result"] not in ("valid", "catch_all"):
                import contacts
                res = contacts.verify_email(r["email"], hr.get("gmail_user", ""))
                if res not in ("valid", "catch_all"):
                    continue
            out.append(dict(r))
            if len(out) >= limit:
                break
    return out



def send_via_gmail(p: dict, to: str, subject: str, body: str, attach_resume: bool = True) -> None:
    pw = read_secret("gmail_app_password")
    if not pw:
        raise RuntimeError(f"Gmail app password missing: put it in {SECRETS/'gmail_app_password'} (chmod 600)")
    user = p["hr_outreach"]["gmail_user"]
    msg = EmailMessage()
    msg["From"] = f"{p['applicant']['name']} <{user}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Reply-To"] = user
    msg.set_content(body)
    if attach_resume:
        rp = Path(p["applicant"]["resume_path"])
        import resume  # lazy: resume imports core
        msg.add_attachment(rp.read_bytes(), maintype="application", subtype="pdf", filename=resume.attachment_name(p))
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
        s.login(user, pw.replace(" ", ""))
        s.send_message(msg)
