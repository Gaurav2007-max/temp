from datetime import datetime, timedelta
from database.db import get_db, execute_db, query_db
from services.document_service import save_and_process_uploaded_documents
from services.verification_engine import run_bidder_verification

def create_clarification_request(tender_id, bidder_id, verification_id, officer_id, requirement_code, query_text, deadline_days=3):
    """
    Procurement Officer issues a clarification request to a bidder.
    """
    deadline = (datetime.utcnow() + timedelta(days=deadline_days)).strftime("%Y-%m-%d %H:%M")
    clar_id = execute_db(
        """
        INSERT INTO clarifications (
            tender_id, bidder_id, verification_id, officer_id,
            requirement_code, query_text, deadline, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
        """,
        (tender_id, bidder_id, verification_id, officer_id, requirement_code, query_text, deadline)
    )
    return clar_id

def submit_clarification_response(clarification_id, bidder_id, response_text, uploaded_files=None):
    """
    Bidder submits response and optional supplementary documents.
    Triggers re-verification which creates a new verification version (v2, v3, etc.)
    without overwriting the original verification.
    """
    clar = query_db(
        "SELECT * FROM clarifications WHERE id = ? AND bidder_id = ?",
        (clarification_id, bidder_id),
        one=True
    )
    if not clar:
        raise ValueError("Clarification request not found or not owned by bidder.")

    # 1. Process supplementary documents if provided
    if uploaded_files:
        save_and_process_uploaded_documents(
            bidder_id=bidder_id,
            tender_id=clar["tender_id"],
            files_list=uploaded_files,
            is_supplementary=1,
            clarification_id=clarification_id
        )

    # 2. Update clarification status
    now_str = datetime.utcnow().isoformat()
    execute_db(
        """
        UPDATE clarifications SET
            status = 'RESPONDED',
            response_text = ?,
            responded_at = ?
        WHERE id = ?
        """,
        (response_text, now_str, clarification_id)
    )

    # 3. Trigger re-verification pass (creates next verification version)
    new_ver = run_bidder_verification(
        tender_id=clar["tender_id"],
        bidder_id=bidder_id,
        is_reverification=True
    )

    return new_ver

def get_clarifications_by_tender(tender_id):
    return query_db(
        """
        SELECT c.*, b.company_name as bidder_name, u.name as officer_name
        FROM clarifications c
        JOIN bidders b ON c.bidder_id = b.id
        JOIN users u ON c.officer_id = u.id
        WHERE c.tender_id = ?
        ORDER BY c.id DESC
        """,
        (tender_id,)
    )

def get_clarifications_by_bidder(bidder_id):
    return query_db(
        """
        SELECT c.*, t.gem_bid_id, t.title as tender_title, u.name as officer_name
        FROM clarifications c
        JOIN tenders t ON c.tender_id = t.id
        JOIN users u ON c.officer_id = u.id
        WHERE c.bidder_id = ?
        ORDER BY c.id DESC
        """,
        (bidder_id,)
    )
