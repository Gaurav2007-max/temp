import os
import re
import json
import shutil
from datetime import datetime, timedelta
from database.db import get_db, execute_db, query_db
from services.statutory_service import fetch_gem_bid

TENDER_PDF_DIR = os.environ.get("TENDER_PDF_DIR", os.path.join(os.path.dirname(__file__), "..", "uploads", "tenders"))

DEFAULT_REQUIREMENT_TEMPLATES = [
    {"code": "REQ_GST", "title": "Valid GSTIN Registration & Up-to-date Returns", "type": "STATUTORY", "mandatory": 1, "docs": "['GST_CERTIFICATE', 'GST_RETURN']"},
    {"code": "REQ_PAN", "title": "Permanent Account Number & IT Compliance", "type": "STATUTORY", "mandatory": 1, "docs": "['PAN_CARD', 'ITR']"},
    {"code": "REQ_TURNOVER", "title": "Minimum Average Annual Turnover", "type": "FINANCIAL", "mandatory": 1, "docs": "['ITR', 'BALANCE_SHEET']"},
    {"code": "REQ_EXPERIENCE", "title": "Past Project Experience & Completion", "type": "TECHNICAL", "mandatory": 1, "docs": "['EXPERIENCE_CERTIFICATE', 'WORK_ORDER']"},
    {"code": "REQ_OEM", "title": "OEM Manufacturer Authorization", "type": "TECHNICAL", "mandatory": 1, "docs": "['OEM_AUTHORIZATION']"},
    {"code": "REQ_MII", "title": "Make in India (MII) Local Content Minimum 50%", "type": "STATUTORY", "mandatory": 1, "docs": "['LOCAL_CONTENT_DECLARATION']"},
    {"code": "REQ_BIS", "title": "BIS Standards / CRS License Compliance", "type": "TECHNICAL", "mandatory": 0, "docs": "['BIS_CERTIFICATE']"},
    {"code": "REQ_UDYAM", "title": "MSME / Udyam Registration (Preference Benefit)", "type": "STATUTORY", "mandatory": 0, "docs": "['UDYAM_CERTIFICATE']"},
    {"code": "REQ_BLACKLIST", "title": "Non-Debarment & Non-Blacklisting Declaration", "type": "STATUTORY", "mandatory": 1, "docs": "['DEBARMENT_DECLARATION']"}
]

def create_tender(gem_bid_id, title, organization, category, description="", estimated_value=0,
                  min_turnover=0, min_local_content=50, min_experience_years=3,
                  min_projects_count=2, min_cumulative_project_value=0,
                  bid_days=5, created_by=None, pdf_file=None):
    """
    Creates a new tender, initializes v1 snapshot, copies/saves PDF if provided, and builds requirement matrix.
    """
    os.makedirs(TENDER_PDF_DIR, exist_ok=True)
    now = datetime.utcnow()
    end_date = now + timedelta(days=bid_days)

    pdf_storage_name = None
    pdf_path = None
    if pdf_file:
        pdf_storage_name = f"tender_{gem_bid_id.replace('/', '_')}.pdf"
        pdf_path = os.path.join(TENDER_PDF_DIR, pdf_storage_name)
        if hasattr(pdf_file, "save"):
            pdf_file.save(pdf_path)
        elif isinstance(pdf_file, str) and os.path.exists(pdf_file):
            shutil.copyfile(pdf_file, pdf_path)

    # Insert tender
    tender_id = execute_db(
        """
        INSERT INTO tenders (
            gem_bid_id, title, description, organization, category,
            status, lifecycle_stage, estimated_value, min_turnover,
            min_experience_years, min_projects_count, min_cumulative_project_value,
            min_local_content, bid_start_date, bid_end_date, tender_version,
            pdf_filename, pdf_storage_path, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gem_bid_id, title, description, organization, category,
            "Published", "OPEN_FOR_BIDDING", estimated_value, min_turnover,
            min_experience_years, min_projects_count, min_cumulative_project_value,
            min_local_content, now.isoformat(), end_date.isoformat(), "v1",
            pdf_storage_name, pdf_path, created_by
        )
    )

    # Create v1 tender_version record
    v1_id = execute_db(
        """
        INSERT INTO tender_versions (
            tender_id, version_tag, corrigendum_reason, changes_summary, officer_id
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (tender_id, "v1", "Initial tender publication", "Original tender requirements created.", created_by)
    )

    # Create default structured requirements
    for tmpl in DEFAULT_REQUIREMENT_TEMPLATES:
        threshold = None
        unit = None
        if tmpl["code"] == "REQ_TURNOVER":
            threshold = min_turnover
            unit = "INR"
        elif tmpl["code"] == "REQ_MII":
            threshold = min_local_content
            unit = "PERCENT"
        elif tmpl["code"] == "REQ_EXPERIENCE":
            threshold = min_projects_count
            unit = "PROJECTS"

        execute_db(
            """
            INSERT INTO requirements (
                tender_id, tender_version_id, code, title, description,
                requirement_type, is_mandatory, threshold_value, threshold_unit, expected_doc_types
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tender_id, v1_id, tmpl["code"], tmpl["title"],
                f"Tender compliance check for {tmpl['title']}",
                tmpl["type"], tmpl["mandatory"], threshold, unit, tmpl["docs"]
            )
        )

    return tender_id

def import_tender_from_gem(gem_bid_id, officer_id=None):
    """
    Imports tender details from GeM API or mock fixture.
    """
    gem_res = fetch_gem_bid(gem_bid_id)
    if not gem_res.get("is_valid") or not gem_res.get("data"):
        raise ValueError(f"Could not find GeM Bid: {gem_bid_id}")

    data = gem_res["data"]
    # Check sample tender PDF if available
    sample_pdf = os.path.join(os.path.dirname(__file__), "..", "sample_data", "tenders", "GEM_2026_B_1234567_Tender.pdf")
    pdf_to_use = sample_pdf if os.path.exists(sample_pdf) else None

    tender_id = create_tender(
        gem_bid_id=data.get("gem_bid_id", gem_bid_id),
        title=data.get("title", "Government e-Marketplace Procurement"),
        organization=data.get("organization", "Central Procurement Entity"),
        category=data.get("category", "Goods & Services"),
        description=data.get("description", "Imported from GeM portal."),
        estimated_value=float(data.get("estimated_value", 25000000)),
        min_turnover=50000000,
        min_local_content=50,
        min_projects_count=2,
        min_cumulative_project_value=10000000,
        bid_days=7,
        created_by=officer_id,
        pdf_file=pdf_to_use
    )
    return tender_id

def is_bidding_open(tender_id):
    """
    Server-side bidding deadline & lifecycle check.
    Returns (is_open: bool, message: str)
    """
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender:
        return False, "Tender not found."

    if tender["lifecycle_stage"] != "OPEN_FOR_BIDDING":
        return False, f"Bidding is closed. Tender is currently in {tender['lifecycle_stage']} stage."

    if tender["bid_end_date"]:
        try:
            end_dt = datetime.fromisoformat(tender["bid_end_date"])
            if datetime.utcnow() > end_dt:
                return False, f"Bidding deadline passed on {end_dt.strftime('%Y-%m-%d %H:%M UTC')}. Late bids rejected."
        except Exception:
            pass

    return True, "Bidding is open."

def update_tender_lifecycle_stage(tender_id, new_stage, officer_id):
    """
    Transitions tender lifecycle stage (OPEN_FOR_BIDDING -> CLARIFICATION -> OFFICER_REVIEW -> DECIDED).
    """
    valid_stages = ("OPEN_FOR_BIDDING", "CLARIFICATION", "OFFICER_REVIEW", "DECIDED")
    if new_stage not in valid_stages:
        raise ValueError(f"Invalid lifecycle stage: {new_stage}")

    execute_db(
        "UPDATE tenders SET lifecycle_stage = ? WHERE id = ?",
        (new_stage, tender_id)
    )

def create_corrigendum(tender_id, officer_id, reason, updated_description=None, updated_turnover=None, updated_local_content=None):
    """
    Publishes a corrigendum creating a new tender version.
    Preserves all historical tender rows, snapshots, and previous requirements.
    """
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender:
        raise ValueError("Tender not found.")

    current_ver = tender["tender_version"] or "v1"
    match = re.search(r"v(\d+)", current_ver)
    next_num = int(match.group(1)) + 1 if match else 2
    new_version_tag = f"v{next_num}"

    # Record version
    changes = []
    if updated_description and updated_description != tender["description"]:
        changes.append(f"Description updated: {updated_description[:50]}...")
    if updated_turnover and float(updated_turnover) != tender["min_turnover"]:
        changes.append(f"Turnover threshold updated to INR {float(updated_turnover):,.2f}")
    if updated_local_content and float(updated_local_content) != tender["min_local_content"]:
        changes.append(f"Local content threshold updated to {float(updated_local_content)}%")

    changes_summary = "; ".join(changes) if changes else "Corrigendum published with tender clause adjustments."

    version_id = execute_db(
        """
        INSERT INTO tender_versions (
            tender_id, version_tag, corrigendum_reason, changes_summary, officer_id
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (tender_id, new_version_tag, reason, changes_summary, officer_id)
    )

    # Update current tender
    execute_db(
        """
        UPDATE tenders SET
            tender_version = ?,
            description = COALESCE(?, description),
            min_turnover = COALESCE(?, min_turnover),
            min_local_content = COALESCE(?, min_local_content)
        WHERE id = ?
        """,
        (
            new_version_tag,
            updated_description,
            float(updated_turnover) if updated_turnover else None,
            float(updated_local_content) if updated_local_content else None,
            tender_id
        )
    )

    # Re-associate/copy requirements under new version tag
    old_reqs = query_db("SELECT * FROM requirements WHERE tender_id = ?", (tender_id,))
    for req in old_reqs:
        t_val = req["threshold_value"]
        if req["code"] == "REQ_TURNOVER" and updated_turnover:
            t_val = float(updated_turnover)
        elif req["code"] == "REQ_MII" and updated_local_content:
            t_val = float(updated_local_content)

        execute_db(
            """
            INSERT INTO requirements (
                tender_id, tender_version_id, code, title, description,
                requirement_type, is_mandatory, threshold_value, threshold_unit, expected_doc_types
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                tender_id, version_id, req["code"], req["title"], req["description"],
                req["requirement_type"], req["is_mandatory"], t_val, req["threshold_unit"], req["expected_doc_types"]
            )
        )

    return new_version_tag
