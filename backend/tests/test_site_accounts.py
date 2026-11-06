"""Per-career-site accounts: one generated password per host, kept in the secrets volume."""
import os
import string
import sys

import pytest

import core
import site_accounts


@pytest.fixture(autouse=True)
def secrets_dir(tmp_path, monkeypatch):
    d = tmp_path / "secrets"
    d.mkdir(mode=0o700)
    monkeypatch.setattr(core, "SECRETS", d)
    return d


def test_create_then_get_round_trip():
    pw = site_accounts.create("careers.acme.com", "me@example.com")
    got = site_accounts.get("careers.acme.com")
    assert got["email"] == "me@example.com" and got["password"] == pw and got["created"]


def test_host_key_ignores_case_and_www():
    pw = site_accounts.create("www.Careers.Acme.com", "me@example.com")
    assert site_accounts.get("careers.acme.com")["password"] == pw
    assert site_accounts.hosts() == ["careers.acme.com"]


def test_generated_password_has_every_character_class():
    for i in range(100):
        pw = site_accounts.create(f"h{i}.example.com", "me@example.com")
        assert len(pw) == 16
        assert any(c in string.ascii_uppercase for c in pw) and any(c in string.ascii_lowercase for c in pw)
        assert any(c in string.digits for c in pw) and any(c in "!@#$%" for c in pw)


def test_simple_password_is_letters_and_digits_only():
    pw = site_accounts.create("strict.example.com", "me@example.com", simple=True)
    assert len(pw) == 14 and pw.isalnum()
    assert any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw)


def test_create_again_replaces_the_password():
    first = site_accounts.create("acme.example.com", "me@example.com")
    second = site_accounts.create("acme.example.com", "me@example.com", simple=True)
    assert first != second and site_accounts.get("acme.example.com")["password"] == second


def test_unknown_host_is_none_and_hosts_lists_no_secrets():
    assert site_accounts.get("nobody.example.com") is None
    site_accounts.create("a.example.com", "me@example.com")
    assert site_accounts.hosts() == ["a.example.com"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_file_is_private(secrets_dir):
    site_accounts.create("a.example.com", "me@example.com")
    assert os.stat(secrets_dir / "site_accounts.json").st_mode & 0o777 == 0o600
