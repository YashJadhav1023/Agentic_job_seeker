"""Moli headless CDP client and session bridge.

Moli (https://github.com/lexmount/moli) is a lightweight Rust-based headless browser (~40MB RAM)
that serves CDP on port 9222.

This client:
  1. Checks if Moli is running on 127.0.0.1:9222.
  2. Bridges persistent Chromium sessions (.profiles/<platform>) into clean Playwright storage_state.json
     files so existing logins (Naukri, LinkedIn, Indeed) work seamlessly in Moli without re-logging in.
  3. Provides sync and async context creators over CDP with automatic fallback to Chromium if Moli is offline.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MOLI_HOST = os.environ.get("JOBHUB_MOLI_HOST", "127.0.0.1")
MOLI_PORT = int(os.environ.get("JOBHUB_MOLI_PORT", "9222"))
MOLI_URL = f"http://{MOLI_HOST}:{MOLI_PORT}"


def is_moli_available() -> bool:
    """Return True if Moli CDP endpoint is listening."""
    if os.environ.get("JOBHUB_DISABLE_MOLI") == "1":
        return False
    try:
        with socket.create_connection((MOLI_HOST, MOLI_PORT), timeout=0.8):
            return True
    except (OSError, socket.timeout):
        return False


def get_storage_state_path(platform: str) -> Path | None:
    """Return path to storage_state.json for platform, generating it from .profiles/<platform> if needed."""
    profile_dir = ROOT / ".profiles" / platform
    state_file = ROOT / ".profiles" / f"{platform}_state.json"

    if state_file.exists() and state_file.stat().st_size > 10:
        return state_file

    if not profile_dir.is_dir():
        return None

    # Try extracting storage_state from the Chromium profile
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            ctx.storage_state(path=str(state_file))
            ctx.close()
        if state_file.exists() and state_file.stat().st_size > 10:
            return state_file
    except Exception as e:
        print(f"[moli_client] Note: could not export storage_state for {platform}: {e}", file=sys.stderr)

    return None


def sync_moli_context(p, platform: str | None = None, **kwargs):
    """Create a Playwright sync context connected to Moli over CDP, or None if Moli unavailable."""
    if not is_moli_available():
        return None

    try:
        browser = p.chromium.connect_over_cdp(MOLI_URL)
        ctx_opts = {
            "viewport": kwargs.get("viewport", {"width": 1280, "height": 800}),
            "locale": kwargs.get("locale", "en-IN"),
        }
        if "user_agent" in kwargs:
            ctx_opts["user_agent"] = kwargs["user_agent"]

        if platform:
            st = get_storage_state_path(platform)
            if st:
                ctx_opts["storage_state"] = str(st)

        ctx = browser.new_context(**ctx_opts)
        ctx.set_default_timeout(kwargs.get("timeout", 15000))
        return ctx
    except Exception as e:
        print(f"[moli_client] CDP connection failed: {e}; falling back to Chromium", file=sys.stderr)
        return None


async def async_moli_context(pw, platform: str | None = None, **kwargs):
    """Create a Playwright async context connected to Moli over CDP, or None if Moli unavailable."""
    if not is_moli_available():
        return None

    try:
        browser = await pw.chromium.connect_over_cdp(MOLI_URL)
        ctx_opts = {
            "viewport": kwargs.get("viewport", {"width": 1280, "height": 800}),
            "locale": kwargs.get("locale", "en-IN"),
        }
        if "user_agent" in kwargs:
            ctx_opts["user_agent"] = kwargs["user_agent"]

        if platform:
            st = get_storage_state_path(platform)
            if st:
                ctx_opts["storage_state"] = str(st)

        ctx = await browser.new_context(**ctx_opts)
        ctx.set_default_timeout(kwargs.get("timeout", 15000))
        return ctx
    except Exception as e:
        print(f"[moli_client] Async CDP connection failed: {e}; falling back to Chromium", file=sys.stderr)
        return None

