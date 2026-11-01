import json
import urllib.error
import urllib.request
import contacts
import keys
import pytest


def test_hunter_check_valid(monkeypatch):
    monkeypatch.setattr(keys, "failed", lambda n: "")
    monkeypatch.setattr(keys, "get", lambda n: "test_hunter_key" if n == "hunter" else "")

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"data": {"status": "valid"}}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8: MockResponse())
    assert contacts.hunter_check("recruiter@acme.com") == "valid"


def test_hunter_check_quota_exhausted(monkeypatch):
    monkeypatch.setattr(keys, "failed", lambda n: "")
    monkeypatch.setattr(keys, "get", lambda n: "test_hunter_key" if n == "hunter" else "")

    failed_keys = []
    monkeypatch.setattr(keys, "mark_failed", lambda n, k, hours=12.0: failed_keys.append((n, k)))

    def mock_urlopen(req, timeout=8):
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    assert contacts.hunter_check("recruiter@acme.com") is None
    assert ("hunter", "credits") in failed_keys


def test_zerobounce_check_valid(monkeypatch):
    monkeypatch.setattr(keys, "failed", lambda n: "")
    monkeypatch.setattr(keys, "get", lambda n: "test_zb_key" if n == "zerobounce" else "")

    class MockResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def read(self):
            return json.dumps({"status": "valid"}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=8: MockResponse())
    assert contacts.zerobounce_check("recruiter@acme.com") == "valid"


def test_verify_email_chains_hunter_to_zerobounce(monkeypatch):
    # Hunter fails / returns None
    monkeypatch.setattr(contacts, "hunter_check", lambda e: None)
    # ZeroBounce succeeds
    monkeypatch.setattr(contacts, "zerobounce_check", lambda e: "valid")

    res = contacts.verify_email("candidate_test@example.org", use_cache=False)
    assert res == "valid"


def test_verify_email_falls_back_when_both_apis_fail(monkeypatch):
    # Both APIs return None
    monkeypatch.setattr(contacts, "hunter_check", lambda e: None)
    monkeypatch.setattr(contacts, "zerobounce_check", lambda e: None)
    monkeypatch.setattr(contacts, "reacher_check", lambda e: None)
    monkeypatch.setattr(contacts, "smtp_probe", lambda e, f="": "valid")

    res = contacts.verify_email("fallback_test@example.org", use_cache=False)
    assert res == "valid"
