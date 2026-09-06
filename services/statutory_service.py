import os
import json
from datetime import datetime

MOCK_API_DIR = os.environ.get("MOCK_API_DIR", os.path.join(os.path.dirname(__file__), "..", "mock_api"))

def _load_mock_fixture(filename):
    path = os.path.join(MOCK_API_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def _now():
    return datetime.utcnow().isoformat() + "Z"

def _make_response(source_mode, source, status, is_valid, message, disclaimer, data=None, **extra):
    res = {
        "source_mode": source_mode,
        "source": source,
        "status": status,
        "is_valid": is_valid,
        "timestamp": _now(),
        "message": message,
        "disclaimer": disclaimer,
        "data": data or {},
    }
    res.update(extra)
    return res

def _handle_mode(mode_env_key, default_mode="MOCK"):
    mode = os.environ.get(mode_env_key, default_mode).upper().strip()
    if mode not in ("MOCK", "OFFICIAL", "UNAVAILABLE"):
        mode = "MOCK"
    return mode

# -------------------------------------------------------------------------
# 1. GST Adapter
# -------------------------------------------------------------------------
def verify_gst(gstin):
    mode = _handle_mode("GST_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "GSTN_PORTAL", "UNAVAILABLE", None,
            "GST verification source is unavailable. Officer manual check required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID OR FAILED"
        )
    if mode == "OFFICIAL":
        api_key = os.environ.get("GST_API_KEY")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GSTN", "UNAVAILABLE", None,
                "Official GST credentials not configured. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("gst.json")
    if fixture and "records" in fixture:
        clean_gstin = (gstin or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("gstin", "").upper() == clean_gstin:
                is_active = rec.get("status", "").lower() == "active"
                returns = rec.get("returns", {})
                all_filed = returns.get("gstr1_filed", False) and returns.get("gstr3b_filed", False)
                is_valid = is_active and all_filed
                status_str = "ACTIVE" if is_valid else ("CANCELLED" if not is_active else "RETURNS_PENDING")
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", status_str, is_valid,
                    f"GST verification for {clean_gstin}: {status_str}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    gstin=clean_gstin,
                    legal_name=rec.get("legal_name"),
                    address=rec.get("address"),
                    state=rec.get("state")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"GSTIN {gstin} not found in mock GSTN database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"gstin": gstin}
    )

# -------------------------------------------------------------------------
# 2. PAN / Income Tax Adapter
# -------------------------------------------------------------------------
def verify_pan(pan):
    mode = _handle_mode("PAN_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "INCOME_TAX_PORTAL", "UNAVAILABLE", None,
            "Income Tax / PAN portal verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        api_key = os.environ.get("PAN_API_KEY")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_INCOME_TAX", "UNAVAILABLE", None,
                "Official Income Tax API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("pan.json")
    if fixture and "records" in fixture:
        clean_pan = (pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("pan", "").upper() == clean_pan:
                is_active = rec.get("status", "").lower() == "active"
                it = rec.get("it_compliance", {})
                itr_filed = it.get("itr_filed_fy2425", False)
                tax_dues = it.get("tax_dues", 0)
                is_valid = is_active and itr_filed and (tax_dues == 0)
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "ACTIVE" if is_active else "INACTIVE", is_valid,
                    f"PAN status {rec.get('status')}; ITR Filed: {itr_filed}, Tax Dues: Rs.{tax_dues}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    pan=clean_pan,
                    entity_name=rec.get("name")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"PAN {pan} not found in mock registry.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"pan": pan}
    )

# -------------------------------------------------------------------------
# 3. Udyam / MSME Adapter
# -------------------------------------------------------------------------
def verify_udyam(udyam_no):
    mode = _handle_mode("UDYAM_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "UDYAM_PORTAL", "UNAVAILABLE", None,
            "Udyam registration portal currently unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        api_key = os.environ.get("UDYAM_API_KEY")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_UDYAM", "UNAVAILABLE", None,
                "Official Udyam API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("udyam.json")
    if fixture and "records" in fixture:
        clean_no = (udyam_no or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("udyam", "").upper() == clean_no:
                is_active = rec.get("status", "").lower() == "active"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "ACTIVE" if is_active else "INACTIVE", is_active,
                    f"Udyam registration {clean_no} verified as {rec.get('msme_classification')} ({rec.get('major_activity')}).",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    udyam=clean_no,
                    enterprise_name=rec.get("enterprise_name"),
                    classification=rec.get("msme_classification"),
                    address=rec.get("address")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"Udyam number {udyam_no} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"udyam": udyam_no}
    )

# -------------------------------------------------------------------------
# 4. MCA Adapter
# -------------------------------------------------------------------------
def verify_mca(cin_or_pan):
    mode = _handle_mode("MCA_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "MCA21_PORTAL", "UNAVAILABLE", None,
            "MCA portal unavailable. Manual verification required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("MCA_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_MCA", "UNAVAILABLE", None,
                "Official MCA credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("mca.json")
    if fixture and "records" in fixture:
        clean_id = (cin_or_pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("cin", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                is_active = rec.get("status", "").lower() == "active"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "ACTIVE" if is_active else "INACTIVE", is_active,
                    f"MCA entity {rec.get('company_name')} is {rec.get('status')}.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    company_name=rec.get("company_name"),
                    cin=rec.get("cin"),
                    address=rec.get("registered_office")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"MCA record for {cin_or_pan} not found.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"id": cin_or_pan}
    )

# -------------------------------------------------------------------------
# 5. EPFO Adapter
# -------------------------------------------------------------------------
def verify_epfo(establishment_id_or_pan):
    mode = _handle_mode("EPFO_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "EPFO_PORTAL", "UNAVAILABLE", None,
            "EPFO unified portal unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("EPFO_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_EPFO", "UNAVAILABLE", None,
                "Official EPFO API credentials absent. Officer review required.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("epfo.json")
    if fixture and "records" in fixture:
        clean_id = (establishment_id_or_pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("establishment_code", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                compliant = rec.get("compliance_status", "").lower() == "compliant"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "COMPLIANT" if compliant else "NON_COMPLIANT", compliant,
                    f"EPFO compliance for {rec.get('establishment_name')}: {rec.get('compliance_status')}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    active_members=rec.get("active_members")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"EPFO establishment {establishment_id_or_pan} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 6. ESIC Adapter
# -------------------------------------------------------------------------
def verify_esic(code_or_pan):
    mode = _handle_mode("ESIC_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "ESIC_PORTAL", "UNAVAILABLE", None,
            "ESIC portal unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("ESIC_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_ESIC", "UNAVAILABLE", None,
                "Official ESIC API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("esic.json")
    if fixture and "records" in fixture:
        clean_id = (code_or_pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("employer_code", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                compliant = rec.get("compliance_status", "").lower() == "compliant"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "COMPLIANT" if compliant else "NON_COMPLIANT", compliant,
                    f"ESIC compliance for {rec.get('employer_name')}: {rec.get('compliance_status')}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"ESIC code {code_or_pan} not found.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 7. Startup India Adapter
# -------------------------------------------------------------------------
def verify_startup(dipp_or_pan):
    mode = _handle_mode("STARTUP_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "STARTUP_INDIA_PORTAL", "UNAVAILABLE", None,
            "Startup India verification service unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("STARTUP_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_STARTUP_INDIA", "UNAVAILABLE", None,
                "Official Startup India credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("startup.json")
    if fixture and "records" in fixture:
        clean_id = (dipp_or_pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("dipp_recognition_no", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                is_recognized = rec.get("recognized", False)
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "RECOGNIZED" if is_recognized else "NOT_RECOGNIZED", is_recognized,
                    f"Startup India DIPP {rec.get('dipp_recognition_no')}: Recognized={is_recognized}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"Startup India identifier {dipp_or_pan} not found.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 8. NSIC Adapter
# -------------------------------------------------------------------------
def verify_nsic(reg_no_or_pan):
    mode = _handle_mode("NSIC_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "NSIC_PORTAL", "UNAVAILABLE", None,
            "NSIC portal verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("NSIC_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_NSIC", "UNAVAILABLE", None,
                "Official NSIC credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    clean_id = (reg_no_or_pan or "").upper().strip()
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "REGISTERED", True,
        f"NSIC registration verified under Single Point Registration Scheme for {clean_id}.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"registration_no": clean_id, "scheme": "SPRS"}
    )

# -------------------------------------------------------------------------
# 9. BIS Adapter
# -------------------------------------------------------------------------
def verify_bis(license_no_or_pan):
    mode = _handle_mode("BIS_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "BIS_PORTAL", "UNAVAILABLE", None,
            "BIS Manakonline verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("BIS_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_BIS", "UNAVAILABLE", None,
                "Official BIS API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("bis.json")
    if fixture and "records" in fixture:
        clean_id = (license_no_or_pan or "").upper().strip()
        for rec in fixture["records"]:
            if rec.get("registration_no", "").upper() == clean_id or rec.get("cml_no", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                is_valid = rec.get("status", "").lower() == "operative"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "OPERATIVE" if is_valid else "EXPIRED", is_valid,
                    f"BIS License {rec.get('registration_no') or rec.get('cml_no')}: {rec.get('status')}, Standard: {rec.get('is_standard')}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    standard=rec.get("is_standard"),
                    expiry=rec.get("valid_to")
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"BIS registration {license_no_or_pan} not found.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 10. Blacklisting / Debarment Adapter
# -------------------------------------------------------------------------
def verify_blacklisting(pan=None, gstin=None):
    mode = _handle_mode("BLACKLIST_MODE")
    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "DEBARMENT_DATABASE", "UNAVAILABLE", None,
            "Central Debarment / CVC Blacklist database unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS BLACKLISTED"
        )
    if mode == "OFFICIAL":
        if not os.environ.get("BLACKLIST_API_KEY"):
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_CVC", "UNAVAILABLE", None,
                "Official CVC / GeM debarment portal credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — MANUAL REVIEW REQUIRED"
            )

    fixture = _load_mock_fixture("blacklist.json")
    clean_pan = (pan or "").upper().strip()
    clean_gstin = (gstin or "").upper().strip()

    if fixture:
        for rec in fixture.get("records", []):
            if (clean_pan and rec.get("pan", "").upper() == clean_pan) or \
               (clean_gstin and rec.get("gstin", "").upper() == clean_gstin):
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "BLACKLISTED", False,
                    f"WARNING: Entity is DEBARRED / BLACKLISTED by {rec.get('authority')}! Reason: {rec.get('reason')}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    debarred=True,
                    authority=rec.get("authority"),
                    reason=rec.get("reason")
                )

        for rec in fixture.get("clear", []):
            if (clean_pan and rec.get("pan", "").upper() == clean_pan) or \
               (clean_gstin and rec.get("gstin", "").upper() == clean_gstin):
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "CLEARED", True,
                    "No debarment or blacklisting orders found. Entity is clear.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    debarred=False
                )

    return _make_response(
        "MOCK", "MOCK_ADAPTER", "CLEARED", True,
        "No debarment records found in mock blacklist registry.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"pan": pan, "gstin": gstin},
        debarred=False
    )

# -------------------------------------------------------------------------
# 11. DigiLocker Adapter
# -------------------------------------------------------------------------
def verify_digilocker(doc_uri_or_id):
    if not doc_uri_or_id:
        return _make_response(
            "UNAVAILABLE", "DIGILOCKER", "UNAVAILABLE", None,
            "No DigiLocker URI provided for verification.",
            "DIGILOCKER URI ABSENT"
        )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "VERIFIED", True,
        f"DigiLocker document {doc_uri_or_id} electronically verified with digital signature.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"uri": doc_uri_or_id, "signature_valid": True}
    )

# -------------------------------------------------------------------------
# 12. GeM Portal Bid Fetcher Adapter
# -------------------------------------------------------------------------
def fetch_gem_bid(gem_bid_id):
    mode = _handle_mode("GEM_MODE")
    if mode == "OFFICIAL":
        api_key = os.environ.get("GEM_API_KEY")
        if not api_key:
            pass # Fallback to mock

    fixture = _load_mock_fixture("gem.json")
    clean_id = (gem_bid_id or "").strip()
    if fixture and "records" in fixture:
        for rec in fixture["records"]:
            if rec.get("gem_bid_id", "").upper() == clean_id.upper():
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "FOUND", True,
                    f"GeM bid details retrieved for {clean_id}.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"GeM Bid {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )
