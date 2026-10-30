"""Optional push alerts: Telegram (secret `telegram_bot_token` + profile notifications.telegram_chat_id) and ntfy
(profile notifications.ntfy_topic; open-source push app for Android/iOS).

Never raises: a failed alert must not break a scan. Returns True when the message was accepted.
"""
from __future__ import annotations

import json
import urllib.request

import core
import keys


def telegram_ready(profile: dict) -> bool:
    n = profile.get("notifications") or {}
    return bool(n.get("telegram_chat_id") and _token())


def _token() -> str:
    return keys.get("telegram")


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


def ntfy(profile: dict, title: str, text: str, click: str = "") -> bool:
    """Phone push through ntfy (open source, https://ntfy.sh or a self-hosted server). Subscribe to the same topic in the
    ntfy app. The topic name is the only secret, so it must be long and random."""
    n = profile.get("notifications") or {}
    topic = str(n.get("ntfy_topic") or "").strip()
    if len(topic) < 12 or not topic.replace("-", "").replace("_", "").isalnum():
        return False
    server = str(n.get("ntfy_server") or "https://ntfy.sh").rstrip("/")
    if not server.startswith("https://"):
        return False
    headers = {"Title": title.encode("ascii", "ignore").decode()[:200], "Priority": "high", "Tags": "raising_hand"}
    if click:
        headers["Click"] = click
    req = urllib.request.Request(f"{server}/{topic}", data=text[:3000].encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 - https only, server from the profile
            return getattr(r, "status", 200) < 300
    except Exception:  # noqa: BLE001
        return False


def push(profile: dict, title: str, text: str, url: str = "") -> bool:
    """Every configured channel (ntfy, Telegram). True when at least one accepted the message."""
    a = ntfy(profile, title, text, url)
    b = telegram(profile, f"{title}\n{text}" + (f"\n{url}" if url else ""))
    return a or b


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
