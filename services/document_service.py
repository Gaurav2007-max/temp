import os
import uuid
import json
import re
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
    "REQ_MII": ["LOCAL_CONTENT_DECLARATION", "MII_CERTIFICATE", "MII_DECLARATION"],
    "REQ_UDYAM": ["UDYAM_CERTIFICATE", "MSME_CERTIFICATE"],
    "REQ_MCA": ["MCA_COI", "INCORPORATION_CERTIFICATE"],
    "REQ_EPFO": ["EPFO_CHALLAN", "EPFO_RETURN"],
    "REQ_ESIC": ["ESIC_CHALLAN", "ESIC_RETURN"],
    "REQ_STARTUP": ["STARTUP_CERTIFICATE"],
    "REQ_NSIC": ["NSIC_CERTIFICATE"]
}

def detect_document_type(filename, text=""):
    """
    Deterministic document classifier based on filename tokens and full text signatures.
    Returns (doc_type, confidence, status).
    """
    name = (filename or "").upper()
    t = (text or "").upper()

    # 1. Work Order / Purchase Order vs Completion Certificate vs Experience
    if "COMPLETION" in name or "WORK COMPLETION" in t or "COMPLETION CERTIFICATE" in t or "SATISFACTORY COMPLETION" in t:
        return "COMPLETION_CERTIFICATE", 0.95, "VALID"

    if "WORK ORDER" in name or "PURCHASE ORDER" in name or "AWARD OF CONTRACT" in t or "WORK ORDER NUMBER" in t or "PO NUMBER" in t:
        return "WORK_ORDER", 0.95, "VALID"

    if "EXPERIENCE" in name or "PAST PERFORMANCE" in t or "PERFORMANCE CERTIFICATE" in t:
        return "EXPERIENCE_CERTIFICATE", 0.90, "VALID"

    # 2. OEM Authorization Form
    if "OEM" in name or "MAF" in name or "MANUFACTURER AUTHORIZATION" in t or "MANUFACTURER'S AUTHORIZATION" in t or "AUTHORIZES M/S" in t or "AUTHORIZATION LETTER" in t:
        return "OEM_AUTHORIZATION", 0.95, "VALID"

    # 3. GST Return vs GST Certificate
    if "GST" in name or "GSTIN" in name or "FORM GST REG-06" in t or "REGISTRATION CERTIFICATE" in t and "GOODS AND SERVICES TAX" in t:
        if "RETURN" in name or "GSTR" in name or "GSTR-1" in t or "GSTR-3B" in t or "GSTR3B" in name:
            return "GST_RETURN", 0.95, "VALID"
        return "GST_CERTIFICATE", 0.95, "VALID"

    # 4. PAN Card
    if "PAN" in name or "PERMANENT ACCOUNT NUMBER" in t or "INCOME TAX DEPARTMENT" in t and "PERMANENT ACCOUNT" in t:
        if "ITR" not in name and "ACKNOWLEDGEMENT" not in name and "FORM ITR" not in t:
            return "PAN_CARD", 0.95, "VALID"

    # 5. ITR / Balance Sheet / Financial Returns
    if "ITR" in name or "INCOME TAX RETURN" in t or "ITR-V" in t or "ANNUAL TURNOVER" in t or "BALANCE SHEET" in name or "GROSS REVENUE" in t or "ACKNOWLEDGEMENT" in name:
        return "ITR", 0.95, "VALID"

    # 6. Udyam / MSME Registration
    if "UDYAM" in name or "MSME" in name or "UDYAM REGISTRATION" in t or "MINISTRY OF MICRO" in t:
        return "UDYAM_CERTIFICATE", 0.95, "VALID"

    # 7. Make In India / Local Content
    if "LOCAL" in name or "MII" in name or "MAKE IN INDIA" in name or "LOCAL CONTENT" in t or "CLASS-I LOCAL SUPPLIER" in t:
        return "LOCAL_CONTENT_DECLARATION", 0.95, "VALID"

    # 8. BIS Certificate / License
    if "BIS" in name or "BUREAU OF INDIAN STANDARDS" in t or "CRS" in name or "STANDARD MARK" in t or "IS/IEC" in t:
        return "BIS_CERTIFICATE", 0.95, "VALID"

    # 9. MCA / Certificate of Incorporation
    if "MCA" in name or "INCORPORATION" in name or "MINISTRY OF CORPORATE AFFAIRS" in t or "CERTIFICATE OF INCORPORATION" in t or "CIN" in name:
        return "MCA_COI", 0.95, "VALID"

    # 10. EPFO / ESIC
    if "EPFO" in name or "PROVIDENT FUND" in t or "ECR" in name:
        return "EPFO_CHALLAN", 0.90, "VALID"

    if "ESIC" in name or "EMPLOYEES' STATE INSURANCE" in t:
        return "ESIC_CHALLAN", 0.90, "VALID"

    # 11. Startup India / NSIC
    if "STARTUP" in name or "DPIIT" in t or "DIPP" in name:
        return "STARTUP_CERTIFICATE", 0.90, "VALID"

    if "NSIC" in name or "NATIONAL SMALL INDUSTRIES" in t:
        return "NSIC_CERTIFICATE", 0.90, "VALID"

    # Fallback to OTHER with low confidence needing review
    return "OTHER", 0.40, "NEEDS_REVIEW"

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
    return False, f"WRONG_DOCUMENT_TYPE: Document type '{doc_type}' may not satisfy requirement '{requirement_code}'. Expected one of: {', '.join(expected)}."

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
        ext_method = pdf_res.get("extraction_method", "TEXT")
        ocr_stat = pdf_res.get("ocr_status", "VALID")
        ocr_conf = pdf_res.get("ocr_confidence", 0.98)
        ocr_qual = pdf_res.get("ocr_quality", "HIGH")

        # Detect document type deterministically
        inferred_type, type_conf, type_stat = detect_document_type(orig_name, text)

        # Extract structured fields using LLM / deterministic fallback
        fields = extract_document_fields_llm(inferred_type, text)
        if pages:
            fields["_page_count"] = len(pages)
        fields["_doc_type_confidence"] = type_conf
        fields["_doc_type_status"] = type_stat

        # Build field-level provenance against page content
        field_provenance = []
        for f_name, f_val in fields.items():
            if str(f_name).startswith("_") or f_val is None:
                continue
            matched_page = 1
            matched_source = ext_method
            matched_conf = ocr_conf
            # Search which page contains the value string or token
            search_token = str(f_val).strip()
            if len(search_token) > 3:
                for p in pages:
                    if search_token.lower() in p.get("text", "").lower():
                        matched_page = p.get("page_num", 1)
                        matched_source = p.get("source", ext_method)
                        matched_conf = p.get("confidence", ocr_conf)
                        break

            field_provenance.append({
                "field": f_name,
                "value": f_val,
                "document_name": orig_name,
                "page": matched_page,
                "source": matched_source,
                "confidence": matched_conf
            })

        fields["_field_evidence"] = field_provenance

        # Insert independent document record
        doc_id = execute_db(
            """
            INSERT INTO documents (
                bidder_id, tender_id, original_filename, storage_filename, storage_path,
                file_size, mime_type, doc_type, is_supplementary, clarification_id,
                extracted_text, extracted_fields, ocr_status, ocr_confidence, ocr_quality,
                page_count, extraction_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bidder_id, tender_id, orig_name, storage_name, storage_path,
                file_size, file_obj.content_type or "application/pdf", inferred_type,
                is_supplementary, clarification_id,
                text, json.dumps(fields),
                ocr_stat, ocr_conf, ocr_qual,
                pdf_res.get("page_count", 1), ext_method
            )
        )

        # Attach doc_id to field provenance
        for item in field_provenance:
            item["document_id"] = doc_id

        execute_db(
            "UPDATE documents SET extracted_fields = ? WHERE id = ?",
            (json.dumps(fields), doc_id)
        )

        saved_docs.append({
            "id": doc_id,
            "original_filename": orig_name,
            "doc_type": inferred_type,
            "storage_name": storage_name,
            "extracted_fields": fields,
            "ocr_status": ocr_stat,
            "ocr_confidence": ocr_conf,
            "extraction_method": ext_method,
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
