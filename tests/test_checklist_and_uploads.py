import io
import os
import json
import pytest
from database.db import get_db, init_db, query_db, execute_db
from services.document_service import (
    save_requirement_document,
    get_bidder_document_checklist,
    validate_uploaded_document_file,
    detect_document_type,
    validate_doc_type_for_requirement
)
from services.seed_data import FileStorageMock
from app import app

@pytest.fixture(scope="module")
def client():
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client

def test_get_bidder_document_checklist_structure(client):
    """Verify document checklist aggregation matches requirements and includes all critical metadata."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        assert tender is not None
        assert bidder is not None

        checklist = get_bidder_document_checklist(tender["id"], bidder["id"])
        assert "tender" in checklist
        assert "requirements" in checklist
        assert "summary" in checklist

        summary = checklist["summary"]
        assert "total_requirements" in summary
        assert "uploaded_count" in summary
        assert "missing_mandatory" in summary
        assert "progress_percentage" in summary

        for req in checklist["requirements"]:
            assert "requirement_id" in req
            assert "code" in req
            assert "title" in req
            assert "is_mandatory" in req
            assert "uploaded" in req
            assert "missing" in req
            assert "recommended_filenames" in req
            assert isinstance(req["recommended_filenames"], list)
            assert "expected_doc_types" in req

def test_save_requirement_document_and_versioning(client):
    """Verify individual requirement upload, version increment (v1 -> v2), and is_current flag behavior."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        bidder = query_db("SELECT id FROM bidders LIMIT 1", one=True)
        execute_db(
            "UPDATE tenders SET lifecycle_stage = 'OPEN_FOR_BIDDING', bid_end_date = '2030-12-31T23:59:59' WHERE id = ?",
            (tender["id"],)
        )
        pan_req = query_db("SELECT id FROM requirements WHERE tender_id = ? AND code = 'REQ_PAN'", (tender["id"],), one=True)
        assert pan_req is not None

        execute_db(
            "DELETE FROM documents WHERE bidder_id = ? AND tender_id = ? AND requirement_id = ?",
            (bidder["id"], tender["id"], pan_req["id"])
        )

        sample_pan_path = os.path.join(os.path.dirname(__file__), "..", "sample_data", "bidders", "bidder_a", "PAN_Card.pdf")
        if not os.path.exists(sample_pan_path):
            pytest.skip("Sample PAN file not present")

        file_v1 = FileStorageMock(sample_pan_path, "PAN_Card_v1.pdf")

        # 1. Upload Version 1
        success, doc1 = save_requirement_document(
            bidder_id=bidder["id"],
            tender_id=tender["id"],
            requirement_id=pan_req["id"],
            file_obj=file_v1
        )
        assert success is True
        assert doc1["version"] == 1
        assert doc1["requirement_id"] == pan_req["id"]

        # Check DB state for v1
        row_v1 = query_db("SELECT id, version, is_current FROM documents WHERE id = ?", (doc1["id"],), one=True)
        assert row_v1["is_current"] == 1

        # 2. Upload Version 2 replacing v1
        file_v2 = FileStorageMock(sample_pan_path, "PAN_Card_v2.pdf")
        success2, doc2 = save_requirement_document(
            bidder_id=bidder["id"],
            tender_id=tender["id"],
            requirement_id=pan_req["id"],
            file_obj=file_v2,
            replace_document_id=doc1["id"]
        )
        assert success2 is True
        assert doc2["version"] == 2
        assert doc2["replaced_document_id"] == doc1["id"]

        # Check DB: v1 must now have is_current = 0, v2 must have is_current = 1
        row_v1_after = query_db("SELECT id, is_current FROM documents WHERE id = ?", (doc1["id"],), one=True)
        row_v2_after = query_db("SELECT id, is_current, version FROM documents WHERE id = ?", (doc2["id"],), one=True)
        assert row_v1_after["is_current"] == 0
        assert row_v2_after["is_current"] == 1
        assert row_v2_after["version"] == 2

def test_wrong_document_type_flagging():
    """Verify that uploading an unrelated document triggers WRONG_DOCUMENT_TYPE and is not silently accepted."""
    # REQ_GST expects GST_CERTIFICATE or GSTR_RETURN
    valid, warning = validate_doc_type_for_requirement("REQ_GST", "PAN_CARD", ["GST_CERTIFICATE", "GSTR_RETURN"])
    assert valid is False
    assert "WRONG_DOCUMENT_TYPE" in warning

    # Correct match
    valid_gst, warning_gst = validate_doc_type_for_requirement("REQ_GST", "GST_CERTIFICATE", ["GST_CERTIFICATE"])
    assert valid_gst is True
    assert warning_gst is None

def test_admin_only_gem_acquisition_access(client):
    """Verify tender acquisition/import routes are strictly restricted to admin role."""
    # 1. Unauthenticated request should redirect to login
    res_unauth = client.get("/tenders/import", follow_redirects=False)
    assert res_unauth.status_code == 302
    assert "/login" in res_unauth.location

    # 2. Officer role request must be rejected (403 Forbidden)
    with client.session_transaction() as sess:
        sess["user_id"] = 2
        sess["user_role"] = "officer"
        sess["user_name"] = "Procurement Officer"

    res_officer = client.get("/tenders/import")
    assert res_officer.status_code == 403

    # 3. Admin role request is permitted (200 OK)
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["user_role"] = "admin"
        sess["user_name"] = "System Administrator"

    res_admin = client.get("/tenders/import")
    assert res_admin.status_code == 200

def test_bidder_upload_route_integration(client):
    """Verify POST /bids/<tender_id>/documents route handles requirement uploads and returns JSON."""
    with app.app_context():
        tender = query_db("SELECT id FROM tenders LIMIT 1", one=True)
        execute_db(
            "UPDATE tenders SET lifecycle_stage = 'OPEN_FOR_BIDDING', bid_end_date = '2030-12-31T23:59:59' WHERE id = ?",
            (tender["id"],)
        )
        bidder = query_db("SELECT id, user_id FROM bidders LIMIT 1", one=True)
        gst_req = query_db("SELECT id FROM requirements WHERE tender_id = ? AND code = 'REQ_GST'", (tender["id"],), one=True)

        sample_gst_path = os.path.join(os.path.dirname(__file__), "..", "sample_data", "bidders", "bidder_a", "GST_Certificate.pdf")
        if not os.path.exists(sample_gst_path):
            pytest.skip("Sample GST file not found")

        with open(sample_gst_path, "rb") as f:
            pdf_bytes = f.read()

    # Authenticate as bidder
    with client.session_transaction() as sess:
        sess["user_id"] = bidder["user_id"]
        sess["user_role"] = "bidder"
        sess["user_name"] = "Apex Technologies"

    data = {
        "requirement_id": str(gst_req["id"]),
        "document": (io.BytesIO(pdf_bytes), "GST_Certificate_Test.pdf")
    }

    res = client.post(
        f"/bids/{tender['id']}/documents",
        data=data,
        content_type="multipart/form-data",
        headers={"Accept": "application/json"}
    )
    if res.status_code != 200:
        print("ERROR RESPONSE:", res.status_code, res.get_data(as_text=True))
    assert res.status_code == 200
    res_json = res.get_json()
    assert res_json["success"] is True
    assert "document" in res_json
    assert res_json["document"]["requirement_id"] == gst_req["id"]
