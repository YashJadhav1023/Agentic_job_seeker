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
