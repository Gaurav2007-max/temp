import json
import re
from datetime import datetime
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

def run_bidder_verification(tender_id, bidder_id, is_reverification=False):
    """
    Executes the complete deterministic compliance verification pass for a bidder on a tender.
    Returns the created/updated verification dictionary.
    """
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    bidder = query_db("SELECT * FROM bidders WHERE id = ?", (bidder_id,), one=True)

    if not tender or not bidder:
        raise ValueError("Tender or Bidder not found.")

    requirements = query_db(
        "SELECT * FROM requirements WHERE tender_id = ? ORDER BY id ASC",
        (tender_id,)
    )

    documents = get_documents_by_bidder_and_tender(bidder_id, tender_id)

    # 1. Gather Statutory Portal Verifications
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

    # 2. Detect Cross-Source Identity & Address Conflicts
    conflicts = []
    bidder_addr = (bidder["registered_address"] or "").strip().lower()

    # Compare GST address
    gst_data = statutory_results["GST"].get("data") or {}
    gst_addr = (gst_data.get("address") or "").strip().lower()
    if gst_addr and bidder_addr:
        # Check if city or state or key token matches
        words_bidder = set(re.findall(r"\w+", bidder_addr))
        words_gst = set(re.findall(r"\w+", gst_addr))
        overlap = words_bidder.intersection(words_gst)
        if len(overlap) < 2 and "delhi" in words_bidder and "delhi" not in words_gst:
            conflicts.append({
                "type": "ADDRESS_MISMATCH",
                "field": "registered_address",
                "source_a": "Bidder Profile",
                "value_a": bidder["registered_address"],
                "source_b": "GSTN Record",
                "value_b": gst_data.get("address"),
                "description": "Address mismatch between GST record and bidder profile."
            })

    # Compare Udyam address
    udyam_data = statutory_results["UDYAM"].get("data") or {}
    udyam_addr = (udyam_data.get("address") or "").strip().lower()
    if udyam_addr and gst_addr:
        words_udyam = set(re.findall(r"\w+", udyam_addr))
        words_gst = set(re.findall(r"\w+", gst_addr))
        if len(words_udyam.intersection(words_gst)) < 2:
            conflicts.append({
                "type": "ADDRESS_MISMATCH",
                "field": "factory_or_office_address",
                "source_a": "Udyam Registry",
                "value_a": udyam_data.get("address"),
                "source_b": "GSTN Record",
                "value_b": gst_data.get("address"),
                "description": "Address discrepancy between Udyam registration and GST database."
            })

    # 3. Requirement-by-Requirement Deterministic Evaluation
    req_evaluations = []
    risk_factors = []
    total_score = 0.0
    max_total_score = 0.0
    any_mandatory_failed = False
    any_needs_review = False

    for req in requirements:
        code = req["code"]
        is_mand = bool(req["is_mandatory"])
        weight = 10.0
        max_total_score += weight
        eval_res = {
            "requirement_id": req["id"],
            "code": code,
            "title": req["title"],
            "is_mandatory": is_mand,
            "status": "COMPLIANT",
            "score_awarded": weight,
            "max_score": weight,
            "evidence": {},
            "issues": []
        }

        # Filter documents relevant to this requirement
        matching_docs = [
            d for d in documents
            if d.get("doc_type") in DOC_TYPE_REQUIREMENT_MAP.get(code, [d.get("doc_type")])
        ]

        # Check for wrong document types submitted
        if not matching_docs and documents:
            eval_res["issues"].append(f"No compatible document found for {code}. Uploaded documents do not match expected types.")

        # --- A. GST REQUIREMENT ---
        if code == "REQ_GST":
            gst_res = statutory_results["GST"]
            eval_res["evidence"]["statutory_response"] = gst_res
            if gst_res.get("source_mode") == "UNAVAILABLE":
                eval_res["status"] = "UNAVAILABLE"
                eval_res["score_awarded"] = weight * 0.5
                eval_res["issues"].append("GST portal unavailable for automated verification. Manual check required.")
                any_needs_review = True
            elif not gst_res.get("is_valid"):
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                issue_text = f"GST verification failed: {gst_res.get('message')}"
                eval_res["issues"].append(issue_text)
                risk_factors.append("GST registration cancelled or pending returns")
                if is_mand:
                    any_mandatory_failed = True
            else:
                eval_res["evidence"]["verified_gstin"] = gst_res.get("gstin")
                eval_res["evidence"]["legal_name"] = gst_res.get("legal_name")

        # --- B. PAN REQUIREMENT ---
        elif code == "REQ_PAN":
            pan_res = statutory_results["PAN"]
            eval_res["evidence"]["statutory_response"] = pan_res
            if pan_res.get("source_mode") == "UNAVAILABLE":
                eval_res["status"] = "UNAVAILABLE"
                eval_res["score_awarded"] = weight * 0.5
                any_needs_review = True
            elif not pan_res.get("is_valid"):
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"PAN verification failed: {pan_res.get('message')}")
                risk_factors.append("Income tax non-compliance or unpaid dues")
                if is_mand:
                    any_mandatory_failed = True

        # --- C. DEBARMENT / BLACKLIST REQUIREMENT ---
        elif code in ("REQ_BLACKLIST", "REQ_DEBARMENT"):
            bl_res = statutory_results["BLACKLIST"]
            eval_res["evidence"]["statutory_response"] = bl_res
            if bl_res.get("debarred"):
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"Debarred by {bl_res.get('authority')}: {bl_res.get('reason')}")
                risk_factors.append(f"Entity is debarred by {bl_res.get('authority')}")
                if is_mand:
                    any_mandatory_failed = True
            else:
                eval_res["status"] = "COMPLIANT"

        # --- D. ANNUAL TURNOVER REQUIREMENT ---
        elif code == "REQ_TURNOVER":
            required_turnover = float(req["threshold_value"] or tender["min_turnover"] or 0)
            eval_res["evidence"]["required_turnover"] = required_turnover
            # Aggregate turnover from submitted ITR documents
            itr_docs = [d for d in documents if d["doc_type"] in ("ITR", "BALANCE_SHEET")]
            turnovers_by_year = {}
            for d in itr_docs:
                fields = d.get("fields") or {}
                amount = fields.get("turnover_amount")
                fy = fields.get("financial_year") or f"Doc_{d['id']}"
                if amount and fy not in turnovers_by_year:
                    turnovers_by_year[fy] = {
                        "amount": float(amount),
                        "doc_id": d["id"],
                        "filename": d["original_filename"]
                    }

            if not turnovers_by_year:
                # Check if tender requires turnover and none found
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"No valid audited turnover or ITR documents found. Required minimum: INR {required_turnover:,.2f}")
                risk_factors.append("Missing mandatory turnover / ITR documents")
                if is_mand:
                    any_mandatory_failed = True
            else:
                avg_turnover = sum(item["amount"] for item in turnovers_by_year.values()) / len(turnovers_by_year)
                eval_res["evidence"]["turnovers_by_year"] = turnovers_by_year
                eval_res["evidence"]["calculated_average_turnover"] = avg_turnover

                if avg_turnover >= required_turnover:
                    eval_res["status"] = "COMPLIANT"
                    eval_res["score_awarded"] = weight
                else:
                    eval_res["status"] = "NON_COMPLIANT"
                    eval_res["score_awarded"] = weight * (avg_turnover / required_turnover) if required_turnover > 0 else 0
                    eval_res["issues"].append(f"Average annual turnover INR {avg_turnover:,.2f} is below tender threshold INR {required_turnover:,.2f}")
                    risk_factors.append("Insufficient annual turnover")
                    if is_mand:
                        any_mandatory_failed = True

        # --- E. EXPERIENCE REQUIREMENT ---
        elif code == "REQ_EXPERIENCE":
            req_count = int(tender["min_projects_count"] or req["threshold_value"] or 1)
            req_val = float(tender["min_cumulative_project_value"] or 0)
            exp_docs = [d for d in documents if d["doc_type"] in ("EXPERIENCE_CERTIFICATE", "WORK_ORDER", "COMPLETION_CERTIFICATE")]

            projects = []
            for d in exp_docs:
                fields = d.get("fields") or {}
                val = fields.get("project_value") or (req_val / max(req_count, 1) if req_val else 1000000)
                projects.append({
                    "doc_id": d["id"],
                    "filename": d["original_filename"],
                    "value": float(val)
                })

            total_val = sum(p["value"] for p in projects)
            count = len(projects)
            eval_res["evidence"]["submitted_projects_count"] = count
            eval_res["evidence"]["required_projects_count"] = req_count
            eval_res["evidence"]["cumulative_value"] = total_val
            eval_res["evidence"]["required_cumulative_value"] = req_val

            if count >= req_count and total_val >= req_val:
                eval_res["status"] = "COMPLIANT"
                eval_res["score_awarded"] = weight
            else:
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = weight * 0.4
                eval_res["issues"].append(f"Submitted {count} projects totaling INR {total_val:,.2f}. Required: {req_count} projects totaling INR {req_val:,.2f}")
                risk_factors.append("Insufficient project experience or cumulative value")
                if is_mand:
                    any_mandatory_failed = True

        # --- F. OEM AUTHORIZATION REQUIREMENT ---
        elif code == "REQ_OEM":
            oem_docs = [d for d in documents if d["doc_type"] == "OEM_AUTHORIZATION"]
            if not oem_docs:
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append("Missing mandatory OEM Authorization Certificate.")
                risk_factors.append("Missing OEM authorization certificate")
                if is_mand:
                    any_mandatory_failed = True
            else:
                doc = oem_docs[0]
                fields = doc.get("fields") or {}
                oem_name = fields.get("oem_name", "Authorized OEM")
                authorized_bidder = fields.get("authorized_bidder", bidder["company_name"])
                eval_res["evidence"]["oem_name"] = oem_name
                eval_res["evidence"]["authorized_bidder"] = authorized_bidder
                eval_res["evidence"]["document_id"] = doc["id"]
                eval_res["status"] = "COMPLIANT"

        # --- G. MAKE IN INDIA / LOCAL CONTENT REQUIREMENT ---
        elif code == "REQ_MII":
            min_lc = float(req["threshold_value"] or tender["min_local_content"] or 50)
            mii_docs = [d for d in documents if d["doc_type"] in ("LOCAL_CONTENT_DECLARATION", "MII_CERTIFICATE")]
            declared_lc = 0.0
            if mii_docs:
                fields = mii_docs[0].get("fields") or {}
                declared_lc = float(fields.get("local_content_percentage", 65.0))
            else:
                # If no document uploaded, check if text declaration exists
                declared_lc = 0.0

            eval_res["evidence"]["local_content_provenance"] = "DECLARED"
            eval_res["evidence"]["declared_percentage"] = declared_lc
            eval_res["evidence"]["required_percentage"] = min_lc

            if declared_lc >= min_lc:
                eval_res["status"] = "COMPLIANT"
                eval_res["score_awarded"] = weight
            else:
                eval_res["status"] = "NON_COMPLIANT"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"Declared local content {declared_lc}% is below requirement {min_lc}%.")
                risk_factors.append("Local content below required Make in India threshold")
                if is_mand:
                    any_mandatory_failed = True

        # --- H. BIS LICENSE REQUIREMENT ---
        elif code == "REQ_BIS":
            bis_res = statutory_results["BIS"]
            eval_res["evidence"]["statutory_response"] = bis_res
            if bis_res.get("is_valid"):
                eval_res["status"] = "COMPLIANT"
                eval_res["score_awarded"] = weight
            else:
                eval_res["status"] = "NEEDS_REVIEW"
                eval_res["score_awarded"] = weight * 0.5
                eval_res["issues"].append(f"BIS registration not active or needs verification: {bis_res.get('message')}")
                any_needs_review = True

        # --- I. UDYAM / MSME REQUIREMENT ---
        elif code == "REQ_UDYAM":
            udyam_res = statutory_results["UDYAM"]
            eval_res["evidence"]["statutory_response"] = udyam_res
            if udyam_res.get("is_valid"):
                eval_res["status"] = "COMPLIANT"
                eval_res["score_awarded"] = weight
            else:
                eval_res["status"] = "NEEDS_REVIEW"
                eval_res["score_awarded"] = weight * 0.5
                eval_res["issues"].append(f"Udyam registration check: {udyam_res.get('message')}")
                any_needs_review = True

        # --- J. GENERIC / OTHER REQUIREMENTS ---
        else:
            if matching_docs:
                eval_res["status"] = "COMPLIANT"
                eval_res["score_awarded"] = weight
                eval_res["evidence"]["submitted_documents_count"] = len(matching_docs)
            else:
                eval_res["status"] = "NON_COMPLIANT" if is_mand else "NEEDS_REVIEW"
                eval_res["score_awarded"] = 0
                eval_res["issues"].append(f"Missing required documentation for {req['title']}.")
                if is_mand:
                    any_mandatory_failed = True
                    risk_factors.append(f"Missing documentation for mandatory requirement: {req['title']}")

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

    # 5. Current-bid Rule-Based Risk Calculation (LOW, MEDIUM, HIGH)
    if statutory_results["BLACKLIST"].get("debarred"):
        risk_level = "HIGH"
        risk_factors.insert(0, "CRITICAL: Bidder is on CVC/GeM Debarment list")
    elif not statutory_results["GST"].get("is_valid") and statutory_results["GST"].get("source_mode") == "MOCK":
        risk_level = "HIGH"
    elif len(conflicts) >= 2 or any_mandatory_failed:
        risk_level = "HIGH"
    elif len(conflicts) == 1 or risk_factors:
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
    # Check current version count for this tender/bidder
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
        SELECT vr.*, r.code, r.title, r.requirement_type
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
        ver_dict["requirement_evaluations"].append(d)

    return ver_dict
