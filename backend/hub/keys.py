"""One place that reads, validates, masks and rate-limits the third-party API keys (Firecrawl, Kiro, OpenAI-style, Telegram).

Lookup order for every key: environment variable (the server's .env, filled from Parameter Store) -> secret file
(`.secrets/<name>`, set from the app). Placeholders such as "UNSET" count as not set.

A key that the provider rejects (401/403) or that is out of credits (402, quota text) is put on a cooldown so a run does
not hammer the API again and again; the owner gets one Telegram alert if that is configured. Key values never go into
logs, run summaries or error text: use `redact()` on anything that may contain provider output.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta

import core

PLACEHOLDERS = {"", "unset", "none", "null", "nil", "changeme", "change-me", "todo", "tbd", "xxx", "your-key", "your_key",
                "<key>", "***"}

# logical name -> (environment variable names in priority order, secret file name)
KNOWN = {
    "firecrawl": (("JOBHUB_FIRECRAWL_API_KEY", "FIRECRAWL_API_KEY"), "firecrawl_api_key"),
    "kiro": (("KIRO_API_KEY",), "kiro_api_key"),
    "llm": (("LLM_API_KEY",), "llm_api_key"),
    "telegram": (("TELEGRAM_BOT_TOKEN",), "telegram_bot_token"),
}

# Shapes of keys that could show up in provider error output.
_KEY_SHAPES = re.compile(r"(ksk_[A-Za-z0-9]{8,}|fc-[A-Za-z0-9]{8,}|sk-[A-Za-z0-9_\-]{12,}|\b\d{6,}:[A-Za-z0-9_\-]{20,}|"
                         r"Bearer\s+[A-Za-z0-9._\-]{12,})")
_AUTH_RX = re.compile(r"\b(401|403)\b|unauthori[sz]ed|forbidden|invalid[ _-]*(api[ _-]*)?(key|token)|expired[ _-]*(api[ _-]*)?(key|token)|"
                      r"authentication (failed|required)|not logged in", re.I)
_CREDIT_RX = re.compile(r"\b402\b|insufficient[ _-]*(credits?|funds|balance)|out of credits|credits? (exhausted|used up)|"
                        r"quota|usage limit|limit exceeded|payment required|billing|subscription (required|expired)|"
                        r"upgrade your plan", re.I)


def _clean(v) -> str:
    v = (v or "").strip().strip('"').strip("'")
    return "" if v.lower() in PLACEHOLDERS else v


def get(name: str) -> str:
    """The key for a logical name ("firecrawl", "kiro", "llm", "telegram"), or "" when it is missing or a placeholder."""
    envs, secret = KNOWN[name]
    for e in envs:
        v = _clean(os.environ.get(e))
        if v:
            return v
    try:
        return _clean(core.read_secret(secret))
    except PermissionError:  # the secret file has loose permissions: refuse it rather than use or print it
        return ""


def present(name: str) -> bool:
    return bool(get(name))


def mask(value: str) -> str:
    return f"{value[:3]}…{value[-2:]}" if len(value) > 8 else "***"


def redact(text) -> str:
    """Text with every configured key value and every key-shaped token replaced by ***."""
    out = str(text or "")
    for n in KNOWN:
        v = get(n)
        if v and len(v) >= 6:
            out = out.replace(v, "***")
    return _KEY_SHAPES.sub("***", out)


def classify(status: int | None = None, text: str = "") -> str:
    """"invalid" | "credits" | "" for an HTTP status and/or provider message."""
    blob = f"{status or ''} {text or ''}"
    if status in (401, 403) or _AUTH_RX.search(blob):
        return "invalid"
    if status == 402 or _CREDIT_RX.search(blob):
        return "credits"
    return ""


# ---------------------------------------------------------------- cooldown after a rejected / exhausted key
def failed(name: str) -> str:
    """Reason while the key is on cooldown, else ""."""
    r = core.kv_get("keyfail:" + name)
    if r and r.get("until", "") > datetime.now().isoformat():
        return r.get("reason", "key unusable")
    return ""


def mark_failed(name: str, kind: str, hours: float = 6.0, profile: dict | None = None) -> None:
    """Remember that the provider refused the key (kind: "invalid" | "credits"); alert the owner once per cooldown."""
    reason = {"invalid": f"{name} API key rejected by the provider (check the key)",
              "credits": f"{name} credits exhausted"}.get(kind, f"{name} unusable")
    first = not failed(name)
    core.kv_set("keyfail:" + name, {"until": (datetime.now() + timedelta(hours=hours)).isoformat(), "reason": reason,
                                    "at": core.now_iso()})
    if first:
        try:
            import notify  # lazy: notify imports core only
            notify.telegram(profile or core.load_profile(), f"Agentic Job Seeker: {reason}. Paused for {hours:g}h.")
        except Exception:  # noqa: BLE001 - an alert must never break a run
            pass


def clear(name: str) -> None:
    core.kv_set("keyfail:" + name, {"until": "", "reason": ""})


def report() -> dict:
    """Which keys are configured and which are on cooldown. Never contains key values."""
    return {n: {"set": present(n), "cooldown": failed(n)} for n in KNOWN}
