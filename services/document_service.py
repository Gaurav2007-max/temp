import os
import uuid
import json
import re
import pypdf
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

RECOMMENDED_FILENAMES = {
    "REQ_GST": ["GST_Certificate.pdf", "GSTR1_FY2025_26.pdf", "GSTR3B_FY2025_26.pdf"],
    "REQ_PAN": ["PAN_Card.pdf"],
    "REQ_TURNOVER": ["Audited_Balance_Sheet.pdf", "ITR_FY2023_24.pdf", "ITR_FY2024_25.pdf", "ITR_FY2025_26.pdf"],
    "REQ_EXPERIENCE": ["Work_Order_1.pdf", "Completion_Certificate_1.pdf", "Experience_Certificate.pdf"],
    "REQ_OEM": ["OEM_Authorization_MAF.pdf"],
    "REQ_BIS": ["BIS_Certificate.pdf", "BIS_License.pdf"],
    "REQ_MII": ["Local_Content_Declaration.pdf", "MII_Declaration.pdf"],
    "REQ_UDYAM": ["Udyam_Registration_Certificate.pdf"],
    "REQ_MCA": ["Certificate_of_Incorporation_MCA.pdf"],
    "REQ_EPFO": ["EPFO_Challan_ECR.pdf"],
    "REQ_ESIC": ["ESIC_Challan.pdf"],
    "REQ_STARTUP": ["Startup_India_Recognition.pdf"],
    "REQ_NSIC": ["NSIC_SPRS_Certificate.pdf"],
    "GST_CERTIFICATE": ["GST_Certificate.pdf"],
    "GST_RETURN": ["GSTR1_FY2025_26.pdf", "GSTR3B_FY2025_26.pdf"],
    "PAN_CARD": ["PAN_Card.pdf"],
    "ITR": ["ITR_FY2024_25.pdf", "ITR_FY2023_24.pdf"],
    "BALANCE_SHEET": ["Audited_Balance_Sheet.pdf"],
    "OEM_AUTHORIZATION": ["OEM_Authorization_MAF.pdf"],
    "LOCAL_CONTENT_DECLARATION": ["Local_Content_Declaration.pdf"],
    "UDYAM_CERTIFICATE": ["Udyam_Registration_Certificate.pdf"],
    "MCA_COI": ["Certificate_of_Incorporation_MCA.pdf"],
    "EXPERIENCE_CERTIFICATE": ["Experience_Certificate.pdf"],
    "WORK_ORDER": ["Work_Order_1.pdf"],
    "COMPLETION_CERTIFICATE": ["Completion_Certificate_1.pdf"]
}

def get_recommended_filenames_for_req(req_code, expected_doc_types=None):
    """Returns a list of recommended filenames for UI guidance."""
    if req_code in RECOMMENDED_FILENAMES:
        return RECOMMENDED_FILENAMES[req_code]
    if expected_doc_types:
        names = []
        for dt in expected_doc_types:
            names.extend(RECOMMENDED_FILENAMES.get(dt, [f"{dt.lower()}.pdf"]))
        return list(dict.fromkeys(names))
    return ["document.pdf"]

def validate_uploaded_document_file(file_obj, max_size_bytes=25 * 1024 * 1024):
    """
    Validates uploaded document strictly:
    1. File exists & filename present
    2. Extension is .pdf
    3. Magic bytes start with %PDF-
    4. Valid PDF structure readable by pypdf
    5. File size <= max_size_bytes (default 25 MB)
    Returns (is_valid: bool, error_msg: str or None)
    """
    if not file_obj or not file_obj.filename:
        return False, "No file provided for upload."

    orig_name = file_obj.filename.strip()
    if not orig_name.lower().endswith(".pdf"):
        return False, f"Invalid file type '{orig_name}'. Only official PDF documents (.pdf) are permitted."

    # Check magic bytes
    file_obj.seek(0)
    header = file_obj.read(1024)
    file_obj.seek(0)

    if not header or not header.startswith(b"%PDF-"):
        return False, "Invalid file signature. Uploaded file is not a valid PDF document."

    # Check file size
    file_obj.seek(0, os.SEEK_END)
    size = file_obj.tell()
    file_obj.seek(0)

    if size > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        return False, f"File size ({size / (1024*1024):.2f} MB) exceeds maximum permitted limit of {max_mb} MB."

    if size < 50:
        return False, "Uploaded PDF file is empty or incomplete."

    # Check PDF readability
    try:
        reader = pypdf.PdfReader(file_obj)
        if len(reader.pages) == 0:
            return False, "Uploaded PDF contains 0 pages."
    except Exception as e:
        return False, f"Corrupted PDF structure: {str(e)}"
    finally:
        file_obj.seek(0)

    return True, None

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

def validate_doc_type_for_requirement(requirement_code, doc_type, expected_doc_types=None):
    """
    Checks if a document type is valid for a given requirement code.
    Returns (is_valid, warning_or_issue_text).
    """
    expected = expected_doc_types or DOC_TYPE_REQUIREMENT_MAP.get(requirement_code)
    if not expected:
        return True, None

    if doc_type in expected:
        return True, None

    # Mismatch detected
    return False, f"WRONG_DOCUMENT_TYPE: Document type '{doc_type}' does not satisfy requirement '{requirement_code}'. Expected one of: {', '.join(expected)}."

def save_requirement_document(bidder_id, tender_id, requirement_id, file_obj, declared_doc_type=None, replace_document_id=None):
    """
    Uploads an individual document mapped specifically to a tender requirement.
    Enforces:
    - Server-side validations (tender stage, requirement relation, file validity, max size)
    - Document versioning (v1, v2, etc.), marking latest as is_current=1 and previous as is_current=0
    - OCR extraction & confidence tracking
    - Classification matching against requirement's expected types
    - Field provenance extraction
    Returns (success: bool, doc_dict_or_error_msg: dict or str)
    """
    # 1. Verify Tender Status
    tender = query_db("SELECT id, status, lifecycle_stage FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender:
        return False, "Tender not found."
    if tender["lifecycle_stage"] not in ("OPEN_FOR_BIDDING", "CLARIFICATION"):
        return False, f"Tender is not open for document submission (Stage: {tender['lifecycle_stage']})."

    # 2. Verify Requirement
    req = query_db("SELECT * FROM requirements WHERE id = ? AND tender_id = ?", (requirement_id, tender_id), one=True)
    if not req:
        return False, "Requirement not found or does not belong to this tender."

    # 3. File Validation
    valid, err_msg = validate_uploaded_document_file(file_obj)
    if not valid:
        return False, err_msg

    # 4. Handle Versioning
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    orig_name = secure_filename(file_obj.filename) or f"doc_{uuid.uuid4().hex[:8]}.pdf"

    if replace_document_id:
        target_doc = query_db(
            "SELECT id, version FROM documents WHERE id = ? AND bidder_id = ? AND tender_id = ?",
            (replace_document_id, bidder_id, tender_id),
            one=True
        )
        if target_doc:
            new_version = (target_doc["version"] or 1) + 1
            replaced_doc_id = target_doc["id"]
            execute_db("UPDATE documents SET is_current = 0 WHERE id = ?", (target_doc["id"],))
        else:
            new_version = 1
            replaced_doc_id = None
    else:
        # Check if there is an existing current doc with same requirement_id and same original_filename
        prev_same_file = query_db(
            "SELECT id, version FROM documents WHERE bidder_id = ? AND tender_id = ? AND requirement_id = ? AND original_filename = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
            (bidder_id, tender_id, requirement_id, orig_name),
            one=True
        )
        if prev_same_file:
            new_version = (prev_same_file["version"] or 1) + 1
            replaced_doc_id = prev_same_file["id"]
            execute_db("UPDATE documents SET is_current = 0 WHERE id = ?", (prev_same_file["id"],))
        else:
            # Single-doc requirements auto-supersede previous uploaded document
            single_doc_reqs = ("REQ_GST", "REQ_PAN", "REQ_OEM", "REQ_MII", "REQ_UDYAM", "REQ_BIS", "REQ_MCA", "REQ_EPFO", "REQ_ESIC", "REQ_STARTUP", "REQ_NSIC", "REQ_BLACKLIST")
            prev_doc = query_db(
                "SELECT id, version FROM documents WHERE bidder_id = ? AND tender_id = ? AND requirement_id = ? AND is_current = 1 ORDER BY version DESC LIMIT 1",
                (bidder_id, tender_id, requirement_id),
                one=True
            )
            if prev_doc and req["code"] in single_doc_reqs:
                new_version = (prev_doc["version"] or 1) + 1
                replaced_doc_id = prev_doc["id"]
                execute_db("UPDATE documents SET is_current = 0 WHERE id = ?", (prev_doc["id"],))
            else:
                new_version = 1
                replaced_doc_id = None

    unique_prefix = f"bid_{bidder_id}_t{tender_id}_r{requirement_id}_v{new_version}_{uuid.uuid4().hex[:6]}"
    storage_name = f"{unique_prefix}_{orig_name}"
    storage_path = os.path.join(UPLOAD_DIR, storage_name)

    # Save physical file
    file_obj.seek(0)
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

    # Detect document type
    inferred_type, type_conf, type_stat = detect_document_type(orig_name, text)
    if declared_doc_type:
        # If bidder declared a valid doc_type from expected list, respect it unless strongly contradicted
        inferred_type = declared_doc_type

    # Verify classification against requirement expected types
    expected_doc_types = []
    if req["expected_doc_types"]:
        try:
            expected_doc_types = json.loads(req["expected_doc_types"])
        except Exception:
            expected_doc_types = [req["expected_doc_types"]]
    if not expected_doc_types:
        expected_doc_types = DOC_TYPE_REQUIREMENT_MAP.get(req["code"], [])

    is_type_valid, type_warning = validate_doc_type_for_requirement(req["code"], inferred_type, expected_doc_types)

    if ocr_stat == "FAILED":
        classification_status = "NEEDS_REVIEW"
    elif not is_type_valid:
        classification_status = "WRONG_DOCUMENT_TYPE"
    elif type_stat == "NEEDS_REVIEW":
        classification_status = "NEEDS_REVIEW"
    else:
        classification_status = "VALID"

    # Extract structured fields using LLM / deterministic fallback
    fields = extract_document_fields_llm(inferred_type, text)
    if pages:
        fields["_page_count"] = len(pages)
    fields["_doc_type_confidence"] = type_conf
    fields["_doc_type_status"] = type_stat
    fields["_classification_status"] = classification_status
    if type_warning:
        fields["_classification_warning"] = type_warning

    # Build field-level provenance against page content
    field_provenance = []
    for f_name, f_val in fields.items():
        if str(f_name).startswith("_") or f_val is None:
            continue
        matched_page = 1
        matched_source = ext_method
        matched_conf = ocr_conf
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

    # Insert document record
    doc_id = execute_db(
        """
        INSERT INTO documents (
            bidder_id, tender_id, requirement_id, version, is_current, replaced_document_id,
            original_filename, storage_filename, storage_path, file_size, mime_type,
            doc_type, classification_status, is_supplementary, clarification_id,
            extracted_text, extracted_fields, ocr_status, ocr_confidence, ocr_quality,
            page_count, extraction_method
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bidder_id, tender_id, requirement_id, new_version, 1, replaced_doc_id,
            orig_name, storage_name, storage_path, file_size, "application/pdf",
            inferred_type, classification_status, 0, None,
            text, json.dumps(fields), ocr_stat, ocr_conf, ocr_qual,
            pdf_res.get("page_count", 1), ext_method
        )
    )

    for item in field_provenance:
        item["document_id"] = doc_id

    execute_db("UPDATE documents SET extracted_fields = ? WHERE id = ?", (json.dumps(fields), doc_id))

    return True, {
        "id": doc_id,
        "requirement_id": requirement_id,
        "version": new_version,
        "original_filename": orig_name,
        "doc_type": inferred_type,
        "classification_status": classification_status,
        "classification_warning": type_warning,
        "ocr_status": ocr_stat,
        "ocr_quality": ocr_qual,
        "ocr_confidence": ocr_conf,
        "extraction_method": ext_method,
        "page_count": pdf_res.get("page_count", 1),
        "extracted_fields": fields,
        "replaced_document_id": replaced_doc_id
    }

def get_bidder_document_checklist(tender_id, bidder_id):
    """
    Returns the requirement-driven document checklist for a bidder on a specific tender.
    Includes:
    - Requirement details, mandatory status, expected document types, recommended filenames
    - Uploaded current document (if any), document version, classification status, OCR status
    - Verification status from the latest verification evaluation (PASS / FAIL / NEEDS_REVIEW)
    - Missing document flags, previous document versions history
    - Aggregate summary counts (required, uploaded, missing, needs_review, verified, progress %)
    """
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender:
        return None

    # Fetch tender requirements
    reqs = query_db(
        "SELECT * FROM requirements WHERE tender_id = ? ORDER BY is_mandatory DESC, id ASC",
        (tender_id,)
    )

    # Fetch latest verification evaluation if available
    from services.verification_engine import get_latest_verification
    latest_ver = get_latest_verification(tender_id, bidder_id)
    ver_by_req_id = {}
    if latest_ver and "requirement_evaluations" in latest_ver:
        for rev in latest_ver["requirement_evaluations"]:
            ver_by_req_id[rev["requirement_id"]] = rev

    checklist_items = []
    uploaded_count = 0
    missing_count = 0
    needs_review_count = 0
    verified_count = 0
    mandatory_count = 0

    for r in reqs:
        req_dict = dict(r)
        req_id = req_dict["id"]
        req_code = req_dict["code"]
        is_mand = bool(req_dict.get("is_mandatory", 1))
        if is_mand:
            mandatory_count += 1

        # Parse expected document types
        expected_types = []
        if req_dict.get("expected_doc_types"):
            try:
                expected_types = json.loads(req_dict["expected_doc_types"])
            except Exception:
                expected_types = [req_dict["expected_doc_types"]]
        if not expected_types:
            expected_types = DOC_TYPE_REQUIREMENT_MAP.get(req_code, [])

        # Parse structured criteria
        try:
            req_dict["structured_criteria"] = json.loads(req_dict.get("structured_criteria") or "{}")
        except Exception:
            req_dict["structured_criteria"] = {}

        # Recommended filenames
        recommended_files = get_recommended_filenames_for_req(req_code, expected_types)

        # Find all current documents for this requirement
        current_docs = query_db(
            """
            SELECT * FROM documents
            WHERE bidder_id = ? AND tender_id = ? AND requirement_id = ? AND is_current = 1
            ORDER BY id ASC
            """,
            (bidder_id, tender_id, req_id)
        )
        current_docs_list = []
        for cd in current_docs:
            cdd = dict(cd)
            try:
                cdd["fields"] = json.loads(cdd.get("extracted_fields") or "{}")
            except Exception:
                cdd["fields"] = {}
            current_docs_list.append(cdd)

        # Fallback search if requirement_id wasn't linked (e.g. legacy uploaded document)
        if not current_docs_list and expected_types:
            placeholders = ",".join(["?"] * len(expected_types))
            query = f"""
                SELECT * FROM documents
                WHERE bidder_id = ? AND tender_id = ? AND doc_type IN ({placeholders}) AND is_current = 1
                ORDER BY id ASC
            """
            fb_docs = query_db(query, [bidder_id, tender_id] + expected_types)
            for cd in fb_docs:
                cdd = dict(cd)
                try:
                    cdd["fields"] = json.loads(cdd.get("extracted_fields") or "{}")
                except Exception:
                    cdd["fields"] = {}
                current_docs_list.append(cdd)

        doc_dict = current_docs_list[-1] if current_docs_list else None

        # Find previous versions
        prev_versions = query_db(
            """
            SELECT id, original_filename, version, ocr_status, classification_status, created_at
            FROM documents
            WHERE bidder_id = ? AND tender_id = ? AND requirement_id = ? AND is_current = 0
            ORDER BY version DESC
            """,
            (bidder_id, tender_id, req_id)
        )
        prev_versions_list = [dict(pv) for pv in prev_versions]

        # Check verification status from engine
        eval_data = ver_by_req_id.get(req_id)
        ver_status = eval_data.get("status", "PENDING_VERIFICATION") if eval_data else ("UPLOADED" if doc_dict else "PENDING_UPLOAD")

        is_uploaded = doc_dict is not None
        is_missing = is_mand and not is_uploaded
        doc_class_status = doc_dict.get("classification_status", "VALID") if doc_dict else None
        ocr_status = doc_dict.get("ocr_status") if doc_dict else None

        needs_review = (
            ver_status in ("NEEDS_REVIEW", "WARNING") or
            doc_class_status in ("WRONG_DOCUMENT_TYPE", "NEEDS_REVIEW") or
            ocr_status in ("NEEDS_REVIEW", "FAILED")
        )

        if is_uploaded:
            uploaded_count += 1
        if is_missing:
            missing_count += 1
        if needs_review:
            needs_review_count += 1
        if ver_status in ("PASS", "COMPLIANT"):
            verified_count += 1

        # Extract expiry date if present
        expiry_date = None
        if doc_dict and doc_dict.get("fields"):
            f = doc_dict["fields"]
            expiry_date = f.get("valid_till") or f.get("authorization_valid_till") or f.get("expiry_date")

        checklist_items.append({
            "requirement_id": req_id,
            "code": req_code,
            "title": req_dict["title"],
            "description": req_dict.get("description"),
            "requirement_type": req_dict.get("requirement_type"),
            "is_mandatory": is_mand,
            "threshold_value": req_dict.get("threshold_value"),
            "threshold_unit": req_dict.get("threshold_unit"),
            "expected_doc_types": expected_types,
            "recommended_filenames": recommended_files,
            "structured_criteria": req_dict["structured_criteria"],
            "uploaded": is_uploaded,
            "missing": is_missing,
            "needs_review": needs_review,
            "current_document": doc_dict,
            "current_documents": current_docs_list,
            "current_document_id": doc_dict["id"] if doc_dict else None,
            "current_document_filename": doc_dict["original_filename"] if doc_dict else None,
            "current_document_version": doc_dict["version"] if doc_dict else None,
            "classification_status": doc_class_status,
            "ocr_status": ocr_status,
            "ocr_quality": doc_dict.get("ocr_quality") if doc_dict else None,
            "page_count": doc_dict.get("page_count") if doc_dict else None,
            "extraction_method": doc_dict.get("extraction_method") if doc_dict else None,
            "verification_status": ver_status,
            "verification_explanation": eval_data.get("explanation") if eval_data else None,
            "verification_issues": eval_data.get("issues", []) if eval_data else [],
            "score_awarded": eval_data.get("score_awarded") if eval_data else 0,
            "max_score": eval_data.get("max_score", 10) if eval_data else 10,
            "expiry_date": expiry_date,
            "previous_versions": prev_versions_list
        })

    total_reqs = len(checklist_items)
    progress_pct = round((uploaded_count / total_reqs * 100) if total_reqs > 0 else 0, 1)

    summary = {
        "tender_id": tender_id,
        "gem_bid_id": tender["gem_bid_id"],
        "title": tender["title"],
        "lifecycle_stage": tender["lifecycle_stage"],
        "total_requirements": total_reqs,
        "mandatory_count": mandatory_count,
        "uploaded_count": uploaded_count,
        "missing_mandatory": missing_count,
        "needs_review_count": needs_review_count,
        "verified_count": verified_count,
        "progress_percentage": progress_pct,
        "all_mandatory_uploaded": missing_count == 0
    }

    return {
        "tender": dict(tender),
        "requirements": checklist_items,
        "summary": summary,
        "latest_verification": latest_ver
    }

def save_and_process_uploaded_documents(bidder_id, tender_id, files_list, is_supplementary=0, clarification_id=None):
    """
    Processes multiple files uploaded by a bidder in batch (backward-compatibility).
    Auto-detects document types and matches against tender requirements.
    """
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    saved_docs = []

    # Map existing requirements by expected type for auto-linking
    req_rows = query_db("SELECT id, code, expected_doc_types FROM requirements WHERE tender_id = ?", (tender_id,))
    type_to_req = {}
    for rr in req_rows:
        expected = []
        if rr["expected_doc_types"]:
            try:
                expected = json.loads(rr["expected_doc_types"])
            except Exception:
                expected = [rr["expected_doc_types"]]
        if not expected:
            expected = DOC_TYPE_REQUIREMENT_MAP.get(rr["code"], [])
        for et in expected:
            type_to_req[et] = rr["id"]

    for file_obj in files_list:
        if not file_obj or not file_obj.filename:
            continue

        orig_name = secure_filename(file_obj.filename) or f"doc_{uuid.uuid4().hex[:8]}.pdf"
        unique_prefix = f"bid_{bidder_id}_t{tender_id}_{uuid.uuid4().hex[:8]}"
        storage_name = f"{unique_prefix}_{orig_name}"
        storage_path = os.path.join(UPLOAD_DIR, storage_name)

        file_obj.save(storage_path)
        file_size = os.path.getsize(storage_path)

        pdf_res = extract_pdf_content(storage_path)
        text = pdf_res.get("full_text", "")
        pages = pdf_res.get("pages", [])
        ext_method = pdf_res.get("extraction_method", "TEXT")
        ocr_stat = pdf_res.get("ocr_status", "VALID")
        ocr_conf = pdf_res.get("ocr_confidence", 0.98)
        ocr_qual = pdf_res.get("ocr_quality", "HIGH")

        inferred_type, type_conf, type_stat = detect_document_type(orig_name, text)
        matched_req_id = type_to_req.get(inferred_type)

        fields = extract_document_fields_llm(inferred_type, text)
        if pages:
            fields["_page_count"] = len(pages)
        fields["_doc_type_confidence"] = type_conf
        fields["_doc_type_status"] = type_stat

        field_provenance = []
        for f_name, f_val in fields.items():
            if str(f_name).startswith("_") or f_val is None:
                continue
            matched_page = 1
            matched_source = ext_method
            matched_conf = ocr_conf
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

        doc_id = execute_db(
            """
            INSERT INTO documents (
                bidder_id, tender_id, requirement_id, version, is_current,
                original_filename, storage_filename, storage_path,
                file_size, mime_type, doc_type, is_supplementary, clarification_id,
                extracted_text, extracted_fields, ocr_status, ocr_confidence, ocr_quality,
                page_count, extraction_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bidder_id, tender_id, matched_req_id, 1, 1,
                orig_name, storage_name, storage_path,
                file_size, file_obj.content_type or "application/pdf", inferred_type,
                is_supplementary, clarification_id,
                text, json.dumps(fields),
                ocr_stat, ocr_conf, ocr_qual,
                pdf_res.get("page_count", 1), ext_method
            )
        )

        for item in field_provenance:
            item["document_id"] = doc_id

        execute_db("UPDATE documents SET extracted_fields = ? WHERE id = ?", (json.dumps(fields), doc_id))

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

def get_documents_by_bidder_and_tender(bidder_id, tender_id, only_current=True):
    """
    Returns documents submitted by a bidder for a tender.
    By default returns only active/current versions (is_current=1).
    """
    if only_current:
        rows = query_db(
            "SELECT * FROM documents WHERE bidder_id = ? AND tender_id = ? AND is_current = 1 ORDER BY id ASC",
            (bidder_id, tender_id)
        )
    else:
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
