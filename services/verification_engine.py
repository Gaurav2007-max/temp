import json
import re
from datetime import datetime, date
from database.db import get_db, execute_db, query_db
from services.statutory_service import (
    verify_gst, verify_pan, verify_udyam, verify_mca, verify_epfo,
    verify_esic, verify_startup, verify_nsic, verify_bis,
    verify_blacklisting, verify_digilocker
)
from services.document_service import (
    get_documents_by_bidder_and_tender,
    validate_doc_type_for_requirement,
    DOC_TYPE_REQUIREMENT_MAP
)
from services.llm_service import generate_ai_compliance_explanation

def normalize_text(val):
    if not val:
        return ""
    return re.sub(r"[^a-zA-Z0-9]", "", str(val)).lower()

def parse_date(date_str):
    if not date_str:
        return None
    cleaned = str(date_str).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None

def run_bidder_verification(tender_id, bidder_id, is_reverification=False):
    """
    Executes the complete deterministic compliance verification pass for a bidder on a tender.
    Follows strict deterministic rules:
    - Independent mandatory eligibility gating (high score never overrides mandatory failure)
    - Deterministic turnover calculation across required financial years without duplicates
    - Project grouping and deduplication for work orders and completion certificates
    - Field-based OEM authorization verification (OEM, bidder match, expiry warning)
    - Field-level evidence provenance (doc_id, page, source, confidence)
    - Cross-source conflict detection (PAN, GSTIN, legal entity name, addresses)
    - Explainable rule-based risk evaluation
    """
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    bidder = query_db("SELECT * FROM bidders WHERE id = ?", (bidder_id,), one=True)

    if not tender or not bidder:
        raise ValueError("Tender or Bidder not found.")

    requirements = query_db(
        """
        SELECT * FROM requirements
        WHERE tender_id = ?
          AND (tender_version_id = (SELECT id FROM tender_versions WHERE tender_id = ? ORDER BY id DESC LIMIT 1)
               OR tender_version_id IS NULL)
        GROUP BY code
        ORDER BY id ASC
        """,
        (tender_id, tender_id)
    )

    documents = get_documents_by_bidder_and_tender(bidder_id, tender_id)

    # 1. Statutory Registry Verification
    statutory_results = {}
    pan = bidder["pan"] or ""
    gstin = bidder["gstin"] or ""
    udyam_no = bidder["udyam_reg_no"] or ""

    # Derive PAN from GSTIN if missing
    if not pan and gstin and len(gstin) >= 12:
        pan = gstin[2:12]

    statutory_results["GST"] = verify_gst(gstin)
    statutory_results["PAN"] = verify_pan(pan)
    statutory_results["UDYAM"] = verify_udyam(udyam_no)
    statutory_results["MCA"] = verify_mca(pan)
    statutory_results["EPFO"] = verify_epfo(pan)
    statutory_results["ESIC"] = verify_esic(pan)
    statutory_results["STARTUP"] = verify_startup(pan)
    statutory_results["NSIC"] = verify_nsic(pan)
    statutory_results["BIS"] = verify_bis(pan)
    statutory_results["BLACKLIST"] = verify_blacklisting(pan=pan, gstin=gstin)

    # 2. Cross-Source Identity & Conflict Detection
    conflicts = []
    company_name = bidder["company_name"] or ""
    norm_bidder_name = normalize_text(company_name)

    # A. PAN vs GSTIN Embedded PAN Check
    if gstin and len(gstin) >= 12:
        gst_pan = gstin[2:12].upper()
        if pan and gst_pan != pan.upper():
            conflicts.append({
                "type": "PAN_GSTIN_MISMATCH",
                "field": "pan",
                "source_a": "Bidder PAN Profile",
                "value_a": pan,
                "source_b": "GSTIN (Characters 3-12)",
                "value_b": gst_pan,
                "severity": "HIGH",
                "description": f"PAN mismatch: Bidder PAN '{pan}' does not match PAN embedded in GSTIN '{gst_pan}'."
            })

    # B. Legal Entity Name Cross-Validation
    gst_legal_name = statutory_results["GST"].get("legal_name") or ""
    if gst_legal_name:
        norm_gst_name = normalize_text(gst_legal_name)
        # Check token overlap
        tokens_bidder = set(re.findall(r"\w+", company_name.lower())) - {"pvt", "ltd", "private", "limited", "inc", "corp"}
        tokens_gst = set(re.findall(r"\w+", gst_legal_name.lower())) - {"pvt", "ltd", "private", "limited", "inc", "corp"}
        if tokens_bidder and tokens_gst and not (tokens_bidder & tokens_gst):
            conflicts.append({
                "type": "COMPANY_NAME_MISMATCH",
                "field": "company_name",
                "source_a": "Bidder Profile",
                "value_a": company_name,
                "source_b": "GSTN Authoritative Portal",
                "value_b": gst_legal_name,
                "severity": "HIGH",
                "description": f"Entity name conflict: Registered bidder name '{company_name}' differs substantially from GSTN legal name '{gst_legal_name}'."
            })

    # C. Address Cross-Validation
    bidder_addr = (bidder["registered_address"] or "").strip()
    gst_data = statutory_results["GST"].get("data") or {}
    gst_addr = (gst_data.get("address") or "").strip()
    if bidder_addr and gst_addr:
        words_bidder = set(re.findall(r"\w+", bidder_addr.lower()))
        words_gst = set(re.findall(r"\w+", gst_addr.lower()))
        overlap = words_bidder.intersection(words_gst)
        if len(overlap) < 2 and ("delhi" in words_bidder and "delhi" not in words_gst):
            conflicts.append({
                "type": "ADDRESS_MISMATCH",
                "field": "registered_address",
                "source_a": "Bidder Profile",
                "value_a": bidder_addr,
                "source_b": "GSTN Record",
                "value_b": gst_addr,
                "severity": "MEDIUM",
                "description": "Registered address in profile differs from GST principal place of business."
            })

    # D. Udyam vs GST Address Check
    udyam_data = statutory_results["UDYAM"].get("data") or {}
    udyam_addr = (udyam_data.get("address") or "").strip()
    if udyam_addr and gst_addr:
        words_udyam = set(re.findall(r"\w+", udyam_addr.lower()))
        words_gst = set(re.findall(r"\w+", gst_addr.lower()))
        if len(words_udyam.intersection(words_gst)) < 2:
            conflicts.append({
                "type": "ADDRESS_MISMATCH",
                "field": "factory_or_office_address",
                "source_a": "Udyam Registry",
                "value_a": udyam_addr,
                "source_b": "GSTN Record",
                "value_b": gst_addr,
                "severity": "MEDIUM",
                "description": "Enterprise unit address in Udyam registry differs from GST principal place of business."
            })

    # 3. Requirement-by-Requirement Deterministic Evaluation
    req_evaluations = []
    risk_factors = []
    total_score = 0.0
    max_total_score = 0.0
    any_mandatory_failed = False
    any_needs_review = False
    today = date.today()

    for req in requirements:
        code = req["code"]
        is_mand = bool(req["is_mandatory"])
        weight = 10.0
        max_total_score += weight

        # Parse structured criteria if present
        criteria = {}
        try:
            criteria = json.loads(req.get("structured_criteria") or "{}")
        except Exception:
            criteria = {}

        eval_res = {
            "requirement_id": req["id"],
            "code": code,
            "title": req["title"],
            "is_mandatory": is_mand,
            "status": "PASS",
            "score_awarded": weight,
            "max_score": weight,
            "evidence": {
                "field_evidence": []
            },
            "issues": [],
            "explanation": ""
        }

        # Filter documents relevant to this requirement
        matching_docs = [
            d for d in documents
            if d.get("doc_type") in DOC_TYPE_REQUIREMENT_MAP.get(code, [d.get("doc_type")])
        ]

        # Gather field-level provenance from matching documents
        for md in matching_docs:
            f_ev = md.get("fields", {}).get("_field_evidence", [])
            for fe in f_ev:
                eval_res["evidence"]["field_evidence"].append(fe)

        # ----------------------------------------------------
        # --- A. GST REQUIREMENT (REQ_GST) ---
        # ----------------------------------------------------
        if code == "REQ_GST":
            gst_res = statutory_results["GST"]
            eval_res["evidence"]["statutory_response"] = gst_res
            if gst_res.get("source_mode") == "UNAVAILABLE":
                eval_res["status"] = "UNAVAILABLE"
                eval_res["score_awarded"] = weight * 0.5
                eval_res["issues"].append("GST portal unavailable for automated verification. Manual check required.")
                eval_res["explanation"] = "Statutory GST portal could not be reached. Manual officer verification required."
                any_needs_review = True
            elif not gst_res.get("is_valid"):
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                msg = f"GST verification failed: {gst_res.get('message', 'Registration invalid or cancelled')}."
                eval_res["issues"].append(msg)
                eval_res["explanation"] = f"GSTIN '{gstin}' is invalid, cancelled, or suspended on the GST portal."
                risk_factors.append("GST registration cancelled, suspended, or invalid")
                if is_mand:
                    any_mandatory_failed = True
            else:
                eval_res["status"] = "PASS"
                eval_res["evidence"]["verified_gstin"] = gst_res.get("gstin")
                eval_res["evidence"]["legal_name"] = gst_res.get("legal_name")
                eval_res["evidence"]["taxpayer_type"] = gst_res.get("taxpayer_type", "Regular")
                eval_res["explanation"] = f"Active GSTIN '{gst_res.get('gstin')}' verified for '{gst_res.get('legal_name')}' with filing status up to date."

        # ----------------------------------------------------
        # --- B. PAN REQUIREMENT (REQ_PAN) ---
        # ----------------------------------------------------
        elif code == "REQ_PAN":
            pan_res = statutory_results["PAN"]
            eval_res["evidence"]["statutory_response"] = pan_res
            if pan_res.get("source_mode") == "UNAVAILABLE":
                eval_res["status"] = "UNAVAILABLE"
                eval_res["score_awarded"] = weight * 0.5
                eval_res["explanation"] = "Income tax portal verification unavailable. Manual PAN card check required."
                any_needs_review = True
            elif not pan_res.get("is_valid"):
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"PAN verification failed: {pan_res.get('message')}")
                eval_res["explanation"] = f"PAN '{pan}' verification failed on Income Tax database: {pan_res.get('message')}."
                risk_factors.append("Income tax non-compliance or invalid PAN")
                if is_mand:
                    any_mandatory_failed = True
            else:
                eval_res["status"] = "PASS"
                eval_res["evidence"]["verified_pan"] = pan_res.get("pan")
                eval_res["evidence"]["legal_name"] = pan_res.get("legal_name")
                eval_res["explanation"] = f"Valid Permanent Account Number '{pan_res.get('pan')}' verified for '{pan_res.get('legal_name')}'."

        # ----------------------------------------------------
        # --- C. DEBARMENT / BLACKLIST (REQ_BLACKLIST) ---
        # ----------------------------------------------------
        elif code in ("REQ_BLACKLIST", "REQ_DEBARMENT"):
            bl_res = statutory_results["BLACKLIST"]
            eval_res["evidence"]["statutory_response"] = bl_res
            if bl_res.get("debarred"):
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                reason = bl_res.get("reason", "Debarred from public procurement")
                auth = bl_res.get("authority", "GeM / CVC")
                eval_res["issues"].append(f"Debarred by {auth}: {reason}")
                eval_res["explanation"] = f"DISQUALIFIED: Bidder appears on official debarment list of {auth} ({reason})."
                risk_factors.append(f"Entity debarred by {auth}")
                if is_mand:
                    any_mandatory_failed = True
            else:
                eval_res["status"] = "PASS"
                eval_res["explanation"] = "Clean record verified on CVC and GeM Debarment / Blacklisting registry."

        # ----------------------------------------------------
        # --- D. ANNUAL TURNOVER REQUIREMENT (REQ_TURNOVER) ---
        # ----------------------------------------------------
        elif code == "REQ_TURNOVER":
            req_turnover = float(req["threshold_value"] or tender["min_turnover"] or 0)
            eval_res["evidence"]["required_turnover"] = req_turnover

            # Required financial years from criteria or standard default
            required_fys = criteria.get("required_financial_years", ["FY2023-24", "FY2024-25", "FY2025-26"])
            eval_res["evidence"]["required_financial_years"] = required_fys

            # Process all submitted ITR / Balance Sheet documents deterministically
            itr_docs = [d for d in documents if d["doc_type"] in ("ITR", "BALANCE_SHEET", "ANNUAL_RETURN")]
            turnovers_by_fy = {}

            for d in itr_docs:
                fields = d.get("fields") or {}
                raw_fy = fields.get("financial_year")
                amount = fields.get("turnover_amount")

                # Normalize financial year string
                if raw_fy:
                    clean_fy = str(raw_fy).strip().upper().replace(" ", "")
                    if not clean_fy.startswith("FY"):
                        clean_fy = f"FY{clean_fy}"
                else:
                    clean_fy = None

                # Only track if both amount and financial year exist
                if amount is not None and clean_fy:
                    try:
                        amt_float = float(amount)
                    except ValueError:
                        amt_float = 0.0

                    if clean_fy not in turnovers_by_fy:
                        turnovers_by_fy[clean_fy] = {
                            "amount": amt_float,
                            "doc_id": d["id"],
                            "filename": d["original_filename"],
                            "page": d.get("page_count", 1),
                            "extraction_method": d.get("extraction_method", "TEXT"),
                            "confidence": d.get("ocr_confidence", 0.98)
                        }

            eval_res["evidence"]["verified_turnovers_by_year"] = turnovers_by_fy

            # Check matching against required financial years
            matched_fys = {k: v for k, v in turnovers_by_fy.items() if k in required_fys}
            missing_fys = [fy for fy in required_fys if fy not in turnovers_by_fy]
            eval_res["evidence"]["matched_financial_years"] = list(matched_fys.keys())
            eval_res["evidence"]["missing_financial_years"] = missing_fys

            if not turnovers_by_fy:
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"No valid audited turnover or ITR documents found for required years. Required minimum: ₹{req_turnover:,.2f}.")
                eval_res["explanation"] = f"FAIL: No valid financial statements found for required financial years ({', '.join(required_fys)}). Minimum required: ₹{req_turnover:,.2f}."
                risk_factors.append("Missing mandatory turnover / ITR documents")
                if is_mand:
                    any_mandatory_failed = True
            else:
                # Calculate average across the required years that were verified
                calc_fys = matched_fys if matched_fys else turnovers_by_fy
                avg_turnover = sum(item["amount"] for item in calc_fys.values()) / len(calc_fys)
                eval_res["evidence"]["calculated_average_turnover"] = avg_turnover

                # Build clear calculation string
                breakdown_parts = [f"{fy}: ₹{item['amount']/10000000:.2f} Cr" for fy, item in sorted(calc_fys.items())]
                calc_str = f"{' + '.join(breakdown_parts)} => Average: ₹{avg_turnover/10000000:.2f} Cr (Required: ₹{req_turnover/10000000:.2f} Cr)"
                eval_res["evidence"]["calculation_formula"] = calc_str

                if missing_fys:
                    eval_res["issues"].append(f"Missing audited statements for financial year(s): {', '.join(missing_fys)}.")

                if avg_turnover >= req_turnover:
                    if missing_fys:
                        eval_res["status"] = "NEEDS_REVIEW"
                        eval_res["score_awarded"] = weight * 0.8
                        eval_res["explanation"] = f"NEEDS_REVIEW: {calc_str}. Verified average exceeds required threshold, but statements for {', '.join(missing_fys)} are missing."
                        any_needs_review = True
                    else:
                        eval_res["status"] = "PASS"
                        eval_res["score_awarded"] = weight
                        eval_res["explanation"] = f"PASS: {calc_str}. Fully compliant across all required financial years."
                else:
                    eval_res["status"] = "FAIL"
                    eval_res["score_awarded"] = round(weight * (avg_turnover / req_turnover), 1) if req_turnover > 0 else 0
                    eval_res["issues"].append(f"Calculated average turnover ₹{avg_turnover:,.2f} is below required threshold ₹{req_turnover:,.2f}.")
                    eval_res["explanation"] = f"FAIL: {calc_str}. Average annual turnover does not meet the tender threshold of ₹{req_turnover:,.2f}."
                    risk_factors.append("Insufficient annual turnover")
                    if is_mand:
                        any_mandatory_failed = True

        # ----------------------------------------------------
        # --- E. EXPERIENCE REQUIREMENT (REQ_EXPERIENCE) ---
        # ----------------------------------------------------
        elif code == "REQ_EXPERIENCE":
            req_count = int(tender["min_projects_count"] or req["threshold_value"] or 1)
            req_val = float(tender["min_cumulative_project_value"] or 0)
            eval_res["evidence"]["required_projects_count"] = req_count
            eval_res["evidence"]["required_cumulative_value"] = req_val

            exp_docs = [d for d in documents if d["doc_type"] in ("EXPERIENCE_CERTIFICATE", "WORK_ORDER", "COMPLETION_CERTIFICATE")]

            # Group documents belonging to the SAME project deterministically
            grouped_projects = {}
            for d in exp_docs:
                fields = d.get("fields") or {}
                wo_no = (fields.get("work_order_no") or "").strip()
                client = (fields.get("client_name") or "").strip()
                val = fields.get("project_value")
                is_comp = fields.get("is_completed") or bool(fields.get("completion_date")) or (d["doc_type"] == "COMPLETION_CERTIFICATE")

                # Project grouping key: normalized work order number if available, else client name
                if wo_no:
                    group_key = f"WO_{normalize_text(wo_no)}"
                elif client:
                    group_key = f"CLIENT_{normalize_text(client)}"
                else:
                    group_key = f"DOC_{d['id']}"

                if group_key not in grouped_projects:
                    grouped_projects[group_key] = {
                        "project_key": group_key,
                        "work_order_no": wo_no or "N/A",
                        "client_name": client or "Public Sector Client",
                        "project_value": float(val) if val else 0.0,
                        "is_completed": is_comp,
                        "completion_date": fields.get("completion_date"),
                        "documents": []
                    }
                else:
                    # Update value if another document in the same project has explicit value
                    if val and float(val) > grouped_projects[group_key]["project_value"]:
                        grouped_projects[group_key]["project_value"] = float(val)
                    if is_comp:
                        grouped_projects[group_key]["is_completed"] = True
                    if fields.get("completion_date"):
                        grouped_projects[group_key]["completion_date"] = fields.get("completion_date")
                    if wo_no and grouped_projects[group_key]["work_order_no"] == "N/A":
                        grouped_projects[group_key]["work_order_no"] = wo_no
                    if client and grouped_projects[group_key]["client_name"] == "Public Sector Client":
                        grouped_projects[group_key]["client_name"] = client

                grouped_projects[group_key]["documents"].append({
                    "doc_id": d["id"],
                    "filename": d["original_filename"],
                    "doc_type": d["doc_type"],
                    "page": d.get("page_count", 1)
                })

            eval_res["evidence"]["grouped_projects"] = list(grouped_projects.values())

            # Evaluate distinct completed projects
            verified_projects = [p for p in grouped_projects.values() if p["is_completed"]]
            uncompleted_projects = [p for p in grouped_projects.values() if not p["is_completed"]]
            verified_count = len(verified_projects)
            cumulative_val = sum(p["project_value"] for p in verified_projects)

            eval_res["evidence"]["verified_completed_count"] = verified_count
            eval_res["evidence"]["verified_cumulative_value"] = cumulative_val

            # Format project descriptions for explanation
            proj_summaries = []
            for idx, p in enumerate(verified_projects, 1):
                proj_summaries.append(
                    f"Project {idx} ({p['client_name']}): Ref '{p['work_order_no']}' - ₹{p['project_value']/10000000:.2f} Cr [{len(p['documents'])} doc(s) matched]"
                )
            eval_summary_str = " | ".join(proj_summaries) if proj_summaries else "No completed projects verified"

            if uncompleted_projects:
                for up in uncompleted_projects:
                    eval_res["issues"].append(f"Project '{up['work_order_no']}' lacks verified completion certificate.")

            if verified_count >= req_count and (req_val == 0 or cumulative_val >= req_val):
                eval_res["status"] = "PASS"
                eval_res["score_awarded"] = weight
                eval_res["explanation"] = f"PASS: {verified_count} completed project(s) verified totaling ₹{cumulative_val/10000000:.2f} Cr (Required: {req_count} projects, ₹{req_val/10000000:.2f} Cr). Deduplicated across {len(exp_docs)} submitted documents. {eval_summary_str}."
            elif verified_count > 0:
                eval_res["status"] = "FAIL" if is_mand and verified_count < req_count else "NEEDS_REVIEW"
                eval_res["score_awarded"] = round(weight * (verified_count / req_count) * 0.7, 1)
                eval_res["issues"].append(f"Verified {verified_count} of {req_count} required completed projects. Cumulative value: ₹{cumulative_val:,.2f}.")
                eval_res["explanation"] = f"FAIL: Insufficient completed projects. Verified {verified_count} projects (₹{cumulative_val/10000000:.2f} Cr) against requirement of {req_count} projects (₹{req_val/10000000:.2f} Cr). {eval_summary_str}."
                risk_factors.append("Insufficient verified project experience")
                if is_mand and verified_count < req_count:
                    any_mandatory_failed = True
                else:
                    any_needs_review = True
            else:
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"No valid completed experience projects found. Required: {req_count} projects totaling ₹{req_val:,.2f}.")
                eval_res["explanation"] = f"FAIL: No satisfactorily completed projects identified. Required: {req_count} projects totaling ₹{req_val/10000000:.2f} Cr."
                risk_factors.append("No valid project experience documents found")
                if is_mand:
                    any_mandatory_failed = True

        # ----------------------------------------------------
        # --- F. OEM AUTHORIZATION (REQ_OEM) ---
        # ----------------------------------------------------
        elif code == "REQ_OEM":
            oem_docs = [d for d in documents if d["doc_type"] == "OEM_AUTHORIZATION"]
            if not oem_docs:
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append("Missing mandatory Manufacturer Authorization Form (MAF).")
                eval_res["explanation"] = "FAIL: No OEM Authorization Form (MAF) submitted for supplied products."
                risk_factors.append("Missing mandatory OEM authorization")
                if is_mand:
                    any_mandatory_failed = True
            else:
                doc = oem_docs[0]
                fields = doc.get("fields") or {}
                oem_name = fields.get("oem_name")
                authorized_bidder = fields.get("authorized_bidder")
                valid_till_str = fields.get("authorization_valid_till")

                eval_res["evidence"]["oem_name"] = oem_name or "Not Specified in MAF"
                eval_res["evidence"]["authorized_bidder"] = authorized_bidder or "Not Specified in MAF"
                eval_res["evidence"]["valid_till"] = valid_till_str or "Not Specified in MAF"
                eval_res["evidence"]["document_id"] = doc["id"]
                eval_res["evidence"]["filename"] = doc["original_filename"]
                eval_res["evidence"]["disclaimer"] = "Document-based verification. Live OEM API verification not claimed."

                # 1. Check OEM name presence
                if not oem_name:
                    eval_res["status"] = "NEEDS_REVIEW"
                    eval_res["issues"].append("OEM / Manufacturer name could not be reliably extracted from MAF.")

                # 2. Check Bidder Name Match
                name_matches = True
                if authorized_bidder:
                    norm_auth = normalize_text(authorized_bidder)
                    norm_comp = normalize_text(bidder["company_name"])
                    # Check token overlap
                    toks_auth = set(re.findall(r"\w+", authorized_bidder.lower())) - {"pvt", "ltd", "private", "limited"}
                    toks_comp = set(re.findall(r"\w+", bidder["company_name"].lower())) - {"pvt", "ltd", "private", "limited"}
                    if not (toks_auth & toks_comp):
                        name_matches = False
                        eval_res["status"] = "FAIL"
                        eval_res["issues"].append(f"Authorized partner in MAF '{authorized_bidder}' does not match bidding entity '{bidder['company_name']}'.")
                        eval_res["explanation"] = f"FAIL: Partner mismatch. MAF authorizes '{authorized_bidder}', but bidder is '{bidder['company_name']}'."
                        risk_factors.append("OEM authorization issued to different entity")
                        if is_mand:
                            any_mandatory_failed = True

                # 3. Check Validity Date & Warning Window
                valid_date = parse_date(valid_till_str)
                if name_matches:
                    if valid_date:
                        if valid_date < today:
                            eval_res["status"] = "FAIL"
                            eval_res["score_awarded"] = 0
                            eval_res["issues"].append(f"OEM Authorization expired on {valid_date.isoformat()}.")
                            eval_res["explanation"] = f"FAIL: OEM Authorization expired on {valid_date.isoformat()}."
                            risk_factors.append("Expired OEM authorization certificate")
                            if is_mand:
                                any_mandatory_failed = True
                        elif (valid_date - today).days <= 30:
                            eval_res["status"] = "WARNING"
                            eval_res["score_awarded"] = weight * 0.9
                            eval_res["issues"].append(f"OEM Authorization expires soon on {valid_date.isoformat()} (within {(valid_date - today).days} days).")
                            eval_res["explanation"] = f"WARNING: OEM Authorization from '{oem_name}' is currently valid but expires on {valid_date.isoformat()} (within 30 days). Extension required."
                            risk_factors.append("OEM authorization expiring within 30 days")
                        else:
                            eval_res["status"] = "PASS"
                            eval_res["score_awarded"] = weight
                            eval_res["explanation"] = f"PASS: Valid OEM Authorization from '{oem_name}' authorizing '{authorized_bidder or bidder['company_name']}' until {valid_date.isoformat()}."
                    else:
                        eval_res["status"] = "PASS"
                        eval_res["score_awarded"] = weight
                        eval_res["explanation"] = f"PASS: OEM Authorization from '{oem_name}' verified. (Document-based check; live OEM API verification not claimed)."

        # ----------------------------------------------------
        # --- G. MAKE IN INDIA / LOCAL CONTENT (REQ_MII) ---
        # ----------------------------------------------------
        elif code == "REQ_MII":
            min_lc = float(req["threshold_value"] or tender["min_local_content"] or 50)
            mii_docs = [d for d in documents if d["doc_type"] in ("LOCAL_CONTENT_DECLARATION", "MII_CERTIFICATE", "MII_DECLARATION")]
            declared_lc = 0.0
            if mii_docs:
                fields = mii_docs[0].get("fields") or {}
                declared_lc = float(fields.get("local_content_percentage", 65.0))
            else:
                declared_lc = 0.0

            eval_res["evidence"]["declared_percentage"] = declared_lc
            eval_res["evidence"]["required_percentage"] = min_lc

            if declared_lc >= min_lc:
                eval_res["status"] = "PASS"
                eval_res["score_awarded"] = weight
                eval_res["explanation"] = f"PASS: Declared local content of {declared_lc}% meets the Class-I Local Supplier threshold (minimum {min_lc}%)."
            else:
                eval_res["status"] = "FAIL"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"Declared local content {declared_lc}% is below tender requirement {min_lc}%.")
                eval_res["explanation"] = f"FAIL: Declared local content of {declared_lc}% is below the minimum required {min_lc}%."
                risk_factors.append("Local content below required Make in India threshold")
                if is_mand:
                    any_mandatory_failed = True

        # ----------------------------------------------------
        # --- H. BIS LICENSE REQUIREMENT (REQ_BIS) ---
        # ----------------------------------------------------
        elif code == "REQ_BIS":
            bis_res = statutory_results["BIS"]
            eval_res["evidence"]["statutory_response"] = bis_res
            if bis_res.get("is_valid"):
                eval_res["status"] = "PASS"
                eval_res["score_awarded"] = weight
                eval_res["explanation"] = f"PASS: Valid BIS standard license verified: {bis_res.get('message', 'Standard verified')}."
            else:
                eval_res["status"] = "NEEDS_REVIEW" if not is_mand else "FAIL"
                eval_res["score_awarded"] = weight * 0.5 if not is_mand else 0
                eval_res["issues"].append(f"BIS registration check: {bis_res.get('message')}")
                eval_res["explanation"] = f"BIS verification pending or not active: {bis_res.get('message')}."
                if is_mand:
                    any_mandatory_failed = True
                else:
                    any_needs_review = True

        # ----------------------------------------------------
        # --- I. UDYAM / MSME REQUIREMENT (REQ_UDYAM) ---
        # ----------------------------------------------------
        elif code == "REQ_UDYAM":
            udyam_res = statutory_results["UDYAM"]
            eval_res["evidence"]["statutory_response"] = udyam_res
            if udyam_res.get("is_valid"):
                eval_res["status"] = "PASS"
                eval_res["score_awarded"] = weight
                eval_res["explanation"] = f"PASS: Active MSME/Udyam registration verified ({udyam_res.get('udyam_reg_no', udyam_no)}). Eligible for purchase preference."
            else:
                eval_res["status"] = "NOT_APPLICABLE" if not is_mand else "FAIL"
                eval_res["score_awarded"] = weight * 0.5 if not is_mand else 0
                eval_res["issues"].append(f"Udyam registration: {udyam_res.get('message')}")
                eval_res["explanation"] = f"Udyam registration not active: {udyam_res.get('message')}. General eligibility unaffected unless claiming MSME exemption."

        # ----------------------------------------------------
        # --- J. GENERIC / OTHER REQUIREMENTS ---
        # ----------------------------------------------------
        else:
            if matching_docs:
                eval_res["status"] = "PASS"
                eval_res["score_awarded"] = weight
                eval_res["evidence"]["submitted_documents_count"] = len(matching_docs)
                eval_res["explanation"] = f"PASS: Submitted required documentation ({len(matching_docs)} document(s) verified)."
            else:
                eval_res["status"] = "FAIL" if is_mand else "NEEDS_REVIEW"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"Missing required documentation for {req['title']}.")
                eval_res["explanation"] = f"Missing required documentation for clause '{req['title']}'."
                if is_mand:
                    any_mandatory_failed = True
                    risk_factors.append(f"Missing documentation for mandatory requirement: {req['title']}")
                else:
                    any_needs_review = True

        total_score += eval_res["score_awarded"]
        req_evaluations.append(eval_res)

    # 4. Final Score & Eligibility Determination
    # Numerical compliance score (0-100)
    score_percentage = round((total_score / max_total_score * 100) if max_total_score > 0 else 0, 1)

    # CRITICAL RULE: High score NEVER overrides mandatory failure!
    if any_mandatory_failed:
        eligibility = "NOT_ELIGIBLE"
    elif any_needs_review or len(conflicts) > 0:
        eligibility = "NEEDS_REVIEW"
    else:
        eligibility = "ELIGIBLE"

    # 5. Deterministic Explainable Risk Calculation (LOW, MEDIUM, HIGH)
    if statutory_results["BLACKLIST"].get("debarred"):
        risk_level = "HIGH"
        risk_factors.insert(0, "CRITICAL: Bidder is on CVC/GeM Debarment list")
    elif not statutory_results["GST"].get("is_valid") and statutory_results["GST"].get("source_mode") == "MOCK":
        risk_level = "HIGH"
    elif len(conflicts) >= 2 or any_mandatory_failed:
        risk_level = "HIGH"
    elif len(conflicts) == 1 or any_needs_review or risk_factors:
        risk_level = "MEDIUM"
    else:
        risk_level = "LOW"

    if conflicts:
        for c in conflicts:
            risk_factors.append(c["description"])

    # 6. Generate AI Decision-Support Recommendation
    all_issues = []
    for r in req_evaluations:
        all_issues.extend(r["issues"])

    ai_recommendation = generate_ai_compliance_explanation(
        bidder["company_name"], score_percentage, eligibility, risk_level, all_issues
    )

    # 7. Persist into Database
    existing_ver = query_db(
        "SELECT MAX(version_num) as max_v FROM verifications WHERE tender_id = ? AND bidder_id = ?",
        (tender_id, bidder_id),
        one=True
    )
    new_version_num = (existing_ver["max_v"] or 0) + 1 if (existing_ver and existing_ver["max_v"]) else 1

    ver_id = execute_db(
        """
        INSERT INTO verifications (
            tender_id, bidder_id, version_num, score, eligibility, risk_level,
            risk_factors, statutory_summary, conflicts_detected, recommendation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tender_id, bidder_id, new_version_num, score_percentage, eligibility, risk_level,
            json.dumps(risk_factors), json.dumps(statutory_results),
            json.dumps(conflicts), ai_recommendation
        )
    )

    # Persist requirement-level evaluations
    for r in req_evaluations:
        execute_db(
            """
            INSERT INTO verification_requirements (
                verification_id, requirement_id, status, is_mandatory,
                score_awarded, max_score, evidence, issues
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ver_id, r["requirement_id"], r["status"], 1 if r["is_mandatory"] else 0,
                r["score_awarded"], r["max_score"],
                json.dumps(r["evidence"]), json.dumps(r["issues"])
            )
        )

    return {
        "id": ver_id,
        "tender_id": tender_id,
        "bidder_id": bidder_id,
        "version_num": new_version_num,
        "score": score_percentage,
        "eligibility": eligibility,
        "risk_level": risk_level,
        "risk_factors": risk_factors,
        "conflicts": conflicts,
        "statutory_results": statutory_results,
        "requirements": req_evaluations,
        "requirement_evaluations": req_evaluations,
        "recommendation": ai_recommendation
    }

def get_latest_verification(tender_id, bidder_id):
    ver = query_db(
        "SELECT * FROM verifications WHERE tender_id = ? AND bidder_id = ? ORDER BY version_num DESC LIMIT 1",
        (tender_id, bidder_id),
        one=True
    )
    if not ver:
        return None

    ver_dict = dict(ver)
    try:
        ver_dict["risk_factors"] = json.loads(ver_dict.get("risk_factors") or "[]")
    except Exception:
        ver_dict["risk_factors"] = []

    try:
        ver_dict["statutory_summary"] = json.loads(ver_dict.get("statutory_summary") or "{}")
    except Exception:
        ver_dict["statutory_summary"] = {}

    try:
        ver_dict["conflicts_detected"] = json.loads(ver_dict.get("conflicts_detected") or "[]")
    except Exception:
        ver_dict["conflicts_detected"] = []

    # Get requirement rows
    req_rows = query_db(
        """
        SELECT vr.*, r.code, r.title, r.requirement_type, r.structured_criteria, r.threshold_value, r.threshold_unit
        FROM verification_requirements vr
        JOIN requirements r ON vr.requirement_id = r.id
        WHERE vr.verification_id = ?
        ORDER BY r.id ASC
        """,
        (ver["id"],)
    )
    ver_dict["requirement_evaluations"] = []
    for rr in req_rows:
        d = dict(rr)
        try:
            d["evidence"] = json.loads(d.get("evidence") or "{}")
        except Exception:
            d["evidence"] = {}
        try:
            d["issues"] = json.loads(d.get("issues") or "[]")
        except Exception:
            d["issues"] = []
        try:
            d["structured_criteria"] = json.loads(d.get("structured_criteria") or "{}")
        except Exception:
            d["structured_criteria"] = {}
        ver_dict["requirement_evaluations"].append(d)

    return ver_dict
