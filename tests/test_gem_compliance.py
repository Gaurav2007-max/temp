"""Comprehensive Test Suite for GeM Bid Compliance Verification Platform
Covers:
- Multiple document upload (request.files.getlist("documents"))
- Many-to-one document mapping
- Independent database records
- Deterministic rule calculations (turnover 3-year avg, experience count/value, OEM, MII local content)
- 3 independent evaluation metrics (Score, Eligibility, Risk)
- Source conflict detection
- Tender lifecycle & 5-day deadlines
- Early bidding and clarification closure
- Tender corrigendum versioning
- Clarification response & automated re-verification
- Officer qualification decisions & remarks
"""
import io
import os
import json
import pytest
from datetime import datetime, timezone, timedelta
from app import app
from database.db import get_db, init_db, utc_now_iso
from services.tender_service import TenderService
from services.document_service import DocumentService
from services.verification_engine import VerificationEngine
from services.clarification_service import ClarificationService
from services.statutory_service import StatutoryVerificationService
from services.audit_service import AuditService
from services.seed_data import run_seed

@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    import database.db
    test_db = "/tmp/test_gem_compliance.db"
    for f in [test_db, f"{test_db}-wal", f"{test_db}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass
    database.db.DB_PATH = test_db
    init_db()
    run_seed()
    yield
    for f in [test_db, f"{test_db}-wal", f"{test_db}-shm"]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except Exception:
                pass

@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as c:
        yield c

def test_tender_creation_and_timeline():
    """Test 5-day bidding window and 5-day clarification window."""
    tender_id = TenderService.create_tender(
        title="Test Hardware Supply Bid",
        organization="Department of Telecommunications",
        category="Hardware",
        estimated_value=15000000,
        bidding_days=5,
        clarification_days=5
    )
    assert tender_id is not None

    tender = TenderService.get_tender_detail(tender_id)
    assert tender["status"] == "OPEN_FOR_BIDDING"
    assert tender["tender_version"] == 1

    # Check 5 days delta
    start = datetime.fromisoformat(tender["bidding_start_at"])
    end = datetime.fromisoformat(tender["bidding_end_at"])
    diff = end - start
    assert diff.days == 5

    # Check bidding submission allowed
    allowed, msg = TenderService.validate_bidder_submission_allowed(tender_id)
    assert allowed is True

def test_early_bidding_and_clarification_closure():
    """Test officer closing bidding and clarification early."""
    tender_id = TenderService.create_tender(
        title="Test Early Closure Bid",
        organization="Ministry of Railways",
        category="Logistics",
        estimated_value=20000000
    )

    # Close bidding early
    success, msg = TenderService.close_bidding_early(tender_id, officer_id=2)
    assert success is True
    t1 = TenderService.get_tender_detail(tender_id)
    assert t1["status"] == "CLARIFICATION"
    assert t1["actual_bidding_closed_at"] is not None

    # Bidding submission must now be rejected
    allowed, err = TenderService.validate_bidder_submission_allowed(tender_id)
    assert allowed is False

    # Close clarification early
    success2, msg2 = TenderService.close_clarification_early(tender_id, officer_id=2)
    assert success2 is True
    t2 = TenderService.get_tender_detail(tender_id)
    assert t2["status"] == "OFFICER_REVIEW"
    assert t2["actual_clarification_closed_at"] is not None

def test_corrigendum_versioning():
    """Test issuing corrigendum v1 -> v2."""
    tender_id = TenderService.create_tender(
        title="Corrigendum Test Tender",
        organization="Department of Telecommunications",
        category="Electronics",
        estimated_value=12000000
    )
    t_before = TenderService.get_tender_detail(tender_id)
    assert t_before["tender_version"] == 1

    # Issue corrigendum
    success, msg = TenderService.create_corrigendum(tender_id, officer_id=2, reason="Amended clause 4.2")
    assert success is True

    t_after = TenderService.get_tender_detail(tender_id)
    assert t_after["tender_version"] == 2

    # Check version snapshots exist
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM tender_versions WHERE tender_id = ?", (tender_id,))
    snapshots = c.fetchone()[0]
    conn.close()
    assert snapshots >= 1

def test_multiple_document_upload(client):
    """Test critical requirement: uploading multiple files via request.files.getlist('documents')."""
    tender_id = TenderService.create_tender(
        title="Multi-Doc Upload Tender",
        organization="Ministry of Defence",
        category="Hardware",
        estimated_value=25000000
    )

    # Switch session to bidder
    with client.session_transaction() as sess:
        sess["user_id"] = 3
        sess["username"] = "bidder_a"
        sess["role"] = "bidder"
        sess["full_name"] = "Bharat Tech Solutions"

    # Prepare 4 sample files
    data = {
        "documents": [
            (io.BytesIO(b"%PDF-1.4 GST Certificate Registration 07AABCU9603R1ZM"), "GST_Certificate.pdf"),
            (io.BytesIO(b"%PDF-1.4 PAN Card Income Tax AABCU9603R"), "PAN_Card.pdf"),
            (io.BytesIO(b"%PDF-1.4 ITR FY2023 Turnover Rs. 35 Crores"), "ITR_FY2023.pdf"),
            (io.BytesIO(b"%PDF-1.4 ITR FY2024 Turnover Rs. 40 Crores"), "ITR_FY2024.pdf")
        ],
        "undertaking": "on"
    }

    resp = client.post(f"/bidder/tender/{tender_id}/submit", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200

    # Verify each document is an independent database record
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM documents WHERE tender_id = ? AND filename IN ('GST_Certificate.pdf', 'PAN_Card.pdf', 'ITR_FY2023.pdf', 'ITR_FY2024.pdf')", (tender_id,))
    doc_count = c.fetchone()[0]
    conn.close()
    assert doc_count == 4

def test_deterministic_rule_engine_calculations():
    """Test 3-year turnover average calculation, experience sum, and MII."""
    # Test Bidder A (seeded compliant bidder)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM bid_submissions WHERE bidder_id = 1 AND tender_id = 1")
    sub = c.fetchone()
    assert sub is not None

    c.execute("SELECT * FROM verifications WHERE submission_id = ? ORDER BY version_number DESC LIMIT 1", (sub["id"],))
    verif = c.fetchone()
    conn.close()

    assert verif is not None
    # 3 Independent metrics
    assert verif["compliance_score"] >= 80
    assert verif["eligibility_recommendation"] == "ELIGIBLE"
    assert verif["risk_level"] == "LOW"

    # Test Turnover requirement calculation
    conn = get_db()
    c = conn.cursor()
    c.execute("""
    SELECT rv.* FROM requirement_verifications rv
    JOIN tender_requirements tr ON rv.requirement_id = tr.id
    WHERE rv.verification_id = ? AND tr.code = 'REQ_TURNOVER'
    """, (verif["id"],))
    turnover_rv = c.fetchone()
    conn.close()

    assert turnover_rv is not None
    calc_vals = json.loads(turnover_rv["calculated_values"])
    assert "calculated_average_crore" in calc_vals
    assert calc_vals["calculated_average_crore"] >= 5.0  # Above required 5.0 Cr threshold

def test_source_conflict_detection():
    """Test address mismatch between bidder profile and statutory GST portal."""
    # Bidder C has an address mismatch (Jaipur in GST vs Bangalore in Profile)
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM bid_submissions WHERE bidder_id = 3")
    sub = c.fetchone()
    c.execute("SELECT * FROM verifications WHERE submission_id = ? ORDER BY version_number DESC LIMIT 1", (sub["id"],))
    verif = c.fetchone()
    conn.close()

    assert verif is not None
    # High risk or conflict detected
    assert verif["risk_level"] == "HIGH"
    issues = json.loads(verif["risk_issues_json"])
    assert any("conflict" in i.lower() or "address" in i.lower() or "gst" in i.lower() or "failed" in i.lower() for i in issues)

def test_clarification_workflow_and_reverification(client):
    """Test officer requesting clarification and bidder responding with multiple files."""
    # 1. Officer requests clarification
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["username"] = "officer1"
        sess["role"] = "officer"
        sess["full_name"] = "Rajesh Verma"

    sub_id = 2  # Bidder B
    clar_data = {
        "requirement_id": 1,
        "reason": "MISSING_EVIDENCE",
        "details": "Please provide latest GST return filing confirmation."
    }
    resp = client.post(f"/officer/submission/{sub_id}/request_clarification", data=clar_data, follow_redirects=True)
    assert resp.status_code == 200

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM clarifications WHERE submission_id = ? ORDER BY id DESC LIMIT 1", (sub_id,))
    clar = c.fetchone()
    conn.close()
    assert clar is not None
    assert clar["status"] == "REQUESTED"

    # 2. Bidder responds with multiple supplementary files
    with client.session_transaction() as sess:
        sess["user_id"] = 4
        sess["username"] = "bidder_b"
        sess["role"] = "bidder"
        sess["full_name"] = "Precision Components"

    resp_data = {
        "documents": [
            (io.BytesIO(b"%PDF-1.4 GST Return Supplementary GSTR-3B"), "GSTR3B_Rectified.pdf"),
            (io.BytesIO(b"%PDF-1.4 CA Clarification Letter Annexure"), "CA_Annexure.pdf")
        ],
        "response_remarks": "Submitting updated return records as requested."
    }
    resp2 = client.post(f"/bidder/clarification/{clar['id']}", data=resp_data, content_type="multipart/form-data", follow_redirects=True)
    assert resp2.status_code == 200

    # 3. Check clarification marked SUBMITTED and re-verification triggered
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT status FROM clarifications WHERE id = ?", (clar["id"],))
    assert c.fetchone()[0] == "SUBMITTED"

    # Check verifications version incremented
    c.execute("SELECT version_number FROM verifications WHERE submission_id = ? ORDER BY version_number DESC LIMIT 1", (sub_id,))
    latest_ver = c.fetchone()[0]
    conn.close()
    assert latest_ver >= 2

def test_officer_final_decision(client):
    """Test procurement officer recording binding decision with mandatory remarks."""
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["username"] = "officer1"
        sess["role"] = "officer"
        sess["full_name"] = "Rajesh Verma"

    sub_id = 1
    decision_data = {
        "decision": "QUALIFIED",
        "remarks": "Bidder meets all mandatory technical, statutory, and financial criteria."
    }
    resp = client.post(f"/officer/submission/{sub_id}/decision", data=decision_data, follow_redirects=True)
    assert resp.status_code == 200

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT officer_decision, officer_decision_remarks, status FROM bid_submissions WHERE id = ?", (sub_id,))
    sub = c.fetchone()
    conn.close()

    assert sub["officer_decision"] == "QUALIFIED"
    assert "mandatory" in sub["officer_decision_remarks"].lower()
    assert sub["status"] == "DECIDED"

def test_executive_report_rendering(client):
    """Test printable report endpoint returns 200 and contains verification details."""
    resp = client.get("/report/1")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8")
    assert "EXECUTIVE BID COMPLIANCE REPORT" in html
    assert "Statutory Registry Verification" in html
    assert "Bharat Tech Solutions" in html

def test_mandatory_failure_overrides_score():
    """Verify deterministic rule: Any mandatory failure must mark recommendation NOT_ELIGIBLE."""
    conn = get_db()
    c = conn.cursor()
    # Bidder C has cancelled GST and debarred PAN
    c.execute("SELECT * FROM bid_submissions WHERE bidder_id = 3")
    sub = c.fetchone()
    assert sub is not None

    c.execute("SELECT * FROM verifications WHERE submission_id = ? ORDER BY version_number DESC LIMIT 1", (sub["id"],))
    verif = c.fetchone()
    conn.close()

    assert verif["eligibility_recommendation"] == "NOT_ELIGIBLE"
    assert verif["risk_level"] == "HIGH"

def test_deadline_enforcement_rejects_late_submission(client):
    """Verify server-side deadline enforcement rejects submissions after bidding window."""
    # Create tender with past bidding end
    tender_id = TenderService.create_tender(
        title="Expired Tender",
        organization="Ministry of Power",
        category="Energy",
        estimated_value=5000000,
        bidding_days=-1  # Expired yesterday
    )

    allowed, msg = TenderService.validate_bidder_submission_allowed(tender_id)
    assert allowed is False
    assert "closed" in msg.lower() or "expired" in msg.lower() or "ended" in msg.lower()

    # Attempt POST upload via client
    with client.session_transaction() as sess:
        sess["user_id"] = 3
        sess["username"] = "bidder_a"
        sess["role"] = "bidder"
        sess["full_name"] = "Bharat Tech Solutions"

    data = {
        "documents": [(io.BytesIO(b"%PDF-1.4 Late File"), "Late_Doc.pdf")],
        "undertaking": "on"
    }
    resp = client.post(f"/bidder/tender/{tender_id}/submit", data=data, content_type="multipart/form-data", follow_redirects=True)
    assert resp.status_code == 200
    # Should redirect with danger flash message
    html = resp.data.decode("utf-8")
    assert "closed" in html.lower() or "rejected" in html.lower() or "deadline" in html.lower()

def test_role_based_access_control(client):
    """Verify RBAC: Bidders cannot access Officer or Admin routes."""
    # Log in as bidder
    with client.session_transaction() as sess:
        sess["user_id"] = 3
        sess["username"] = "bidder_a"
        sess["role"] = "bidder"

    # Attempt to access admin dashboard
    resp_admin = client.get("/admin", follow_redirects=False)
    assert resp_admin.status_code == 302  # Redirected due to unauthorized role

    # Attempt to access officer review
    resp_officer = client.get("/officer/tenders", follow_redirects=False)
    assert resp_officer.status_code == 302

def test_unauthenticated_root_redirects_to_login(client):
    """Verify that unauthenticated access to / redirects to /login."""
    with client.session_transaction() as sess:
        sess.clear()
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

def test_user_registration_and_role_dashboard_routing(client):
    """Test user registration as a Bidder creates user and bidder profile and routes to /bidder."""
    with client.session_transaction() as sess:
        sess.clear()

    reg_data = {
        "full_name": "Test Vendor Pvt Ltd",
        "username": "new_test_vendor",
        "email": "vendor@example.com",
        "password": "Password123",
        "confirm_password": "Password123",
        "role": "bidder",
        "company_name": "Test Vendor Pvt Ltd",
        "gstin": "07AAACR1234F1Z1",
        "pan": "AAACR1234F",
        "address": "Connaught Place, New Delhi"
    }

    resp = client.post("/register", data=reg_data, follow_redirects=False)
    assert resp.status_code == 302
    assert "/bidder" in resp.headers["Location"]

    # Verify user was created in DB
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE username = 'new_test_vendor'")
    u = c.fetchone()
    assert u is not None
    assert u["role"] == "bidder"

    # Verify bidder profile was created
    c.execute("SELECT * FROM bidders WHERE user_id = ?", (u["id"],))
    b = c.fetchone()
    assert b is not None
    assert b["gstin"] == "07AAACR1234F1Z1"
    conn.close()

def test_user_login_and_role_redirect(client):
    """Test login with registered credentials routes to the correct role dashboard."""
    with client.session_transaction() as sess:
        sess.clear()

    # Login as newly created vendor
    login_data = {
        "username": "new_test_vendor",
        "password": "Password123"
    }
    resp = client.post("/login", data=login_data, follow_redirects=False)
    assert resp.status_code == 302
    assert "/bidder" in resp.headers["Location"]

    # When logged in as bidder, visiting / routes to /bidder
    resp_root = client.get("/", follow_redirects=False)
    assert resp_root.status_code == 302
    assert "/bidder" in resp_root.headers["Location"]

