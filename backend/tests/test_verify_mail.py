"""Verification code / link from a career site's sign-up email."""
import time
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone

import core
import verify_mail

PROFILE = {"hr_outreach": {"gmail_user": "me@example.com"}}


def test_code_from_workday_style_mail():
    got = verify_mail.extract("Verify your account", "Hello,\nYour verification code is 482913. It expires in 10 minutes.",
                              "acme.wd5.myworkdayjobs.com", sender="Workday <no-reply@myworkday.com>")
    assert got == {"code": "482913", "link": None}


def test_code_from_an_unrelated_sender_is_ignored():
    got = verify_mail.extract("Your OTP", "Your OTP for the transaction is 552211. Do not share it.", "careers.acme.com",
                              sender="HDFC Bank <alerts@hdfcbank.net>")
    assert got["code"] is None
    got = verify_mail.extract("Verify", "Your verification code is 552211", "careers.acme.com", sender="")
    assert got["code"] is None, "no sender and no company name: not provably this site's code"


def test_code_from_the_site_or_naming_the_company_counts():
    assert verify_mail.extract("Verify", "Your verification code is 7781", "careers.acme.com",
                               sender="no-reply@mail.acme.com")["code"] == "7781"
    assert verify_mail.extract("Welcome to Acme Careers", "Your verification code is 7782", "acme.wd5.myworkdayjobs.com",
                               sender="noreply@sendgrid.example.org")["code"] == "7782"
    assert verify_mail.extract("Verify", "Your verification code is 7783", "acme.wd5.myworkdayjobs.com",
                               sender="noreply@sendgrid.example.org")["code"] is None


def test_link_from_greenhouse_style_mail():
    body = '<p>Confirm your email</p><a href="https://boards.greenhouse.io/verify?token=abc123">Verify</a>'
    got = verify_mail.extract("Confirm your email", body, "boards.greenhouse.io")
    assert got == {"code": None, "link": "https://boards.greenhouse.io/verify?token=abc123"}


def test_numbers_without_a_code_word_are_not_codes():
    got = verify_mail.extract("Your order", "Call us at 9876543210. Order number 55512345 was shipped.", "acme.com")
    assert got["code"] is None


def test_links_to_other_domains_are_ignored():
    got = verify_mail.extract("Verify", '<a href="https://evil.example.net/verify?token=x">Verify</a>', "careers.acme.com")
    assert got["link"] is None


def test_link_on_the_registrable_domain_is_accepted():
    got = verify_mail.extract("Activate", "Activate: https://login.acme.com/activate?t=9", "careers.acme.com")
    assert got["link"] == "https://login.acme.com/activate?t=9"


def _mail(subject, body, sent_at):
    m = EmailMessage()
    m["From"] = "no-reply@myworkday.com"
    m["Subject"] = subject
    m["Date"] = format_datetime(datetime.fromtimestamp(sent_at, timezone.utc))
    m.set_content(body)
    return m.as_bytes()


class FakeImap:
    def __init__(self, inbox, spam=()):
        self.boxes = {"INBOX": list(inbox), "[Gmail]/Spam": list(spam)}
        self.box = None

    def login(self, user, pw):
        self.user = user

    def select(self, box, readonly=False):
        self.box = box
        return "OK", [b"1"]

    def search(self, charset, *criteria):
        n = len(self.boxes[self.box])
        return "OK", [" ".join(str(i + 1) for i in range(n)).encode()]

    def fetch(self, num, what):
        return "OK", [(b"1", self.boxes[self.box][int(num) - 1])]

    def logout(self):
        pass


def test_wait_for_finds_the_new_code(monkeypatch):
    monkeypatch.setattr(core, "read_secret", lambda name: "app pass word")
    since = time.time() - 5
    old = _mail("Verify", "Your verification code is 111111", since - 3600)
    new = _mail("Verify", "Your verification code is 482913", since + 2)
    got = verify_mail.wait_for(PROFILE, "acme.wd5.myworkdayjobs.com", since, imap_factory=lambda: FakeImap([old, new]),
                               sleep=lambda s: None)
    assert got == {"code": "482913", "link": None}


def test_wait_for_looks_in_spam(monkeypatch):
    monkeypatch.setattr(core, "read_secret", lambda name: "pw")
    since = time.time() - 5
    spam = _mail("Verify", "Your one-time code: 7731", since + 1)
    got = verify_mail.wait_for(PROFILE, "acme.wd5.myworkdayjobs.com", since, imap_factory=lambda: FakeImap([], [spam]),
                               sleep=lambda s: None)
    assert got["code"] == "7731"


def test_wait_for_without_gmail_password_returns_at_once(monkeypatch):
    monkeypatch.setattr(core, "read_secret", lambda name: "")
    calls = []
    got = verify_mail.wait_for(PROFILE, "acme.com", time.time(), imap_factory=lambda: calls.append(1), sleep=lambda s: None)
    assert got == {} and calls == []


def test_wait_for_times_out_with_empty_result(monkeypatch):
    monkeypatch.setattr(core, "read_secret", lambda name: "pw")
    clock = iter([0, 0, 50, 100, 200, 400])
    got = verify_mail.wait_for(PROFILE, "acme.com", time.time(), timeout=180, imap_factory=lambda: FakeImap([]),
                               sleep=lambda s: None, now=lambda: next(clock))
    assert got == {}


def test_wait_for_skips_another_senders_otp(monkeypatch):
    monkeypatch.setattr(core, "read_secret", lambda name: "pw")
    since = time.time() - 5
    bank = EmailMessage()
    bank["From"] = "alerts@hdfcbank.net"
    bank["Subject"] = "OTP"
    bank["Date"] = format_datetime(datetime.fromtimestamp(since + 3, timezone.utc))
    bank.set_content("Your OTP is 990011")
    site = _mail("Verify", "Your verification code is 482913", since + 1)
    got = verify_mail.wait_for(PROFILE, "acme.wd5.myworkdayjobs.com", since,
                               imap_factory=lambda: FakeImap([site, bank.as_bytes()]), sleep=lambda s: None)
    assert got == {"code": "482913", "link": None}
