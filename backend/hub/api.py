"""Agentic Job Seeker - HTTPS API over the job-hub tools, for the Android app and remote MCP clients.

Every tool in server.TOOLS is exposed automatically, so the app, the stdio MCP server and this API never drift:

  GET  /health                       liveness (no auth)
  GET  /api/v1/tools                 tool catalogue with JSON schemas (the app renders forms from these)
  POST /api/v1/tools/{name}          run a tool; body = tool arguments
  GET  /api/v1/runs                  recent background runs (queued ones first)
  GET  /api/v1/queue                 run queue: running now, waiting in FIFO order, limit
  POST /api/v1/browser/session       one-tap remote browser for linking LinkedIn / Naukri / Indeed (returns a single-use URL)
  GET  /api/v1/runs/{run_id}         run status + log tail
  GET/PUT /api/v1/profile            read / replace profile.yaml (validated)
  GET/POST /api/v1/resume            which resume is live / upload the PDF used for search, scoring, emails and applying
  GET  /api/v1/resume/file           download the live resume (to check it is the right one)
  GET  /api/v1/accounts              linked accounts: LinkedIn / Naukri / Indeed sessions, Gmail
  PUT  /api/v1/accounts/gmail        link Gmail: address + app password (optionally test the SMTP login)
  PUT  /api/v1/secrets/{name}        write-only secret store (e.g. gmail_app_password); values are never returned
  POST /mcp                          MCP Streamable-HTTP (JSON response mode) so Kiro/Claude can use the cloud hub

Auth: `Authorization: Bearer <JOBHUB_API_TOKEN>` on everything except /health. The server refuses to start without a
token of at least 32 characters. Repeated bad tokens from one IP are locked out for 15 minutes.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml  # noqa: E402
from fastapi import Body, Depends, FastAPI, File, HTTPException, Request, UploadFile  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response  # noqa: E402

import browser_access  # noqa: E402
import core  # noqa: E402
import keys  # noqa: E402
import resume  # noqa: E402
import scheduler  # noqa: E402
import server  # noqa: E402

API_VERSION = "1.0.0"
MIN_TOKEN_LEN = 32
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_PROFILE_BYTES = 64 * 1024
ALLOWED_SECRETS = {"gmail_app_password", "reacher.env", "kiro_api_key", "llm_api_key", "telegram_bot_token", "firecrawl_api_key"}
SECRET_TO_KEY = {v[1]: k for k, v in keys.KNOWN.items()}   # secret file name -> logical key name
EMAIL_RX = __import__("re").compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PROFILE_REQUIRED = ("applicant", "compensation", "search", "limits", "hr_outreach", "safety")


# ---------------------------------------------------------------- auth + brute-force lockout
class _Lockout:
    def __init__(self, max_fail: int = 10, window: int = 300, ban: int = 900):
        self.max_fail, self.window, self.ban = max_fail, window, ban
        self.fails: dict[str, deque] = defaultdict(deque)
        self.banned: dict[str, float] = {}
        self.lock = threading.Lock()

    def check(self, ip: str) -> None:
        with self.lock:
            until = self.banned.get(ip, 0)
            if until > time.time():
                raise HTTPException(429, "too many failed auth attempts; try later")
            self.banned.pop(ip, None)

    def fail(self, ip: str) -> None:
        now = time.time()
        with self.lock:
            q = self.fails[ip]
            q.append(now)
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.max_fail:
                self.banned[ip] = now + self.ban
                q.clear()


lockout = _Lockout()


def _token() -> str:
    t = os.environ.get("JOBHUB_API_TOKEN", "")
    if len(t) < MIN_TOKEN_LEN:
        raise RuntimeError(f"JOBHUB_API_TOKEN must be set and at least {MIN_TOKEN_LEN} chars "
                           "(generate one with: openssl rand -hex 32)")
    return t


def _client_ip(req: Request) -> str:
    # Behind Caddy the real client is the first X-Forwarded-For hop; Caddy overwrites the header, so it is trusted
    # only when JOBHUB_TRUST_PROXY=1 (set in docker-compose).
    if os.environ.get("JOBHUB_TRUST_PROXY") == "1":
        xff = req.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return req.client.host if req.client else "unknown"


def require_auth(req: Request) -> None:
    ip = _client_ip(req)
    lockout.check(ip)
    hdr = req.headers.get("authorization", "")
    scheme, _, supplied = hdr.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), _token().encode()):
        lockout.fail(ip)
        raise HTTPException(401, "invalid or missing bearer token", headers={"WWW-Authenticate": "Bearer"})


# ---------------------------------------------------------------- tool dispatch
def run_tool(name: str, args: dict) -> dict:
    """Same semantics as the stdio MCP server: returns {"result", "is_error"} and never raises for tool errors."""
    if name not in server.HANDLERS:
        raise HTTPException(404, f"unknown tool {name}")
    if not isinstance(args, dict):
        raise HTTPException(422, "arguments must be a JSON object")
    missing = [r for r in server.REQUIRED.get(name, []) if r not in args]
    if missing:
        raise HTTPException(422, f"missing required argument(s): {', '.join(missing)}")
    try:
        res = server.HANDLERS[name](args)
    except KeyError as e:
        return {"tool": name, "is_error": True, "result": {"error": f"missing argument: {e}"}}
    except Exception as e:  # noqa: BLE001 - report tool failures to the client instead of a 500
        return {"tool": name, "is_error": True, "result": {"error": f"{type(e).__name__}: {e}"}}
    res = json.loads(json.dumps(res, default=str))
    return {"tool": name, "is_error": isinstance(res, dict) and "error" in res, "result": res}


def create_app() -> FastAPI:
    _token()  # fail fast on a missing/weak token
    @contextlib.asynccontextmanager
    async def lifespan(_app):
        scheduler.start()  # 24/7 autopilot / post-scan timer (off unless the profile sets schedule_every_hours)
        yield

    app = FastAPI(title="Agentic Job Seeker API", version=API_VERSION, lifespan=lifespan,
                  docs_url=None if os.environ.get("JOBHUB_DISABLE_DOCS") == "1" else "/docs", redoc_url=None)
    auth = [Depends(require_auth)]

    @app.get("/health")
    def health():
        return {"ok": True, "version": API_VERSION, "tools": len(server.TOOLS)}

    @app.get("/api/v1/tools", dependencies=auth)
    def tools():
        return {"tools": server.tool_list()}

    @app.post("/api/v1/tools/{name}", dependencies=auth)
    async def call(name: str, args: dict = Body(default_factory=dict)):
        return await run_in_threadpool(run_tool, name, args)

    @app.get("/api/v1/runs", dependencies=auth)
    def runs(limit: int = 30):
        limit = max(1, min(limit, 200))
        core.dispatch_queue()
        with core.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM run_queue ORDER BY n")]  # waiting runs first
            ids += [r["id"] for r in c.execute("SELECT id FROM runs ORDER BY started DESC LIMIT ?", (limit,))]
        return {"runs": [{k: v for k, v in core.run_status(i, 3).items()} for i in ids]}
    @app.post("/api/v1/whatsapp/numbers", dependencies=auth)
    async def whatsapp_numbers(file: UploadFile = File(...), company: str = "", default_country_code: str = ""):
        """Upload a PDF / Word / Excel / CSV / text file; every phone number in it is added to the WhatsApp list."""
        import whatsapp
        data = await file.read(whatsapp.MAX_FILE_BYTES + 1)
        try:
            text = await run_in_threadpool(whatsapp.file_to_text, data, file.filename or "")
        except Exception as e:  # noqa: BLE001 - corrupt or unsupported files are the caller's problem, not a 500
            raise HTTPException(422, f"could not read the file: {e}") from e
        return await run_in_threadpool(whatsapp.import_numbers, text, f"file:{(file.filename or '')[:60]}",
                                       default_country_code or None, company)

    @app.post("/api/v1/whatsapp/webhook")
    async def whatsapp_webhook(req: Request, s: str = ""):
        """Called by the gateway for incoming messages. Authenticated by the secret in the URL, not the bearer token."""
        import whatsapp
        ip = _client_ip(req)
        lockout.check(ip)
        if not hmac.compare_digest(s.encode(), whatsapp.webhook_secret().encode()):
            lockout.fail(ip)
            raise HTTPException(401, "bad webhook secret")
        try:
            payload = await req.json()
        except Exception:  # noqa: BLE001
            return {"ok": True}
        res = await run_in_threadpool(whatsapp.handle_event, payload)
        return {"ok": True, **res}

    @app.get("/api/v1/queue", dependencies=auth)
    def queue():
        return core.queue_state()

    @app.post("/api/v1/browser/session", dependencies=auth)
    async def browser_session(req: Request, body: dict = Body(default_factory=dict)):
        """Start the sign-in browser for a platform and return a single-use link that opens it directly: no basic-auth
        prompt and no VNC password prompt. The link sets a 30-minute cookie that Caddy checks for /browser/*."""
        platform = str(body.get("platform", ""))
        if platform not in ("linkedin", "naukri", "indeed"):
            raise HTTPException(422, "platform must be linkedin, naukri or indeed")
        res = await run_in_threadpool(run_tool, "login_platform", {"platform": platform})
        if res["is_error"]:
            raise HTTPException(409, str(res["result"].get("error")))
        base = str(req.base_url).rstrip("/")
        return {"platform": platform, "run_id": res["result"].get("run_id"), "queued": bool(res["result"].get("queued")),
                "url": f"{base}/api/v1/browser/enter?t={browser_access.new_ticket(platform)}", "expires_in": browser_access.TICKET_TTL,
                "note": f"Sign in to {platform} in the browser that opens. The window closes by itself once you are signed in."}

    @app.get("/api/v1/browser/enter")
    def browser_enter(req: Request, t: str = ""):
        ip = _client_ip(req)
        lockout.check(ip)
        if browser_access.use_ticket(t) is None:
            lockout.fail(ip)
            raise HTTPException(401, "link expired or already used - open it again from the app")
        r = RedirectResponse(browser_access.vnc_url(os.environ.get("JOBHUB_VNC_PASSWORD", "")), status_code=303)
        r.set_cookie(browser_access.COOKIE, browser_access.cookie_value(), max_age=browser_access.COOKIE_TTL, path="/browser",
                     httponly=True, secure=True, samesite="lax")
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.get("/api/v1/attention/open")
    def attention_open(req: Request, id: int = 0, k: str = ""):
        """Link from a "job bot needs you" notification: opens the remote browser where the bot waits on a login or a
        human check. Valid only while that request is open (signed with the API token, expires with the request)."""
        import attention
        ip = _client_ip(req)
        lockout.check(ip)
        if not attention.link_ok(id, k):
            lockout.fail(ip)
            raise HTTPException(403, "this request is finished or expired")
        r = RedirectResponse(browser_access.vnc_url(os.environ.get("JOBHUB_VNC_PASSWORD", "")), status_code=303)
        r.set_cookie(browser_access.COOKIE, browser_access.cookie_value(), max_age=browser_access.COOKIE_TTL, path="/browser",
                     httponly=True, secure=True, samesite="lax")
        r.headers["Referrer-Policy"] = "no-referrer"
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.get("/internal/browser-auth")
    def browser_auth(req: Request):
        """Called by Caddy (forward_auth) before it serves /browser/*: 204 only with a valid ticket cookie."""
        if browser_access.cookie_ok(req.cookies.get(browser_access.COOKIE, "")):
            return Response(status_code=204)
        raise HTTPException(401, "remote browser access needs a link from the app")


    @app.get("/api/v1/runs/{run_id}", dependencies=auth)
    def run(run_id: str, tail: int = 80):
        res = core.run_status(run_id, max(1, min(tail, 500)))
        if "error" in res:
            raise HTTPException(404, res["error"])
        return res

    @app.get("/api/v1/profile", dependencies=auth)
    def get_profile():
        return {"path": str(core.PROFILE_PATH), "yaml": core.PROFILE_PATH.read_text(), "profile": core.load_profile()}

    @app.put("/api/v1/profile", dependencies=auth)
    async def put_profile(req: Request):
        raw = await req.body()
        if len(raw) > MAX_PROFILE_BYTES:
            raise HTTPException(413, "profile too large")
        try:
            data = yaml.safe_load(raw.decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            raise HTTPException(422, f"invalid YAML: {e}") from e
        if not isinstance(data, dict) or [k for k in PROFILE_REQUIRED if k not in data]:
            raise HTTPException(422, f"profile must contain sections: {', '.join(PROFILE_REQUIRED)}")
        target = Path(os.environ.get("JOBHUB_PROFILE") or (core.ROOT / "profile.yaml"))
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(raw)
        tmp.replace(target)
        core.PROFILE_PATH = target
        return {"saved": str(target), "missing_profile_fields": core.missing_profile_fields(data)}

    @app.get("/api/v1/resume", dependencies=auth)
    def get_resume():
        return resume.info(core.load_profile())

    @app.get("/api/v1/resume/file", dependencies=auth)
    def get_resume_file():
        p = core.load_profile()
        f = Path(p["applicant"]["resume_path"])
        if not f.exists():
            raise HTTPException(404, "no resume uploaded")
        return FileResponse(f, media_type="application/pdf", filename=resume.attachment_name(p))

    @app.post("/api/v1/resume", dependencies=auth)
    async def upload_resume(file: UploadFile = File(...), update_profile: bool = True, replace_search: bool = False,
                            sync_naukri: bool = False):
        """Save the resume, then (by default) refresh profile skills from it so search and scoring follow the new
        resume. replace_search=true also replaces search keywords/title rules (needs an LLM); sync_naukri=true pushes
        it to the Naukri profile (live only when the profile allows live actions)."""
        data = await file.read(MAX_RESUME_BYTES + 1)
        if len(data) > MAX_RESUME_BYTES:
            raise HTTPException(413, "resume must be <= 5 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(422, "resume must be a PDF")
        p = core.load_profile()
        out = {"saved": p["applicant"]["resume_path"], "bytes": len(data),
               "resume": await run_in_threadpool(resume.save_upload, p, data, file.filename or "resume.pdf")}
        replace_search = replace_search or bool((p.get("search") or {}).get("auto_from_resume"))
        if update_profile and out["resume"].get("readable"):
            out["profile_update"] = await run_in_threadpool(server.resume_to_profile, {"apply": True, "replace_search": replace_search})
        if sync_naukri:
            out["naukri_sync"] = await run_in_threadpool(server.sync_resume, {})
        return out

    @app.get("/api/v1/accounts", dependencies=auth)
    def accounts():
        p = core.load_profile()
        return {"platforms": {pl: server._has_session(pl) for pl in ("linkedin", "naukri", "indeed")},
                "gmail": {"user": p["hr_outreach"].get("gmail_user", ""), "app_password": bool(core.read_secret("gmail_app_password"))},
                "remote_browser_url": os.environ.get("JOBHUB_REMOTE_BROWSER_URL", "")}

    @app.put("/api/v1/accounts/gmail", dependencies=auth)
    async def link_gmail(email: str = Body(..., embed=True, max_length=200),
                         app_password: str = Body("", embed=True, max_length=64), test: bool = Body(True, embed=True)):
        email = email.strip()
        if not EMAIL_RX.fullmatch(email):
            raise HTTPException(422, "invalid email address")
        pw = app_password.replace(" ", "").strip()
        if pw and len(pw) != 16:
            raise HTTPException(422, "a Gmail app password has 16 characters")
        if pw and test:
            import smtplib
            import ssl
            def _login():
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=20) as smtp:
                    smtp.login(email, pw)
            try:
                await run_in_threadpool(_login)
            except smtplib.SMTPAuthenticationError as e:
                raise HTTPException(422, "Gmail rejected this address / app password") from e
            except OSError as e:
                raise HTTPException(502, f"could not reach smtp.gmail.com to test: {type(e).__name__}") from e
        if pw:
            f = core.SECRETS / "gmail_app_password"
            fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(pw)
            os.chmod(f, 0o600)
        p = core.load_profile()
        p["hr_outreach"]["gmail_user"] = email
        if str(p["applicant"].get("email", "")).strip().lower() in resume.PLACEHOLDERS:
            p["applicant"]["email"] = email
        resume.write_profile(p)
        return {"gmail_user": email, "app_password_saved": bool(core.read_secret("gmail_app_password")), "tested": bool(pw and test)}

    @app.put("/api/v1/secrets/{name}", dependencies=auth)
    async def put_secret(name: str, value: str = Body(..., embed=True, min_length=1, max_length=4096)):
        if name not in ALLOWED_SECRETS:
            raise HTTPException(404, f"secret must be one of {sorted(ALLOWED_SECRETS)}")
        f = core.SECRETS / name
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value.strip())
        os.chmod(f, 0o600)
        if name in SECRET_TO_KEY:
            keys.clear(SECRET_TO_KEY[name])  # a replaced key gets a fresh start: lift any cooldown
        return {"saved": name}

    @app.get("/api/v1/secrets", dependencies=auth)
    def list_secrets():
        return {"secrets": {n: (keys.present(SECRET_TO_KEY[n]) if n in SECRET_TO_KEY else bool(core.read_secret(n)))
                            for n in sorted(ALLOWED_SECRETS)},
                "cooldowns": {k: v["cooldown"] for k, v in keys.report().items() if v["cooldown"]}}

    # ------------------------------------------------------------ MCP over HTTP (JSON response mode)
    @app.post("/mcp", dependencies=auth)
    async def mcp(req: Request):
        try:
            msg = await req.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
        if isinstance(msg, list):
            out = [r for r in [await _mcp_one(m) for m in msg] if r is not None]
            return JSONResponse(out) if out else JSONResponse(None, status_code=202)
        r = await _mcp_one(msg)
        return JSONResponse(r) if r is not None else JSONResponse(None, status_code=202)

    return app


async def _mcp_one(m: Any) -> dict | None:
    if not isinstance(m, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
    method, id_ = m.get("method"), m.get("id")
    if id_ is None:  # notification
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": id_, "result": result}

    if method == "initialize":
        pv = (m.get("params") or {}).get("protocolVersion", "2025-06-18")
        return ok({"protocolVersion": pv, "capabilities": {"tools": {}}, "serverInfo": server.SERVER})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": server.tool_list()})
    if method == "tools/call":
        p = m.get("params") or {}
        name, args = p.get("name"), p.get("arguments") or {}
        if name not in server.HANDLERS:
            return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32602, "message": f"unknown tool {name}"}}
        try:
            res = await run_in_threadpool(run_tool, name, args)
        except HTTPException as e:
            return ok({"content": [{"type": "text", "text": str(e.detail)}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(res["result"], indent=2)}], "isError": res["is_error"]})
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": f"method not found: {method}"}}

