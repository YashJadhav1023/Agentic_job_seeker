"""Optional push alerts. Telegram only for now: secret `telegram_bot_token` + profile notifications.telegram_chat_id.

Never raises: a failed alert must not break a scan. Returns True when the message was accepted.
"""
from __future__ import annotations

import json
import os
import urllib.request

import core


def telegram_ready(profile: dict) -> bool:
    n = profile.get("notifications") or {}
    return bool(n.get("telegram_chat_id") and _token())


def _token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or core.read_secret("telegram_bot_token") or ""


def telegram(profile: dict, text: str) -> bool:
    n = profile.get("notifications") or {}
    if not telegram_ready(profile):
        return False
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{_token()}/sendMessage",
        data=json.dumps({"chat_id": n["telegram_chat_id"], "text": text[:4000], "disable_web_page_preview": True}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - fixed Telegram API host
            return json.loads(r.read().decode()).get("ok", False)
    except Exception:  # noqa: BLE001
        return False


def post_alert(profile: dict, v: dict, author: str, excerpt: str, emails: list, links: list) -> bool:
    lines = ["New matching hiring post",
             f"Role: {v.get('role') or '?'}", f"Company: {v.get('company') or '?'}", f"Location: {v.get('location') or '?'}",
             f"Posted by: {author or '?'}"]
    if emails:
        lines.append("Email: " + ", ".join(emails[:3]))
    if links:
        lines.append("Link: " + links[0])
    lines += ["", excerpt[:600]]
    return telegram(profile, "\n".join(lines))
