from datetime import date, timedelta

from app import app
from database.db import query_db
from services.applicability_service import evaluate_requirement_applicability
from services.document_service import _validity_from_fields
from services.notification_service import get_user_notifications, mark_notification_read, notify_user
from services.requirement_service import extract_tender_requirements_from_pages
from services.statutory_service import verify_gst


def test_tender_requirement_extraction_preserves_evidence_and_thresholds():
    pages = [{
        "page_num": 4,
        "text": (
            "The bidder shall have minimum annual turnover of INR 5 crore. "
            "Valid GST registration is mandatory. Minimum 50% local content is required."
        ),
    }]

    requirements = extract_tender_requirements_from_pages(pages, use_ai=False)
    by_code = {item["code"]: item for item in requirements}

    assert by_code["REQ_TURNOVER"]["threshold"] == 50000000
    assert by_code["REQ_MII"]["threshold"] == 50
    assert by_code["REQ_GST"]["threshold"] is None
    assert all(item["source_page"] == 4 for item in requirements)
    assert all(item["source_clause"] for item in requirements)

    duplicate_code_requirements = extract_tender_requirements_from_pages([
        {"page_num": 1, "text": "Average annual turnover minimum INR 10 crore for three years."},
        {"page_num": 2, "text": "Minimum turnover INR 5 crore in the previous financial year."},
    ], use_ai=False)
    turnover_requirements = [item for item in duplicate_code_requirements if item["code"] == "REQ_TURNOVER"]
    assert len(turnover_requirements) == 2


def test_applicability_does_not_turn_optional_preference_into_pass():
    requirement = {
        "code": "REQ_UDYAM",
        "is_mandatory": 0,
        "structured_criteria": "{}",
    }
    bidder = {"company_name": "Example Supplier", "udyam_reg_no": None}
    result = evaluate_requirement_applicability(requirement, bidder, {})
    assert result["status"] == "NOT_APPLICABLE"
    assert "preference" in result["reason"]


def test_document_expiry_states_are_explicit():
    soon = (date.today() + timedelta(days=10)).isoformat()
    expired = (date.today() - timedelta(days=1)).isoformat()
    assert _validity_from_fields({"authorization_valid_till": soon})[0] == "EXPIRING_SOON"
    assert _validity_from_fields({"authorization_valid_till": expired})[0] == "EXPIRED"
    assert _validity_from_fields({})[0] == "NO_EXPIRY_FOUND"


def test_local_notification_provider_tracks_read_state():
    with app.app_context():
        user = query_db("SELECT id FROM users LIMIT 1", one=True)
        assert user
        notification_id = notify_user(
            user["id"], "TEST_EVENT", "Test notification", "Test message", "tests", 1
        )
        unread = get_user_notifications(user["id"], unread_only=True)
        assert any(item["id"] == notification_id for item in unread)
        mark_notification_read(notification_id, user["id"])
        read_row = query_db("SELECT status FROM notifications WHERE id = ?", (notification_id,), one=True)
        assert read_row["status"] == "READ"


def test_explicit_official_adapter_without_configuration_is_unavailable(monkeypatch):
    monkeypatch.setenv("GST_MODE", "OFFICIAL")
    monkeypatch.delenv("GST_API_KEY", raising=False)
    monkeypatch.delenv("GST_API_URL", raising=False)
    result = verify_gst("07AABCU9603R1ZM")
    assert result["source_mode"] == "UNAVAILABLE"
    assert result["is_valid"] is None
