"""Tender management service: lifecycle, timeline calculations, early closure,
corrigendum versioning, PDF ingestion, and deadline validation.
"""
import os
import json
from datetime import datetime, timezone, timedelta
from database.db import get_db, utc_now_iso
from services.pdf_service import generate_sample_tender_pdf, extract_pdf_pages_and_text

def parse_iso(ts_str):
    if not ts_str:
        return None
    try:
        # Handle trailing Z or offsets
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None

class TenderService:
    @staticmethod
    def calculate_time_remaining(end_iso):
        """Calculates human-readable time remaining string from now to end_iso."""
        end_dt = parse_iso(end_iso)
        if not end_dt:
            return "EXPIRED"
        now_dt = datetime.now(timezone.utc)
        diff = end_dt - now_dt
        if diff.total_seconds() <= 0:
            return "EXPIRED"
        days = diff.days
        hours = int((diff.total_seconds() % 86400) // 3600)
        minutes = int((diff.total_seconds() % 3600) // 60)
        if days > 0:
            return f"{days} days {hours} hours remaining"
        elif hours > 0:
            return f"{hours} hours {minutes} mins remaining"
        else:
            return f"{minutes} mins remaining"

    @staticmethod
    def create_tender(title, organization, category, description="Procurement specification", estimated_value=10000000,
                      gem_bid_id=None, bidding_days=5, clarification_days=5,
                      bid_window_days=None, clarification_window_days=None, pdf_path=None):
        """Create a new tender record with computed 5-day deadlines."""
        if bid_window_days is not None:
            bidding_days = bid_window_days
        if clarification_window_days is not None:
            clarification_days = clarification_window_days
        if not gem_bid_id:
            import random
            gem_bid_id = f"GEM/2026/B/{random.randint(1000000, 9999999)}"

        conn = get_db()
        cursor = conn.cursor()
        now_dt = datetime.now(timezone.utc)
        bidding_end_dt = now_dt + timedelta(days=bidding_days)
        clarification_start_dt = bidding_end_dt
        clarification_end_dt = clarification_start_dt + timedelta(days=clarification_days)

        bidding_start_iso = now_dt.isoformat()
        bidding_end_iso = bidding_end_dt.isoformat()
        clarification_start_iso = clarification_start_dt.isoformat()
        clarification_end_iso = clarification_end_dt.isoformat()

        cursor.execute("""
        INSERT INTO tenders (
            gem_bid_id, title, organization, category, description, estimated_value,
            status, current_stage, tender_version, pdf_path,
            bid_window_days, clarification_window_days,
            bidding_start_at, bidding_end_at, clarification_start_at, clarification_end_at,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            gem_bid_id, title, organization, category, description, estimated_value,
            "OPEN_FOR_BIDDING", "BIDDING", pdf_path,
            bidding_days, clarification_days,
            bidding_start_iso, bidding_end_iso, clarification_start_iso, clarification_end_iso,
            bidding_start_iso
        ))
        tender_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return tender_id

    @staticmethod
    def ingest_mock_gem_tender(mock_json_path=None):
        """
        Imports Mock GeM tender, generates sample tender PDF, extracts clauses,
        and saves tender requirements.
        """
        if not mock_json_path:
            mock_json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mock_api", "gem.json")
        
        with open(mock_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        record = data["records"][0]
        gem_bid_id = record["gem_bid_id"]

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM tenders WHERE gem_bid_id = ?", (gem_bid_id,))
        existing = cursor.fetchone()
        
        pdf_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "tenders", f"{gem_bid_id.replace('/', '_')}_Tender.pdf")
        if not os.path.exists(pdf_path):
            generate_sample_tender_pdf(pdf_path, gem_bid_id)

        if existing:
            tender_id = existing["id"]
        else:
            tender_id = TenderService.create_tender(
                gem_bid_id=gem_bid_id,
                title=record["title"],
                organization=record["organization"],
                category=record["category"],
                description=record["description"],
                estimated_value=record.get("estimated_value", 25000000),
                bid_window_days=5,
                clarification_window_days=5,
                pdf_path=pdf_path
            )

        # Standard requirements extracted from tender clauses
        requirements_defs = [
            {
                "code": "REQ_GST",
                "title": "Goods & Services Tax (GST) Registration",
                "description": "Bidder must possess active GST registration with proof of regular GSTR-1 / GSTR-3B filings.",
                "is_mandatory": 1,
                "expected_document_types": ["GST_CERTIFICATE", "GST_RETURN"],
                "validation_rule": "GST_VALIDITY",
                "rule_parameters": {"require_returns": True},
                "source_clause": "Section I, Clause 1.1: Goods & Services Tax (GST) Compliance",
                "source_clause_page": "1"
            },
            {
                "code": "REQ_PAN",
                "title": "Permanent Account Number (PAN) Card",
                "description": "Bidder must possess valid PAN card matching legal entity identity.",
                "is_mandatory": 1,
                "expected_document_types": ["PAN"],
                "validation_rule": "PAN_VALIDITY",
                "rule_parameters": {},
                "source_clause": "Section I, Clause 1.2: Permanent Account Number (PAN)",
                "source_clause_page": "1"
            },
            {
                "code": "REQ_TURNOVER",
                "title": "Average Annual Financial Turnover (Last 3 FY)",
                "description": "Average turnover for last 3 financial years must be >= INR 5.00 Crore.",
                "is_mandatory": 1,
                "expected_document_types": ["ITR", "FINANCIAL_STATEMENT"],
                "validation_rule": "TURNOVER_MIN_AVERAGE",
                "rule_parameters": {"min_avg_turnover": 50000000, "years": 3},
                "source_clause": "Section II, Clause 2.1: Annual Turnover (3 Financial Years)",
                "source_clause_page": "2"
            },
            {
                "code": "REQ_EXPERIENCE",
                "title": "Similar Work Experience (Minimum 3 Projects)",
                "description": "At least 3 similar completed projects in last 5 years with aggregate contract value >= INR 5.00 Crore.",
                "is_mandatory": 1,
                "expected_document_types": ["WORK_ORDER", "COMPLETION_CERTIFICATE"],
                "validation_rule": "EXPERIENCE_PROJECTS",
                "rule_parameters": {"min_projects": 3, "min_aggregate_value": 50000000, "lookback_years": 5},
                "source_clause": "Section II, Clause 2.2: Similar Work Experience",
                "source_clause_page": "2"
            },
            {
                "code": "REQ_OEM",
                "title": "Manufacturer Authorization Form (MAF)",
                "description": "Valid OEM authorization letter verifying manufacturer, bidder, product line, and valid expiry date.",
                "is_mandatory": 1,
                "expected_document_types": ["OEM_AUTHORIZATION"],
                "validation_rule": "OEM_AUTH",
                "rule_parameters": {"verify_expiry": True},
                "source_clause": "Section III, Clause 3.1: Manufacturer Authorization (OEM Form)",
                "source_clause_page": "2"
            },
            {
                "code": "REQ_LOCAL_CONTENT",
                "title": "Make in India (MII) Local Content Declaration",
                "description": "Minimum local content percentage must be >= 50% (Class-I Local Supplier).",
                "is_mandatory": 1,
                "expected_document_types": ["LOCAL_CONTENT_DECLARATION"],
                "validation_rule": "LOCAL_CONTENT_PCT",
                "rule_parameters": {"min_local_content_pct": 50.0},
                "source_clause": "Section III, Clause 3.3: Make in India (MII) Preference",
                "source_clause_page": "2"
            },
            {
                "code": "REQ_BIS",
                "title": "Bureau of Indian Standards (BIS) Registration",
                "description": "Equipment must conform to BIS / CRS safety registration standards.",
                "is_mandatory": 0,
                "expected_document_types": ["BIS"],
                "validation_rule": "BIS_LICENSE",
                "rule_parameters": {},
                "source_clause": "Section III, Clause 3.2: Bureau of Indian Standards (BIS) Certification",
                "source_clause_page": "2"
            },
            {
                "code": "REQ_BLACKLIST",
                "title": "Non-Debarment / Integrity Undertaking",
                "description": "Bidder must not be blacklisted or debarred by any government authority.",
                "is_mandatory": 1,
                "expected_document_types": ["OTHER", "LOCAL_CONTENT_DECLARATION"],
                "validation_rule": "BLACKLIST_CHECK",
                "rule_parameters": {},
                "source_clause": "Section IV, Clause 4.1: Non-Debarment Undertaking",
                "source_clause_page": "2"
            }
        ]

        # Insert requirements if not already present
        for req in requirements_defs:
            cursor.execute("SELECT id FROM tender_requirements WHERE tender_id = ? AND code = ?", (tender_id, req["code"]))
            if not cursor.fetchone():
                cursor.execute("""
                INSERT INTO tender_requirements (
                    tender_id, tender_version, code, title, description, is_mandatory,
                    expected_document_types, validation_rule, rule_parameters,
                    source_clause, source_clause_page
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    tender_id, req["code"], req["title"], req["description"], req["is_mandatory"],
                    json.dumps(req["expected_document_types"]), req["validation_rule"],
                    json.dumps(req["rule_parameters"]), req["source_clause"], req["source_clause_page"]
                ))

        conn.commit()
        conn.close()
        return tender_id

    @staticmethod
    def get_tender_detail(tender_id):
        """Retrieve full tender details, timeline, assigned officers, and requirements."""
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        tender_row = cursor.fetchone()
        if not tender_row:
            conn.close()
            return None
        tender = dict(tender_row)

        # Assigned officers
        cursor.execute("""
        SELECT u.id, u.username, u.full_name, u.email, a.assigned_at
        FROM tender_officer_assignments a
        JOIN users u ON a.officer_id = u.id
        WHERE a.tender_id = ?
        """, (tender_id,))
        tender["assigned_officers"] = [dict(r) for r in cursor.fetchall()]

        # Requirements
        cursor.execute("""
        SELECT * FROM tender_requirements
        WHERE tender_id = ? AND tender_version = ?
        ORDER BY is_mandatory DESC, id ASC
        """, (tender_id, tender.get("tender_version", 1)))
        reqs = []
        for r in cursor.fetchall():
            rd = dict(r)
            rd["expected_document_types"] = json.loads(rd["expected_document_types"])
            rd["rule_parameters"] = json.loads(rd["rule_parameters"]) if rd.get("rule_parameters") else {}
            reqs.append(rd)
        tender["requirements"] = reqs

        # Bidder count
        cursor.execute("SELECT COUNT(*) as count FROM bid_submissions WHERE tender_id = ?", (tender_id,))
        tender["bidder_count"] = cursor.fetchone()["count"]

        # Time remaining strings
        tender["bidding_time_remaining"] = TenderService.calculate_time_remaining(tender.get("bidding_end_at"))
        tender["clarification_time_remaining"] = TenderService.calculate_time_remaining(tender.get("clarification_end_at"))

        conn.close()
        return tender

    @staticmethod
    def close_bidding_early(tender_id, officer_id, reason="Early closure by procurement officer"):
        """
        Officer closes bidding early.
        1. actual_bidding_closed_at = current UTC timestamp
        2. status moves to CLARIFICATION
        3. clarification_start_at = actual_bidding_closed_at
        4. clarification_end_at = clarification_start_at + clarification_window_days
        5. Audit log recorded.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        if not t:
            conn.close()
            return False, "Tender not found"

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()
        clar_window = t["clarification_window_days"] or 5
        clar_end_dt = now_dt + timedelta(days=clar_window)
        clar_end_iso = clar_end_dt.isoformat()

        cursor.execute("""
        UPDATE tenders
        SET actual_bidding_closed_at = ?,
            status = 'CLARIFICATION',
            current_stage = 'CLARIFICATION',
            clarification_start_at = ?,
            clarification_end_at = ?
        WHERE id = ?
        """, (now_iso, now_iso, clar_end_iso, tender_id))

        # Log audit
        cursor.execute("""
        INSERT INTO audit_logs (user_id, user_role, action, entity_type, entity_id, details_json, timestamp)
        VALUES (?, 'officer', 'EARLY_BIDDING_CLOSURE', 'tender', ?, ?, ?)
        """, (
            officer_id, tender_id,
            json.dumps({
                "previous_status": t["status"],
                "new_status": "CLARIFICATION",
                "reason": reason,
                "actual_bidding_closed_at": now_iso,
                "new_clarification_end_at": clar_end_iso
            }),
            now_iso
        ))

        conn.commit()
        conn.close()
        return True, "Bidding closed early. Clarification window activated."

    @staticmethod
    def close_clarification_early(tender_id, officer_id, reason="Clarification closed early by procurement officer"):
        """
        Officer closes clarification window early.
        1. actual_clarification_closed_at = current UTC timestamp
        2. status moves to OFFICER_REVIEW
        3. Audit log recorded.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        if not t:
            conn.close()
            return False, "Tender not found"

        now_iso = utc_now_iso()
        cursor.execute("""
        UPDATE tenders
        SET actual_clarification_closed_at = ?,
            status = 'OFFICER_REVIEW',
            current_stage = 'OFFICER_REVIEW'
        WHERE id = ?
        """, (now_iso, tender_id))

        # Log audit
        cursor.execute("""
        INSERT INTO audit_logs (user_id, user_role, action, entity_type, entity_id, details_json, timestamp)
        VALUES (?, 'officer', 'EARLY_CLARIFICATION_CLOSURE', 'tender', ?, ?, ?)
        """, (
            officer_id, tender_id,
            json.dumps({
                "previous_status": t["status"],
                "new_status": "OFFICER_REVIEW",
                "reason": reason,
                "actual_clarification_closed_at": now_iso
            }),
            now_iso
        ))

        conn.commit()
        conn.close()
        return True, "Clarification closed early. Tender moved to Officer Review."

    @staticmethod
    def create_corrigendum(tender_id, officer_id, reason, updated_metadata=None, updated_requirements=None):
        """
        Creates a new tender version / corrigendum:
        1. Snapshots old version into tender_versions.
        2. Increments tender_version.
        3. Updates requirements for new version.
        4. Marks existing bid_submissions as NEEDS_REEVALUATION.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        if not t:
            conn.close()
            return False, "Tender not found"

        old_version = t["tender_version"]
        new_version = old_version + 1
        now_iso = utc_now_iso()

        # Fetch current requirements for snapshot
        cursor.execute("SELECT * FROM tender_requirements WHERE tender_id = ? AND tender_version = ?", (tender_id, old_version))
        req_rows = [dict(r) for r in cursor.fetchall()]

        # Insert snapshot into tender_versions
        cursor.execute("""
        INSERT INTO tender_versions (
            tender_id, version_number, metadata_json, requirements_json,
            pdf_path, corrigendum_reason, created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            tender_id, old_version,
            json.dumps(dict(t)), json.dumps(req_rows),
            t["pdf_path"], reason, officer_id, now_iso
        ))

        # Update tender row version
        cursor.execute("UPDATE tenders SET tender_version = ? WHERE id = ?", (new_version, tender_id))

        # Re-insert requirements under new_version (applying any updates if given)
        for req in req_rows:
            cursor.execute("""
            INSERT INTO tender_requirements (
                tender_id, tender_version, code, title, description, is_mandatory,
                expected_document_types, validation_rule, rule_parameters,
                source_clause, source_clause_page
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tender_id, new_version, req["code"], req["title"], req["description"],
                req["is_mandatory"], req["expected_document_types"], req["validation_rule"],
                req["rule_parameters"], req["source_clause"], req["source_clause_page"]
            ))

        # Mark existing bidder submissions as NEEDS_REEVALUATION
        cursor.execute("""
        UPDATE bid_submissions
        SET status = 'RE_VERIFICATION',
            eligibility_recommendation = 'NEEDS_REVIEW'
        WHERE tender_id = ?
        """, (tender_id,))

        # Log audit
        cursor.execute("""
        INSERT INTO audit_logs (user_id, user_role, action, entity_type, entity_id, details_json, timestamp)
        VALUES (?, 'officer', 'CORRIGENDUM_ISSUED', 'tender', ?, ?, ?)
        """, (
            officer_id, tender_id,
            json.dumps({
                "old_version": old_version,
                "new_version": new_version,
                "reason": reason
            }),
            now_iso
        ))

        conn.commit()
        conn.close()
        return True, f"Corrigendum issued successfully. Version {new_version} activated."

    @staticmethod
    def validate_bidder_submission_allowed(tender_id):
        """
        Server-side check:
        1. Tender must exist.
        2. Status must be 'OPEN_FOR_BIDDING'.
        3. Current UTC timestamp must be < bidding_end_at.
        Returns: (is_allowed: bool, message: str)
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        conn.close()

        if not t:
            return False, "Tender does not exist."

        if t["status"] != "OPEN_FOR_BIDDING":
            return False, f"Bidding is not open for this tender. Current status: {t['status']}."

        end_dt = parse_iso(t["bidding_end_at"])
        if end_dt and datetime.now(timezone.utc) > end_dt:
            return False, "Bidding submission deadline has expired. Submissions are rejected."

        return True, "Bidding is open."

    @staticmethod
    def validate_clarification_submission_allowed(tender_id):
        """
        Server-side check for clarification submission:
        Current UTC timestamp must be <= clarification_end_at.
        """
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tenders WHERE id = ?", (tender_id,))
        t = cursor.fetchone()
        conn.close()

        if not t:
            return False, "Tender does not exist."

        end_dt = parse_iso(t["clarification_end_at"])
        if end_dt and datetime.now(timezone.utc) > end_dt:
            return False, "Clarification window has expired. Submissions are rejected."

        return True, "Clarification is open."
