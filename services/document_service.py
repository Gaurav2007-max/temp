"""Document management service: multi-document handling, secure storage,
classification, quality check, page-level field extraction, and authorization.
"""
import os
import re
import json
from werkzeug.utils import secure_filename
from database.db import get_db, utc_now_iso
from services.pdf_service import extract_pdf_pages_and_text
from services.llm_service import llm_service

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
ALLOWED_EXTENSIONS = {"pdf", "txt", "png", "jpg", "jpeg"}

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

class DocumentService:
    @staticmethod
    def process_uploaded_document(file_obj, bidder_id, tender_id, submission_id=None, clarification_id=None, verification_version=1):
        """
        Processes an individual uploaded document file object:
        1. Validates and saves to secure disk path.
        2. Extracts text and page mapping (pypdf for PDF, decode for text).
        3. Classifies document independently (with confidence).
        4. Evaluates quality status.
        5. Extracts page-level fields and stores them in document_extracted_fields.
        6. Inserts independent document record in documents table.
        Returns document dict.
        """
        raw_filename = file_obj.filename
        if not raw_filename or not allowed_file(raw_filename):
            return None

        clean_name = secure_filename(raw_filename)
        timestamp_prefix = utc_now_iso().replace(":", "-").replace(".", "-")
        saved_filename = f"{bidder_id}_{timestamp_prefix}_{clean_name}"
        
        target_dir = os.path.join(UPLOAD_FOLDER, f"bidder_{bidder_id}")
        os.makedirs(target_dir, exist_ok=True)
        disk_path = os.path.join(target_dir, saved_filename)
        file_obj.save(disk_path)

        # Extract text & pages
        ext = clean_name.rsplit(".", 1)[1].lower()
        pages_dict = {}
        extracted_text = ""
        
        if ext == "pdf":
            _, extracted_text, pages_dict = extract_pdf_pages_and_text(disk_path)
        else:
            try:
                with open(disk_path, "r", encoding="utf-8", errors="ignore") as f:
                    extracted_text = f.read()
                pages_dict = {1: extracted_text}
            except Exception:
                extracted_text = ""
                pages_dict = {}

        # Classify document
        doc_type, confidence, source = llm_service.classify_document(clean_name, extracted_text)
        
        # Assess quality
        quality_status, quality_details = llm_service.assess_document_quality(clean_name, extracted_text)

        # Extract page-level fields
        extracted_fields_list = llm_service.extract_fields(clean_name, extracted_text, pages_dict)

        # Persist document record
        conn = get_db()
        cursor = conn.cursor()
        now = utc_now_iso()

        cursor.execute("""
        INSERT INTO documents (
            bidder_id, submission_id, tender_id, filename, secure_filepath,
            document_type, upload_timestamp, document_version, clarification_id,
            verification_version, quality_status, quality_details, extracted_text,
            extracted_fields, classification_confidence, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            bidder_id, submission_id, tender_id, clean_name, disk_path,
            doc_type, now, 1, clarification_id,
            verification_version, quality_status, quality_details, extracted_text,
            json.dumps(extracted_fields_list), confidence
        ))
        doc_id = cursor.lastrowid

        # Insert page-level evidence records
        for f in extracted_fields_list:
            p_num = f.get("page_number", "UNKNOWN")
            cursor.execute("""
            INSERT INTO document_extracted_fields (
                document_id, filename, page_number, field_name, value, source, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                doc_id, clean_name, p_num, f.get("field_name"), f.get("value"),
                f.get("source", "OCR"), f.get("confidence", 0.9), now
            ))

        conn.commit()
        conn.close()

        return {
            "id": doc_id,
            "filename": clean_name,
            "document_type": doc_type,
            "classification_confidence": confidence,
            "quality_status": quality_status,
            "extracted_fields": extracted_fields_list
        }

    @staticmethod
    def get_document_by_id(doc_id):
        """Retrieve document record by ID."""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def get_extracted_fields_for_document(doc_id):
        """Retrieve page-level extracted fields for document ID."""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM document_extracted_fields WHERE document_id = ?", (doc_id,))
        rows = cursor.fetchall()
        conn.close()
        return [dict(r) for r in rows]

    @staticmethod
    def authorize_document_access(user, doc_id):
        """
        Authorizes access to document file:
        - Admin: full access
        - Officer: access if assigned to tender
        - Bidder: access only if own document
        """
        if not user:
            return False, "Not authenticated"
        
        doc = DocumentService.get_document_by_id(doc_id)
        if not doc:
            return False, "Document not found"

        role = user.get("role")
        if role == "admin":
            return True, doc

        conn = get_db()
        cursor = conn.cursor()

        if role == "officer":
            # Must be assigned to tender
            cursor.execute("""
            SELECT 1 FROM tender_officer_assignments
            WHERE tender_id = ? AND officer_id = ?
            """, (doc["tender_id"], user["id"]))
            assigned = cursor.fetchone()
            conn.close()
            if assigned:
                return True, doc
            return False, "Procurement officer is not assigned to this tender"

        if role == "bidder":
            # Must match bidder record
            cursor.execute("SELECT id FROM bidders WHERE user_id = ?", (user["id"],))
            bidder_row = cursor.fetchone()
            conn.close()
            if bidder_row and bidder_row["id"] == doc["bidder_id"]:
                return True, doc
            return False, "Bidder cannot access another bidder's documents"

        conn.close()
        return False, "Unauthorized role"
