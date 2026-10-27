"""With outbound port 25 blocked (AWS), Microsoft 365 mailboxes are still verified over HTTPS."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hub"))

import contacts  # noqa: E402
import verdict_tool  # noqa: E402


def _blocked(monkeypatch, mx):
    monkeypatch.setattr(contacts, "_PORT_25_AVAILABLE", False)
    monkeypatch.setattr(contacts, "mx_hosts", lambda d: [mx])
    monkeypatch.setattr(contacts.socket, "gethostbyname", lambda h: "192.0.2.1")


def test_m365_mailbox_exists(monkeypatch):
    _blocked(monkeypatch, "acme-com.mail.protection.outlook.com")
    monkeypatch.setattr(verdict_tool, "check_m365", lambda e, mx: True)
    assert contacts.smtp_probe("jane.doe@acme.com", "me@example.com") == "valid"


def test_m365_mailbox_missing(monkeypatch):
    _blocked(monkeypatch, "acme-com.mail.protection.outlook.com")
    monkeypatch.setattr(verdict_tool, "check_m365", lambda e, mx: False)
    assert contacts.smtp_probe("nobody@acme.com", "me@example.com") == "invalid"


def test_m365_masked_stays_catch_all(monkeypatch):
    _blocked(monkeypatch, "acme-com.mail.protection.outlook.com")
    monkeypatch.setattr(verdict_tool, "check_m365", lambda e, mx: None)
    assert contacts.smtp_probe("jane@acme.com", "me@example.com") == "catch_all"


def test_other_provider_unchanged(monkeypatch):
    _blocked(monkeypatch, "aspmx.l.google.com")
    assert contacts.smtp_probe("hr@acme.com", "me@example.com") == "valid"
