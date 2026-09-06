import os
import uuid
import json
from werkzeug.utils import secure_filename
from database.db import get_db, execute_db, query_db
from services.pdf_service import extract_pdf_content
from services.llm_service import extract_document_fields_llm

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads", "documents"))

DOC_TYPE_REQUIREMENT_MAP = {
    "REQ_GST": ["GST_CERTIFICATE", "GST_RETURN"],
    "REQ_PAN": ["PAN_CARD", "ITR"],
    "REQ_TURNOVER": ["ITR", "BALANCE_SHEET", "ANNUAL_RETURN"],
    "REQ_EXPERIENCE": ["EXPERIENCE_CERTIFICATE", "WORK_ORDER", "COMPLETION_CERTIFICATE"],
    "REQ_OEM": ["OEM_AUTHORIZATION"],
    "REQ_BIS": ["BIS_CERTIFICATE", "BIS_LICENSE"],
    "REQ_MII": ["LOCAL_CONTENT_DECLARATION", "MII_CERTIFICATE"],
    "REQ_UDYAM": ["UDYAM_CERTIFICATE", "MSME_CERTIFICATE"],
    "REQ_EPFO": ["EPFO_CHALLAN", "EPFO_RETURN"],
    "REQ_ESIC": ["ESIC_CHALLAN", "ESIC_RETURN"],
    "REQ_STARTUP": ["STARTUP_CERTIFICATE"],
    "REQ_NSIC": ["NSIC_CERTIFICATE"]
}

def detect_document_type(filename, text=""):
    """
    Infers document type from filename and text content patterns.
    """
    name = (filename or "").upper()
    t = (text or "").upper()

    if "GST" in name or "GSTIN" in name:
        if "RETURN" in name or "GSTR" in name or "GSTR1" in name or "GSTR3B" in name or "GSTR" in t:
            return "GST_RETURN"
        return "GST_CERTIFICATE"

    if "PAN" in name or "PERMANENT ACCOUNT NUMBER" in t:
        return "PAN_CARD"

    if "ITR" in name or "INCOME TAX RETURN" in t or "ACKNOWLEDGEMENT" in name:
        return "ITR"

    if "OEM" in name or "MANUFACTURER" in name or "AUTHORIZATION" in name or "MANUFACTURER'S AUTHORIZATION" in t:
        return "OEM_AUTHORIZATION"

    if "BIS" in name or "BUREAU OF INDIAN STANDARDS" in t or "CRS" in name:
        return "BIS_CERTIFICATE"

    if "LOCAL" in name or "MII" in name or "MAKE IN INDIA" in name or "LOCAL CONTENT" in t:
        return "LOCAL_CONTENT_DECLARATION"

    if "UDYAM" in name or "MSME" in name or "UDYAM REGISTRATION" in t:
        return "UDYAM_CERTIFICATE"

    if "EXPERIENCE" in name or "COMPLETION" in name or "WORK ORDER" in name or "COMPLETION CERTIFICATE" in t:
        return "EXPERIENCE_CERTIFICATE"

    if "EPFO" in name or "PROVIDENT FUND" in t or "ECR" in name:
        return "EPFO_CHALLAN"

    if "ESIC" in name or "EMPLOYEES' STATE INSURANCE" in t:
        return "ESIC_CHALLAN"

    if "STARTUP" in name or "DIPP" in name or "DPIIT" in t:
        return "STARTUP_CERTIFICATE"

    if "NSIC" in name:
        return "NSIC_CERTIFICATE"

    return "OTHER"

def validate_doc_type_for_requirement(requirement_code, doc_type):
    """
    Checks if a document type is valid for a given requirement code.
    Returns (is_valid, warning_or_issue_text).
    """
    expected = DOC_TYPE_REQUIREMENT_MAP.get(requirement_code)
    if not expected:
        return True, None

    if doc_type in expected:
        return True, None

    # Mismatch detected
    return False, f"Document type '{doc_type}' may not satisfy requirement '{requirement_code}'. Expected one of: {', '.join(expected)}."

def save_and_process_uploaded_documents(bidder_id, tender_id, files_list, is_supplementary=0, clarification_id=None):
    """
    Processes multiple files uploaded by a bidder.
    Each file becomes an independent record in the 'documents' table.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_docs = []

    for file_obj in files_list:
        if not file_obj or not file_obj.filename:
            continue

        orig_name = secure_filename(file_obj.filename)
        if not orig_name:
            orig_name = f"doc_{uuid.uuid4().hex[:8]}.pdf"

        unique_prefix = f"bid_{bidder_id}_t{tender_id}_{uuid.uuid4().hex[:8]}"
        storage_name = f"{unique_prefix}_{orig_name}"
        storage_path = os.path.join(UPLOAD_DIR, storage_name)

        # Save physical file
        file_obj.save(storage_path)
        file_size = os.path.getsize(storage_path)

        # Process PDF content & OCR
        pdf_res = extract_pdf_content(storage_path)
        text = pdf_res.get("full_text", "")
        pages = pdf_res.get("pages", [])

        # Detect document type
        inferred_type = detect_document_type(orig_name, text)

        # Extract structured fields using LLM / deterministic fallback
        fields = extract_document_fields_llm(inferred_type, text)
        if pages:
            fields["_page_count"] = len(pages)

        # Insert independent document record
        doc_id = execute_db(
            """
            INSERT INTO documents (
                bidder_id, tender_id, original_filename, storage_filename, storage_path,
                file_size, mime_type, doc_type, is_supplementary, clarification_id,
                extracted_text, extracted_fields, ocr_status, ocr_confidence, ocr_quality, page_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bidder_id, tender_id, orig_name, storage_name, storage_path,
                file_size, file_obj.content_type or "application/pdf", inferred_type,
                is_supplementary, clarification_id,
                text, json.dumps(fields),
                pdf_res.get("ocr_status", "VALID"),
                pdf_res.get("ocr_confidence", 1.0),
                pdf_res.get("ocr_quality", "HIGH"),
                pdf_res.get("page_count", 1)
            )
        )

        saved_docs.append({
            "id": doc_id,
            "original_filename": orig_name,
            "doc_type": inferred_type,
            "storage_name": storage_name,
            "extracted_fields": fields,
            "ocr_status": pdf_res.get("ocr_status", "VALID"),
            "ocr_confidence": pdf_res.get("ocr_confidence", 1.0),
            "page_count": pdf_res.get("page_count", 1)
        })

    return saved_docs

def get_documents_by_bidder_and_tender(bidder_id, tender_id):
    rows = query_db(
        "SELECT * FROM documents WHERE bidder_id = ? AND tender_id = ? ORDER BY id ASC",
        (bidder_id, tender_id)
    )
    docs = []
    for r in rows:
        d = dict(r)
        try:
            d["fields"] = json.loads(d.get("extracted_fields") or "{}")
        except Exception:
            d["fields"] = {}
        docs.append(d)
    return docs
