import json
import re
import hashlib

from database.db import execute_db, query_db
from services.pdf_service import extract_pdf_content
from services.llm_service import extract_tender_requirements_llm

REQUIREMENT_PATTERNS = [
    ("REQ_GST", "GST registration and returns", "STATUTORY", r"\bGST(?:IN)?\b|goods\s+and\s+services\s+tax|GSTR[- ]?[13]B?"),
    ("REQ_PAN", "PAN and income tax compliance", "STATUTORY", r"permanent\s+account\s+number|\bPAN\b|income\s+tax\s+return"),
    ("REQ_UDYAM", "Udyam / MSME registration", "STATUTORY", r"\bUDYAM\b|\bMSME\b|micro,?\s+small\s+and\s+medium"),
    ("REQ_TURNOVER", "Minimum annual turnover", "FINANCIAL", r"annual\s+turnover|average\s+turnover|minimum\s+turnover"),
    ("REQ_NET_WORTH", "Minimum net worth", "FINANCIAL", r"net\s+worth"),
    ("REQ_EXPERIENCE", "Past project experience", "TECHNICAL", r"past\s+(?:project|similar)\s+experience|prior\s+experience|experience\s+of\s+at\s+least"),
    ("REQ_PROJECT_COUNT", "Completed project count", "TECHNICAL", r"(?:minimum|required)\s+\d+\s+(?:completed\s+)?projects?"),
    ("REQ_OEM", "OEM manufacturer authorization", "TECHNICAL", r"OEM\s+authorization|manufacturer(?:'s)?\s+authorization|MAF"),
    ("REQ_BIS", "BIS / CRS certification", "TECHNICAL", r"\bBIS\b|Bureau\s+of\s+Indian\s+Standards|\bCRS\b|ISI\s+license"),
    ("REQ_MII", "Make in India local content", "STATUTORY", r"local\s+content|Make\s+in\s+India|Class[- ]I\s+local\s+supplier"),
    ("REQ_STARTUP", "Startup India recognition", "STATUTORY", r"Startup\s+India|DPIIT|DIPP\s+recognition"),
    ("REQ_NSIC", "NSIC registration", "STATUTORY", r"\bNSIC\b|National\s+Small\s+Industries\s+Corporation"),
    ("REQ_EPFO", "EPFO compliance", "STATUTORY", r"\bEPFO\b|provident\s+fund|employee\s+provident"),
    ("REQ_ESIC", "ESIC compliance", "STATUTORY", r"\bESIC\b|Employees'?\s+State\s+Insurance"),
    ("REQ_BLACKLIST", "Non-blacklisting and non-debarment", "STATUTORY", r"blacklist|debar|debarment|non[- ]?blacklisting"),
]

DOCUMENTS_BY_CODE = {
    "REQ_GST": ["GST_CERTIFICATE", "GST_RETURN"],
    "REQ_PAN": ["PAN_CARD", "ITR"],
    "REQ_UDYAM": ["UDYAM_CERTIFICATE"],
    "REQ_TURNOVER": ["ITR", "BALANCE_SHEET"],
    "REQ_NET_WORTH": ["NET_WORTH_CERTIFICATE", "BALANCE_SHEET"],
    "REQ_EXPERIENCE": ["EXPERIENCE_CERTIFICATE", "WORK_ORDER", "COMPLETION_CERTIFICATE"],
    "REQ_PROJECT_COUNT": ["EXPERIENCE_CERTIFICATE", "COMPLETION_CERTIFICATE"],
    "REQ_OEM": ["OEM_AUTHORIZATION"],
    "REQ_BIS": ["BIS_CERTIFICATE", "BIS_LICENSE"],
    "REQ_MII": ["LOCAL_CONTENT_DECLARATION", "MII_DECLARATION"],
    "REQ_STARTUP": ["STARTUP_CERTIFICATE"],
    "REQ_NSIC": ["NSIC_CERTIFICATE"],
    "REQ_EPFO": ["EPFO_CHALLAN", "EPFO_RETURN"],
    "REQ_ESIC": ["ESIC_CHALLAN", "ESIC_RETURN"],
    "REQ_BLACKLIST": ["DEBARMENT_DECLARATION"],
}


def _code_for_text(text):
    for code, _, _, pattern in REQUIREMENT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return code
    return None


def _threshold(text):
    match = re.search(
        r"(?:minimum|at\s+least|not\s+less\s+than)(?:[^0-9%]{0,80})\s*(?:INR|Rs\.?|₹)?\s*([0-9][0-9,]*(?:\.\d+)?)\s*(crore|crores|cr|lakh|lakhs|%)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None
    value = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "INR").upper()
    if unit in ("CRORE", "CRORES", "CR"):
        value *= 10000000
        unit = "INR"
    elif unit in ("LAKH", "LAKHS"):
        value *= 100000
        unit = "INR"
    return value, unit


def _mandatory(text, requirement_type):
    lowered = text.lower()
    if re.search(r"optional|may\s+be\s+submitted|where\s+applicable", lowered):
        return False
    if requirement_type in ("STATUTORY", "FINANCIAL"):
        return True
    return bool(re.search(r"must|shall|required|mandatory|eligible", lowered))


def _normalise(item, default_page=None, default_source="DETERMINISTIC_PARSER"):
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or item.get("source_clause") or title).strip()
    source_clause = str(item.get("source_clause") or description).strip()
    code = item.get("code") or _code_for_text(f"{title} {description}")
    if not title or not code or not source_clause:
        return None

    pattern_match = next((p for p in REQUIREMENT_PATTERNS if p[0] == code), None)
    requirement_type = item.get("requirement_type") or (pattern_match[2] if pattern_match else "DOCUMENTARY")
    if requirement_type not in ("STATUTORY", "FINANCIAL", "TECHNICAL", "DOCUMENTARY"):
        requirement_type = "DOCUMENTARY"

    page = item.get("source_page", default_page)
    try:
        page = int(page) if page is not None else None
    except (TypeError, ValueError):
        page = None
    try:
        confidence = max(0.0, min(1.0, float(item.get("confidence", 0.75))))
    except (TypeError, ValueError):
        confidence = 0.75

    threshold = item.get("threshold")
    unit = item.get("unit")
    if threshold is None and code in ("REQ_TURNOVER", "REQ_NET_WORTH", "REQ_PROJECT_COUNT", "REQ_MII"):
        threshold, unit = _threshold(source_clause)

    return {
        "code": code,
        "title": title,
        "description": description,
        "requirement_type": requirement_type,
        "mandatory": bool(item.get("mandatory", _mandatory(source_clause, requirement_type))),
        "threshold": threshold,
        "unit": unit,
        "financial_years": item.get("financial_years") or [],
        "required_documents": item.get("required_documents") or DOCUMENTS_BY_CODE.get(code, []),
        "applicability_conditions": item.get("applicability_conditions") or [],
        "exemption_conditions": item.get("exemption_conditions") or [],
        "source_clause": source_clause,
        "source_page": page,
        "confidence": confidence,
        "extraction_source": item.get("_source") or default_source,
    }

def _requirement_fingerprint(item):
    identity = "|".join([
        item["code"], item["title"].strip().lower(), item["description"].strip().lower(),
        str(item["threshold"]), str(item["unit"]), json.dumps(item["financial_years"], sort_keys=True),
    ])
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def extract_tender_requirements_from_pages(pages, use_ai=True):
    """Return deduplicated, source-grounded normalized requirements from PDF pages."""
    requirements = {}
    if use_ai:
        for item in extract_tender_requirements_llm(pages):
            normalized = _normalise(item, default_source="GEMINI_TENDER_EXTRACTION")
            if normalized:
                requirements[_requirement_fingerprint(normalized)] = normalized

    for index, page in enumerate(pages or [], start=1):
        text = (page.get("text") or "").strip()
        if not text:
            continue
        for code, title, requirement_type, pattern in REQUIREMENT_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                continue
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            clause = next((line for line in lines if re.search(pattern, line, re.IGNORECASE)), text[:600])
            if clause == text[:600]:
                sentences = re.split(r"(?<=[.!?])\s+", text)
                clause = next((sentence.strip() for sentence in sentences if re.search(pattern, sentence, re.IGNORECASE)), clause)
            normalized = _normalise(
                {
                    "code": code,
                    "title": title,
                    "description": clause,
                    "requirement_type": requirement_type,
                    "source_clause": clause,
                    "source_page": page.get("page_num", index),
                    "confidence": 0.86,
                },
                default_page=page.get("page_num", index),
            )
            if normalized:
                requirements.setdefault(_requirement_fingerprint(normalized), normalized)

    return list(requirements.values())


def extract_and_persist_tender_requirements(tender_id, use_ai=True):
    """Extract source-grounded requirements and persist them without deleting templates."""
    tender = query_db("SELECT id, pdf_storage_path FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender or not tender["pdf_storage_path"]:
        return {"status": "FALLBACK_TEMPLATES", "requirements": [], "reason": "Tender PDF is not available."}

    pdf_result = extract_pdf_content(tender["pdf_storage_path"])
    requirements = extract_tender_requirements_from_pages(pdf_result.get("pages", []), use_ai=use_ai)
    if not requirements:
        return {
            "status": "FALLBACK_TEMPLATES",
            "requirements": [],
            "reason": pdf_result.get("error") or "No source-grounded requirements were extracted.",
        }

    version = query_db(
        "SELECT id FROM tender_versions WHERE tender_id = ? ORDER BY id DESC LIMIT 1",
        (tender_id,),
        one=True,
    )
    version_id = version["id"] if version else None
    persisted = []
    for item in requirements:
        fingerprint = _requirement_fingerprint(item)
        criteria = {
            "financial_years": item["financial_years"],
            "applicability_conditions": item["applicability_conditions"],
            "exemption_conditions": item["exemption_conditions"],
        }
        existing = query_db(
            """SELECT id FROM requirements WHERE tender_id = ? AND tender_version_id IS ?
               AND (requirement_fingerprint = ? OR (requirement_fingerprint IS NULL AND source_clause IS NULL))
               AND code = ? ORDER BY id DESC LIMIT 1""",
            (tender_id, version_id, fingerprint, item["code"]),
            one=True,
        )
        values = (
            item["title"], item["description"], item["requirement_type"],
            1 if item["mandatory"] else 0, item["threshold"], item["unit"],
            json.dumps(item["required_documents"]), json.dumps(criteria),
            item["source_clause"], item["source_page"], item["confidence"],
            item["extraction_source"], json.dumps(item["applicability_conditions"]),
            json.dumps(item["exemption_conditions"]), json.dumps(item), fingerprint,
        )
        if existing:
            execute_db(
                """UPDATE requirements SET title = ?, description = ?, requirement_type = ?,
                   is_mandatory = ?, threshold_value = ?, threshold_unit = ?, expected_doc_types = ?,
                   structured_criteria = ?, source_clause = ?, source_page = ?, extraction_confidence = ?,
                   extraction_source = ?, applicability_conditions = ?, exemption_conditions = ?,
                   original_ai_output = ?, requirement_fingerprint = ?, review_status = 'PENDING', review_version = COALESCE(review_version, 0) + 1
                   WHERE id = ?""",
                values + (existing["id"],),
            )
            requirement_id = existing["id"]
        else:
            requirement_id = execute_db(
                """INSERT INTO requirements (
                   tender_id, tender_version_id, code, title, description, requirement_type,
                   is_mandatory, threshold_value, threshold_unit, expected_doc_types,
                   structured_criteria, source_clause, source_page, extraction_confidence,
                   extraction_source, applicability_conditions, exemption_conditions,
                         original_ai_output, review_status, requirement_fingerprint
                     ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)""",
                     (tender_id, version_id, item["code"]) + values,
            )
        persisted.append({"id": requirement_id, **item})

    return {"status": "EXTRACTED", "requirements": persisted, "pdf": pdf_result}
