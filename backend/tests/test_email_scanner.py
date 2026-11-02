import pytest
from unittest.mock import MagicMock
import email_scanner as es

def test_classify_content():
    assert es.classify_content("Interview Invitation: DevOps Engineer", "Let's connect on Zoom") == "interview_invite"
    assert es.classify_content("Discussion on CTC and Notice Period", "Please share current CTC, expected CTC, and notice period") == "ctc_inquiry"
    assert es.classify_content("Update on your candidature", "Unfortunately we have decided to pursue other candidates") == "rejection"
    assert es.classify_content("Delivery Status Notification (Failure)", "Undeliverable mail") == "bounce"


def test_build_ai_reply_draft_ctc():
    profile = {
        "applicant": {"name": "Yash Patel", "phone": "+919876543210", "location": {"city": "Pune"}},
        "compensation": {"current_ctc_inr": 450000, "expected_ctc_inr": 650000, "notice_period_days": 15},
    }
    draft = es.build_ai_reply_draft(profile, "Priya Sharma", "Salary discussion", "Please send your CTC details", "ctc_inquiry")
    assert "Yash Patel" in draft
    assert "4.5 LPA" in draft
    assert "6.5 LPA" in draft
    assert "15 days" in draft
    assert "Pune" in draft


def test_build_ai_reply_draft_interview():
    profile = {
        "applicant": {"name": "Yash Patel", "phone": "+919876543210", "location": {"city": "Pune"}},
        "compensation": {"notice_period_days": 30},
    }
    draft = es.build_ai_reply_draft(profile, "David HR", "Interview Schedule", "Are you available tomorrow?", "interview_invite")
    assert "Yash Patel" in draft
    assert "available" in draft.lower()
    assert "+919876543210" in draft


def test_extract_bounced_address():
    known = {"dead.contact@telemergeitservices.com", "valid@company.com"}
    body1 = "Delivery to the following recipient failed permanently:\n\n    dead.contact@telemergeitservices.com\n\nTechnical details..."
    assert es.extract_bounced_address(body1, known) == "dead.contact@telemergeitservices.com"

    body2 = "Final-Recipient: rfc822; bounced.user@corp.com\nAction: failed"
    assert es.extract_bounced_address(body2) == "bounced.user@corp.com"

    body3 = "Your message to candidate@domain.com couldn't be delivered."
    assert es.extract_bounced_address(body3) == "candidate@domain.com"


def test_scheduler_inbox_scan_due(monkeypatch):
    import scheduler
    import core
    from datetime import datetime
    monkeypatch.setattr(core, "load_profile", lambda: {"inbox_scan": {"schedule_every_hours": 0.5}})
    monkeypatch.setattr(es, "scan_inbox", lambda p: {"scanned": 10, "replies_found": 1})
    now = datetime(2026, 11, 2, 10, 0)
    out = scheduler.tick(now)
    assert "inbox_scan" in out
    assert out["inbox_scan"]["replies_found"] == 1


