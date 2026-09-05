"""Clarification service: request creation, multiple-file clarification submissions,
state transitions, and comprehensive re-verification.
"""
import os
import json
from database.db import get_db, utc_now_iso
from services.document_service import DocumentService
from services.verification_engine import VerificationEngine

class ClarificationService:
    @staticmethod
    def create_clarification_request(submission_id, tender_id, bidder_id, requirement_id, reason, details, officer_id):
        """Procurement officer initiates a formal clarification request."""
        conn = get_db()
        cursor = conn.cursor()
        now_iso = utc_now_iso()

        cursor.execute("""
        INSERT INTO clarifications (
            submission_id, tender_id, bidder_id, requirement_id, reason, details,
            status, requested_by, requested_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'REQUESTED', ?, ?)
        """, (
            submission_id, tender_id, bidder_id, requirement_id, reason, details,
            officer_id, now_iso
        ))
        clarification_id = cursor.lastrowid

        # Update submission status to CLARIFICATION_REQUIRED
        cursor.execute("""
        UPDATE bid_submissions
        SET status = 'CLARIFICATION_REQUIRED'
        WHERE id = ?
        """, (submission_id,))

        # Update tender status to CLARIFICATION if currently in BIDDING_CLOSED or VERIFICATION
        cursor.execute("SELECT status FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        if t and t["status"] in ("BIDDING_CLOSED", "VERIFICATION", "OPEN_FOR_BIDDING"):
            cursor.execute("""
            UPDATE tenders
            SET status = 'CLARIFICATION', current_stage = 'CLARIFICATION'
            WHERE id = ?
            """, (tender_id,))

        # Audit log
        cursor.execute("""
        INSERT INTO audit_logs (user_id, user_role, action, entity_type, entity_id, details_json, timestamp)
        VALUES (?, 'officer', 'CLARIFICATION_REQUESTED', 'clarification', ?, ?, ?)
        """, (
            officer_id, clarification_id,
            json.dumps({"submission_id": submission_id, "requirement_id": requirement_id, "reason": reason, "details": details}),
            now_iso
        ))

        conn.commit()
        conn.close()
        return clarification_id

    @staticmethod
    def submit_clarification_documents(clarification_id, file_list, response_remarks=""):
        """
        Bidder responds to clarification with multiple files:
        1. Validates each file and saves as an independent document record with clarification_id.
        2. Preserves all existing original documents.
        3. Sets clarification status to 'SUBMITTED'.
        4. Sets submission status to 'CLARIFICATION_SUBMITTED'.
        5. Triggers automated RE-VERIFICATION using ALL documents against LATEST tender version.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM clarifications WHERE id = ?", (clarification_id,))
        clar = cursor.fetchone()
        if not clar:
            conn.close()
            return False, "Clarification request not found."

        submission_id = clar["submission_id"]
        tender_id = clar["tender_id"]
        bidder_id = clar["bidder_id"]

        now_iso = utc_now_iso()

        # Update clarification record
        cursor.execute("""
        UPDATE clarifications
        SET status = 'SUBMITTED', response_remarks = ?, responded_at = ?
        WHERE id = ?
        """, (response_remarks, now_iso, clarification_id))

        cursor.execute("""
        UPDATE bid_submissions
        SET status = 'CLARIFICATION_SUBMITTED'
        WHERE id = ?
        """, (submission_id,))

        conn.commit()
        conn.close()

        # Process each uploaded clarification file
        uploaded_docs = []
        for f in file_list:
            doc_rec = DocumentService.process_uploaded_document(
                file_obj=f,
                bidder_id=bidder_id,
                tender_id=tender_id,
                submission_id=submission_id,
                clarification_id=clarification_id,
                verification_version=2
            )
            if doc_rec:
                uploaded_docs.append(doc_rec)

        # Trigger automatic RE-VERIFICATION:
        # Re-verification takes ALL current valid bidder documents + clarification documents
        # against the LATEST tender version!
        verif_result = VerificationEngine.run_verification(
            submission_id=submission_id,
            tender_id=tender_id,
            bidder_id=bidder_id,
            is_reverification=True
        )

        return True, {
            "uploaded_count": len(uploaded_docs),
            "clarification_id": clarification_id,
            "verification_result": verif_result
        }
