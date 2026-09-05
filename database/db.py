"""Database configuration, schema, and connection utilities for SQLite."""
import os
import sqlite3
import json
from datetime import datetime, timezone

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(__file__), "gem_compliance.db"))

def utc_now_iso():
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()

class RowWrapper:
    """Wrapper around sqlite3.Row providing dictionary-like .get() and column access."""
    __slots__ = ('_row',)
    def __init__(self, cursor, row):
        self._row = sqlite3.Row(cursor, row)
    def __getitem__(self, key):
        return self._row[key]
    def __iter__(self):
        return iter(self._row)
    def __len__(self):
        return len(self._row)
    def keys(self):
        return self._row.keys()
    def get(self, key, default=None):
        try:
            return self._row[key]
        except (IndexError, KeyError):
            return default
    def __contains__(self, key):
        return key in self._row.keys()

def get_db():
    """Get database connection with dict-like row access."""
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = lambda c, r: RowWrapper(c, r)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn

def init_db():
    """Initialize database tables and indexes."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    cursor = conn.cursor()

    # 1. Users
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL, -- 'admin', 'officer', 'bidder'
        organization TEXT,
        email TEXT,
        phone TEXT,
        created_at TEXT NOT NULL
    )
    """)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN phone TEXT")
    except Exception:
        pass

    # 2. Tenders
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tenders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gem_bid_id TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        organization TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT,
        estimated_value REAL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'OPEN_FOR_BIDDING',
        current_stage TEXT NOT NULL DEFAULT 'BIDDING',
        tender_version INTEGER DEFAULT 1,
        pdf_path TEXT,
        bid_window_days INTEGER DEFAULT 5,
        clarification_window_days INTEGER DEFAULT 5,
        bidding_start_at TEXT,
        bidding_end_at TEXT,
        clarification_start_at TEXT,
        clarification_end_at TEXT,
        actual_bidding_closed_at TEXT,
        actual_clarification_closed_at TEXT,
        completed_at TEXT,
        closed_at TEXT,
        created_at TEXT NOT NULL
    )
    """)

    # 3. Tender Officer Assignments
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tender_officer_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tender_id INTEGER NOT NULL,
        officer_id INTEGER NOT NULL,
        assigned_at TEXT NOT NULL,
        assigned_by INTEGER,
        FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
        FOREIGN KEY(officer_id) REFERENCES users(id) ON DELETE CASCADE,
        UNIQUE(tender_id, officer_id)
    )
    """)

    # 4. Tender Versions (Corrigendum)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tender_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tender_id INTEGER NOT NULL,
        version_number INTEGER NOT NULL,
        metadata_json TEXT NOT NULL,
        requirements_json TEXT NOT NULL,
        pdf_path TEXT,
        corrigendum_reason TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL,
        FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE
    )
    """)

    # 5. Tender Requirements
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tender_requirements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tender_id INTEGER NOT NULL,
        tender_version INTEGER DEFAULT 1,
        code TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        is_mandatory INTEGER DEFAULT 1,
        expected_document_types TEXT NOT NULL, -- JSON array
        validation_rule TEXT NOT NULL,
        rule_parameters TEXT, -- JSON object
        source_clause TEXT,
        source_clause_page TEXT DEFAULT 'UNKNOWN',
        FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE
    )
    """)

    # 6. Bidders
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bidders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE NOT NULL,
        legal_name TEXT NOT NULL,
        trade_name TEXT,
        pan TEXT,
        gstin TEXT,
        udyam_reg TEXT,
        cin TEXT,
        registered_address TEXT,
        contact_email TEXT,
        contact_phone TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)

    # 7. Bid Submissions
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bid_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tender_id INTEGER NOT NULL,
        bidder_id INTEGER NOT NULL,
        tender_version_submitted INTEGER DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'SUBMITTED',
        submission_timestamp TEXT NOT NULL,
        compliance_score REAL DEFAULT 0.0,
        eligibility_recommendation TEXT DEFAULT 'NEEDS_REVIEW',
        risk_level TEXT DEFAULT 'MEDIUM',
        risk_score REAL DEFAULT 0.0,
        active_verification_version INTEGER DEFAULT 1,
        officer_decision TEXT,
        officer_decision_remarks TEXT,
        officer_decision_by INTEGER,
        officer_decision_at TEXT,
        FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
        FOREIGN KEY(bidder_id) REFERENCES bidders(id) ON DELETE CASCADE,
        UNIQUE(tender_id, bidder_id)
    )
    """)

    # 8. Documents
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bidder_id INTEGER NOT NULL,
        submission_id INTEGER,
        tender_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        secure_filepath TEXT NOT NULL,
        document_type TEXT NOT NULL DEFAULT 'OTHER',
        upload_timestamp TEXT NOT NULL,
        document_version INTEGER DEFAULT 1,
        clarification_id INTEGER,
        verification_version INTEGER DEFAULT 1,
        quality_status TEXT DEFAULT 'OK',
        quality_details TEXT,
        extracted_text TEXT,
        extracted_fields TEXT, -- JSON
        classification_confidence REAL DEFAULT 0.8,
        is_active INTEGER DEFAULT 1,
        FOREIGN KEY(bidder_id) REFERENCES bidders(id) ON DELETE CASCADE,
        FOREIGN KEY(tender_id) REFERENCES tenders(id) ON DELETE CASCADE
    )
    """)

    # 9. Document Extracted Fields (Page-level evidence entity)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS document_extracted_fields (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        document_id INTEGER NOT NULL,
        filename TEXT NOT NULL,
        page_number TEXT NOT NULL DEFAULT 'UNKNOWN',
        field_name TEXT NOT NULL,
        value TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'OCR',
        confidence REAL DEFAULT 0.9,
        created_at TEXT NOT NULL,
        FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
    )
    """)

    # 10. Verifications
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        tender_id INTEGER NOT NULL,
        bidder_id INTEGER NOT NULL,
        version_number INTEGER NOT NULL DEFAULT 1,
        tender_version INTEGER NOT NULL DEFAULT 1,
        compliance_score REAL DEFAULT 0.0,
        eligibility_recommendation TEXT DEFAULT 'NEEDS_REVIEW',
        risk_level TEXT DEFAULT 'MEDIUM',
        risk_score REAL DEFAULT 0.0,
        risk_issues_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(submission_id) REFERENCES bid_submissions(id) ON DELETE CASCADE
    )
    """)

    # 11. Requirement Verifications
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS requirement_verifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        verification_id INTEGER NOT NULL,
        requirement_id INTEGER NOT NULL,
        requirement_code TEXT NOT NULL,
        status TEXT NOT NULL, -- 'PASS', 'FAIL', 'WARNING', 'NEEDS_REVIEW', 'WRONG_DOCUMENT_TYPE'
        is_mandatory INTEGER DEFAULT 1,
        candidate_document_ids TEXT NOT NULL, -- JSON array
        evidence_records TEXT NOT NULL, -- JSON array of page-level evidence objects
        calculated_values TEXT, -- JSON object of aggregated calculations
        rule_summary TEXT,
        verification_source TEXT NOT NULL DEFAULT 'MIXED',
        conflict_detected INTEGER DEFAULT 0,
        conflict_details TEXT,
        FOREIGN KEY(verification_id) REFERENCES verifications(id) ON DELETE CASCADE
    )
    """)

    # 12. Clarifications
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clarifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        submission_id INTEGER NOT NULL,
        tender_id INTEGER NOT NULL,
        bidder_id INTEGER NOT NULL,
        requirement_id INTEGER,
        reason TEXT NOT NULL,
        details TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'REQUESTED', -- 'REQUESTED', 'SUBMITTED', 'UNDER_REVIEW', 'ACCEPTED', 'REJECTED', 'EXPIRED'
        requested_by INTEGER NOT NULL,
        requested_at TEXT NOT NULL,
        response_remarks TEXT,
        responded_at TEXT,
        FOREIGN KEY(submission_id) REFERENCES bid_submissions(id) ON DELETE CASCADE
    )
    """)

    # 13. Audit Logs
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_role TEXT,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER,
        details_json TEXT,
        timestamp TEXT NOT NULL
    )
    """)

    # 14. Statutory Checks
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS statutory_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bidder_id INTEGER NOT NULL,
        api_type TEXT NOT NULL,
        identifier_used TEXT NOT NULL,
        status TEXT NOT NULL,
        raw_response_json TEXT,
        source_mode TEXT NOT NULL DEFAULT 'MOCK',
        checked_at TEXT NOT NULL,
        FOREIGN KEY(bidder_id) REFERENCES bidders(id) ON DELETE CASCADE
    )
    """)

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully at:", DB_PATH)
