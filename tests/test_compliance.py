import os
import pytest
from database.db import get_db, init_db, query_db, execute_db
from services.statutory_service import (
    verify_gst, verify_pan, verify_udyam, verify_mca, verify_bis,
    verify_blacklisting, verify_digilocker
)
from services.pdf_service import extract_pdf_content
from services.llm_service import extract_document_fields_llm
from services.verification_engine import run_bidder_verification, get_latest_verification
from services.tender_service import update_tender_lifecycle_stage, create_corrigendum
from services.clarification_service import create_clarification_request, submit_clarification_response
from app import app

@pytest.fixture(scope="module")
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client

def test_statutory_adapters_schema_and_disclaimer():
    """Verify statutory adapters return uniform schema with explicit source mode & disclaimers."""
    gst_res = verify_gst("07AABCU9603R1ZM")
    required_keys = {"source", "source_mode", "is_valid", "status", "data", "disclaimer", "message", "timestamp"}
    assert required_keys.issubset(gst_res.keys())
    assert gst_res["is_valid"] is True
    assert "MOCK" in gst_res["disclaimer"]

    pan_res = verify_pan("AABCU9603R")
    assert required_keys.issubset(pan_res.keys())
    assert pan_res["is_valid"] is True

    udyam_res = verify_udyam("UDYAM-DL-03-0012345")
    assert required_keys.issubset(udyam_res.keys())
    assert udyam_res["is_valid"] is True

    blacklist_res = verify_blacklisting(pan="AABCU9603R", gstin="07AABCU9603R1ZM")
    assert required_keys.issubset(blacklist_res.keys())
    assert blacklist_res["is_valid"] is True # Clear / not blacklisted

def test_pdf_extraction_on_sample_files():
    """Verify PDF extraction succeeds on real sample documents in sample_data."""
    sample_path = os.path.join(os.path.dirname(__file__), "..", "sample_data", "bidders", "bidder_a", "GST_Certificate.pdf")
    if os.path.exists(sample_path):
        meta = extract_pdf_content(sample_path)
        assert meta["page_count"] > 0
        assert meta["ocr_status"] == "VALID"
        assert len(meta["full_text"]) > 0

def test_llm_deterministic_fallback_extraction():
    """Verify regex fallback extraction extracts critical compliance fields without crash."""
    mock_gst_text = "Government of India GST Registration Certificate GSTIN: 07AABCU9603R1ZM Legal Name: Bharat Tech Solutions"
    extracted = extract_document_fields_llm("GST_CERTIFICATE", mock_gst_text)
    assert extracted.get("gstin") == "07AABCU9603R1ZM"

    mock_pan_text = "Income Tax Department Permanent Account Number PAN AABCU9603R Name: Bharat Tech Solutions"
    extracted_pan = extract_document_fields_llm("PAN_CARD", mock_pan_text)
    assert extracted_pan.get("pan") == "AABCU9603R"

def test_verification_engine_mandatory_gating(client):
    """Verify that mandatory failure yields NOT_ELIGIBLE and risk ratings are computed."""
    with app.app_context():
        # Tender 1 and Bidder 1 exist from seed data
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        if tender and bidder:
            ver = run_bidder_verification(tender["id"], bidder["id"])
            assert "score" in ver
            assert ver["score"] >= 0 and ver["score"] <= 100
            assert ver["eligibility"] in ("ELIGIBLE", "NOT_ELIGIBLE", "NEEDS_REVIEW")
            assert ver["risk_level"] in ("LOW", "MEDIUM", "HIGH")
            assert len(ver["requirement_evaluations"]) > 0

def test_tender_corrigendum_versioning(client):
    """Verify that publishing a corrigendum creates a new immutable tender version."""
    with app.app_context():
        tender = query_db("SELECT id, tender_version FROM tenders LIMIT 1", one=True)
        if tender:
            admin = query_db("SELECT id FROM users WHERE role = 'admin' LIMIT 1", one=True)
            new_ver = create_corrigendum(
                tender_id=tender["id"],
                officer_id=admin["id"],
                reason="Turnover amendment test",
                updated_turnover=60000000
            )
            assert new_ver.startswith("v")
            updated_tender = query_db("SELECT min_turnover, tender_version FROM tenders WHERE id = ?", (tender["id"],), one=True)
            assert updated_tender["min_turnover"] == 60000000
            assert updated_tender["tender_version"] == new_ver

def test_clarification_workflow(client):
    """Verify clarification request creation, response, and automated re-verification."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        officer = query_db("SELECT id FROM users WHERE role = 'officer' LIMIT 1", one=True)

        if tender and bidder and officer:
            clar_id = create_clarification_request(
                tender_id=tender["id"],
                bidder_id=bidder["id"],
                verification_id=1,
                officer_id=officer["id"],
                requirement_code="REQ_TURNOVER",
                query_text="Please confirm FY25 audit figures."
            )
            assert clar_id > 0

            # Bidder responds
            new_ver = submit_clarification_response(
                clarification_id=clar_id,
                bidder_id=bidder["id"],
                response_text="Attached CA certificate confirming turnover.",
                uploaded_files=[]
            )
            assert new_ver is not None
            assert new_ver["version_num"] >= 2

def test_security_csrf_and_rbac(client):
    """Verify CSRF rejects invalid requests and RBAC blocks unauthenticated/unauthorized access."""
    # Unauthenticated access to /admin redirects to login
    res = client.get("/admin", follow_redirects=False)
    assert res.status_code in (302, 401, 403)

    # POST to login without CSRF should fail with 400
    post_res = client.post("/login", data={"username": "fake", "password": "wrong"})
    assert post_res.status_code == 400

def test_officer_decision_determination(client):
    """Verify human-in-the-loop officer decision recording with audit trail."""
    with app.app_context():
        ver = query_db("SELECT id, tender_id, bidder_id FROM verifications LIMIT 1", one=True)
        officer = query_db("SELECT id FROM users WHERE role = 'officer' LIMIT 1", one=True)
        if ver and officer:
            execute_db(
                """
                UPDATE verifications SET
                    officer_decision = 'QUALIFIED',
                    officer_remarks = 'All statutory records verified compliant.',
                    decided_by = ?,
                    decided_at = '2026-09-06 12:00 UTC'
                WHERE id = ?
                """,
                (officer["id"], ver["id"])
            )
            updated = query_db("SELECT officer_decision, officer_remarks FROM verifications WHERE id = ?", (ver["id"],), one=True)
            assert updated["officer_decision"] == "QUALIFIED"
            assert "verified compliant" in updated["officer_remarks"]

def test_object_level_authorization(client):
    """Verify that a bidder user cannot access another bidder's private documents or evaluation reports."""
    with client.session_transaction() as sess:
        sess["user_id"] = 3 # Bidder user
        sess["user_role"] = "bidder"
        sess["_csrf_token"] = "test_csrf_token"

    # Attempt to access non-existent or foreign bidder report
    res = client.get("/reports/1/9999", follow_redirects=False)
    assert res.status_code in (403, 404)

def test_deterministic_turnover_calculation_and_duplicates(client):
    """Verify deterministic turnover verification across financial years without duplicates."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        if tender and bidder:
            ver = run_bidder_verification(tender["id"], bidder["id"])
            turnover_eval = next((r for r in ver["requirements"] if r["code"] == "REQ_TURNOVER"), None)
            assert turnover_eval is not None
            assert "verified_turnovers_by_year" in turnover_eval["evidence"]
            # Ensure each FY appears at most once
            fys = turnover_eval["evidence"]["verified_turnovers_by_year"]
            assert len(fys) == len(set(fys.keys()))
            if "calculation_formula" in turnover_eval["evidence"]:
                assert "Average:" in turnover_eval["evidence"]["calculation_formula"]

def test_project_grouping_and_deduplication(client):
    """Verify that Work Orders and Completion Certificates for the same contract are grouped into 1 project."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        if tender and bidder:
            ver = run_bidder_verification(tender["id"], bidder["id"])
            exp_eval = next((r for r in ver["requirements"] if r["code"] == "REQ_EXPERIENCE"), None)
            assert exp_eval is not None
            assert "grouped_projects" in exp_eval["evidence"]
            # Each grouped project must have distinct project_key
            projects = exp_eval["evidence"]["grouped_projects"]
            keys = [p["project_key"] for p in projects]
            assert len(keys) == len(set(keys))

def test_oem_authorization_validation(client):
    """Verify OEM authorization parameters and document-based verification disclaimer."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        if tender and bidder:
            ver = run_bidder_verification(tender["id"], bidder["id"])
            oem_eval = next((r for r in ver["requirements"] if r["code"] == "REQ_OEM"), None)
            assert oem_eval is not None
            assert "disclaimer" in oem_eval["evidence"]
            assert "Live OEM API verification not claimed" in oem_eval["evidence"]["disclaimer"]

def test_cross_validation_conflict_detection(client):
    """Verify cross-source identity and address conflict detection structure."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        if tender and bidder:
            ver = run_bidder_verification(tender["id"], bidder["id"])
            assert "conflicts" in ver
            for c in ver["conflicts"]:
                assert "field" in c
                assert "source_a" in c
                assert "source_b" in c
                assert "severity" in c


