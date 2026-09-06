import os
import sqlite3
from flask import g

DATABASE_PATH = os.environ.get("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "..", "gem_compliance.db"))

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'officer', 'bidder')),
    phone TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bidders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    company_name TEXT NOT NULL,
    pan TEXT,
    gstin TEXT,
    udyam_reg_no TEXT,
    registered_address TEXT,
    contact_person TEXT,
    phone TEXT,
    email TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tenders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gem_bid_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    organization TEXT NOT NULL,
    category TEXT,
    status TEXT DEFAULT 'Published',
    lifecycle_stage TEXT DEFAULT 'OPEN_FOR_BIDDING' CHECK(lifecycle_stage IN ('OPEN_FOR_BIDDING', 'CLARIFICATION', 'OFFICER_REVIEW', 'DECIDED')),
    estimated_value REAL DEFAULT 0,
    min_turnover REAL DEFAULT 0,
    min_experience_years INTEGER DEFAULT 0,
    min_projects_count INTEGER DEFAULT 0,
    min_cumulative_project_value REAL DEFAULT 0,
    min_local_content REAL DEFAULT 50,
    bid_start_date TEXT,
    bid_end_date TEXT,
    tender_version TEXT DEFAULT 'v1',
    pdf_filename TEXT,
    pdf_storage_path TEXT,
    created_by INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tender_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    version_tag TEXT NOT NULL,
    corrigendum_reason TEXT,
    changes_summary TEXT,
    officer_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
    FOREIGN KEY (officer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    tender_version_id INTEGER,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    requirement_type TEXT NOT NULL CHECK(requirement_type IN ('STATUTORY', 'FINANCIAL', 'TECHNICAL', 'DOCUMENTARY')),
    is_mandatory INTEGER DEFAULT 1,
    threshold_value REAL,
    threshold_unit TEXT,
    expected_doc_types TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
    FOREIGN KEY (tender_version_id) REFERENCES tender_versions(id)
);

CREATE TABLE IF NOT EXISTS tender_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    officer_id INTEGER NOT NULL,
    assigned_by INTEGER,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
    FOREIGN KEY (officer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (assigned_by) REFERENCES users(id),
    UNIQUE(tender_id, officer_id)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bidder_id INTEGER NOT NULL,
    tender_id INTEGER NOT NULL,
    original_filename TEXT NOT NULL,
    storage_filename TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    file_size INTEGER DEFAULT 0,
    mime_type TEXT,
    doc_type TEXT NOT NULL,
    is_supplementary INTEGER DEFAULT 0,
    clarification_id INTEGER,
    extracted_text TEXT,
    extracted_fields TEXT,
    ocr_status TEXT DEFAULT 'VALID' CHECK(ocr_status IN ('VALID', 'WARNING', 'NEEDS_REVIEW', 'INVALID')),
    ocr_confidence REAL DEFAULT 1.0,
    ocr_quality TEXT DEFAULT 'HIGH',
    page_count INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id) ON DELETE CASCADE,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    bidder_id INTEGER NOT NULL,
    version_num INTEGER DEFAULT 1,
    score REAL DEFAULT 0,
    eligibility TEXT DEFAULT 'NEEDS_REVIEW' CHECK(eligibility IN ('ELIGIBLE', 'NOT_ELIGIBLE', 'NEEDS_REVIEW')),
    risk_level TEXT DEFAULT 'LOW' CHECK(risk_level IN ('LOW', 'MEDIUM', 'HIGH')),
    risk_factors TEXT,
    statutory_summary TEXT,
    conflicts_detected TEXT,
    recommendation TEXT,
    officer_decision TEXT CHECK(officer_decision IN ('QUALIFIED', 'DISQUALIFIED', NULL)),
    officer_remarks TEXT,
    decided_by INTEGER,
    decided_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id) ON DELETE CASCADE,
    FOREIGN KEY (decided_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS verification_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verification_id INTEGER NOT NULL,
    requirement_id INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('COMPLIANT', 'NON_COMPLIANT', 'NEEDS_REVIEW', 'WARNING', 'UNAVAILABLE')),
    is_mandatory INTEGER DEFAULT 1,
    score_awarded REAL DEFAULT 0,
    max_score REAL DEFAULT 10,
    evidence TEXT,
    issues TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (verification_id) REFERENCES verifications(id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES requirements(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS clarifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tender_id INTEGER NOT NULL,
    bidder_id INTEGER NOT NULL,
    verification_id INTEGER NOT NULL,
    officer_id INTEGER NOT NULL,
    requirement_code TEXT,
    query_text TEXT NOT NULL,
    deadline TEXT,
    status TEXT DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'RESPONDED', 'RESOLVED', 'EXPIRED')),
    response_text TEXT,
    responded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tender_id) REFERENCES tenders(id) ON DELETE CASCADE,
    FOREIGN KEY (bidder_id) REFERENCES bidders(id) ON DELETE CASCADE,
    FOREIGN KEY (verification_id) REFERENCES verifications(id) ON DELETE CASCADE,
    FOREIGN KEY (officer_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER,
    actor_name TEXT,
    actor_role TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    details TEXT,
    ip_address TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (actor_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_tenders_gem_id ON tenders(gem_bid_id);
CREATE INDEX IF NOT EXISTS idx_documents_bidder_tender ON documents(bidder_id, tender_id);
CREATE INDEX IF NOT EXISTS idx_verifications_bidder_tender ON verifications(bidder_id, tender_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
"""

def get_db_connection(db_path=None):
    path = db_path or os.environ.get("SQLITE_PATH", DATABASE_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def get_db():
    if "db" not in g:
        g.db = get_db_connection()
    return g.db

def close_db(e=None):
    try:
        db = g.pop("db", None)
        if db is not None:
            db.close()
    except (RuntimeError, LookupError):
        pass

def init_db(db_path=None):
    conn = get_db_connection(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

def query_db(query, args=(), one=False):
    cur = get_db().execute(query, args)
    rv = cur.fetchall()
    cur.close()
    return (rv[0] if rv else None) if one else rv

def execute_db(query, args=(), commit=True):
    db = get_db()
    cur = db.execute(query, args)
    if commit:
        db.commit()
    last_id = cur.lastrowid
    cur.close()
    return last_id
