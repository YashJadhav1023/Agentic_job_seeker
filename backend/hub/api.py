"""Agentic Job Seeker - HTTPS API over the job-hub tools, for the Android app and remote MCP clients.

Every tool in server.TOOLS is exposed automatically, so the app, the stdio MCP server and this API never drift:

  GET  /health                       liveness (no auth)
  GET  /api/v1/tools                 tool catalogue with JSON schemas (the app renders forms from these)
  POST /api/v1/tools/{name}          run a tool; body = tool arguments
  GET  /api/v1/runs                  recent background runs
  GET  /api/v1/runs/{run_id}         run status + log tail
  GET/PUT /api/v1/profile            read / replace profile.yaml (validated)
  POST /api/v1/resume                upload the resume PDF used for scoring and email attachments
  PUT  /api/v1/secrets/{name}        write-only secret store (e.g. gmail_app_password); values are never returned
  POST /mcp                          MCP Streamable-HTTP (JSON response mode) so Kiro/Claude can use the cloud hub

Auth: `Authorization: Bearer <JOBHUB_API_TOKEN>` on everything except /health. The server refuses to start without a
token of at least 32 characters. Repeated bad tokens from one IP are locked out for 15 minutes.
"""
from __future__ import annotations

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
from fastapi.responses import JSONResponse  # noqa: E402

import core  # noqa: E402
import server  # noqa: E402

API_VERSION = "1.0.0"
MIN_TOKEN_LEN = 32
MAX_RESUME_BYTES = 5 * 1024 * 1024
MAX_PROFILE_BYTES = 64 * 1024
ALLOWED_SECRETS = {"gmail_app_password", "reacher.env"}
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
    app = FastAPI(title="Agentic Job Seeker API", version=API_VERSION,
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
        with core.db() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM runs ORDER BY started DESC LIMIT ?", (limit,))]
        return {"runs": [{k: v for k, v in core.run_status(i, 3).items()} for i in ids]}

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

    @app.post("/api/v1/resume", dependencies=auth)
    async def upload_resume(file: UploadFile = File(...)):
        data = await file.read(MAX_RESUME_BYTES + 1)
        if len(data) > MAX_RESUME_BYTES:
            raise HTTPException(413, "resume must be <= 5 MB")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(422, "resume must be a PDF")
        dest = Path(core.load_profile()["applicant"]["resume_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".upload")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return {"saved": str(dest), "bytes": len(data)}

    @app.put("/api/v1/secrets/{name}", dependencies=auth)
    async def put_secret(name: str, value: str = Body(..., embed=True, min_length=1, max_length=4096)):
        if name not in ALLOWED_SECRETS:
            raise HTTPException(404, f"secret must be one of {sorted(ALLOWED_SECRETS)}")
        f = core.SECRETS / name
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value.strip())
        os.chmod(f, 0o600)
        return {"saved": name}

    @app.get("/api/v1/secrets", dependencies=auth)
    def list_secrets():
        return {"secrets": {n: (core.SECRETS / n).exists() for n in sorted(ALLOWED_SECRETS)}}

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

