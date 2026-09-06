import os
import json
import urllib.request
import urllib.error
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

def _execute_official_http_get(service_name, url, api_key=None, timeout=5):
    """
    Executes an official HTTP request with timeout.
    Returns (success: bool, status_code: int, data: dict or str, error_msg: str or None)
    """
    headers = {
        "User-Agent": "GeM-Bid-Compliance-Platform/2.0",
        "Accept": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = {"raw": body}
            return True, response.status, parsed, None
    except urllib.error.HTTPError as e:
        return False, e.code, None, f"HTTP Error {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        return False, 0, None, f"Network/Connection error: {e.reason}"
    except Exception as e:
        return False, 0, None, f"Request failed: {str(e)}"

# -------------------------------------------------------------------------
# 1. GST Adapter
# -------------------------------------------------------------------------
def verify_gst(gstin):
    mode = _handle_mode("GST_MODE")
    clean_gstin = (gstin or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "GSTN_PORTAL", "UNAVAILABLE", None,
            "GST verification source is unavailable. Officer manual check required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID OR FAILED",
            gstin=clean_gstin
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("GST_API_KEY")
        api_url = os.environ.get("GST_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GSTN", "UNAVAILABLE", None,
                "Official GST credentials not configured. Officer manual verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                gstin=clean_gstin
            )
        target_url = f"{api_url or 'https://api.gstn.org.in/v1/taxpayer'}/{clean_gstin}"
        ok, code, data, err = _execute_official_http_get("GSTN", target_url, api_key)
        if ok and isinstance(data, dict):
            is_active = str(data.get("status", "")).lower() == "active"
            return _make_response(
                "OFFICIAL", "OFFICIAL_GSTN", "ACTIVE" if is_active else "CANCELLED", is_active,
                f"Official GSTN record retrieved for {clean_gstin}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (GSTN)",
                data=data,
                gstin=clean_gstin,
                legal_name=data.get("legal_name"),
                address=data.get("address"),
                state=data.get("state")
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GSTN", "UNAVAILABLE", None,
                f"Official GSTN API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)",
                gstin=clean_gstin
            )

    # MOCK mode
    fixture = _load_mock_fixture("gst.json")
    if fixture and "records" in fixture:
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
        f"GSTIN {clean_gstin} not found in mock GSTN database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"gstin": clean_gstin}
    )

# -------------------------------------------------------------------------
# 2. PAN / Income Tax Adapter
# -------------------------------------------------------------------------
def verify_pan(pan):
    mode = _handle_mode("PAN_MODE")
    clean_pan = (pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "INCOME_TAX_PORTAL", "UNAVAILABLE", None,
            "Income Tax / PAN portal verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID",
            pan=clean_pan
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("PAN_API_KEY")
        api_url = os.environ.get("PAN_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_INCOME_TAX", "UNAVAILABLE", None,
                "Official Income Tax API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan
            )
        target_url = f"{api_url or 'https://api.incometax.gov.in/v1/pan'}/{clean_pan}"
        ok, code, data, err = _execute_official_http_get("PAN", target_url, api_key)
        if ok and isinstance(data, dict):
            is_active = str(data.get("status", "")).lower() == "active"
            return _make_response(
                "OFFICIAL", "OFFICIAL_INCOME_TAX", "ACTIVE" if is_active else "INACTIVE", is_active,
                f"Official PAN verification for {clean_pan}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (INCOME TAX)",
                data=data,
                pan=clean_pan,
                entity_name=data.get("name")
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_INCOME_TAX", "UNAVAILABLE", None,
                f"Official PAN API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan
            )

    # MOCK mode
    fixture = _load_mock_fixture("pan.json")
    if fixture and "records" in fixture:
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
        f"PAN {clean_pan} not found in mock registry.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"pan": clean_pan}
    )

# -------------------------------------------------------------------------
# 3. Udyam / MSME Adapter
# -------------------------------------------------------------------------
def verify_udyam(udyam_no):
    mode = _handle_mode("UDYAM_MODE")
    clean_no = (udyam_no or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "UDYAM_PORTAL", "UNAVAILABLE", None,
            "Udyam registration portal currently unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID",
            udyam=clean_no
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("UDYAM_API_KEY")
        api_url = os.environ.get("UDYAM_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_UDYAM", "UNAVAILABLE", None,
                "Official Udyam API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                udyam=clean_no
            )
        target_url = f"{api_url or 'https://udyamregistration.gov.in/api/v1/verify'}/{clean_no}"
        ok, code, data, err = _execute_official_http_get("UDYAM", target_url, api_key)
        if ok and isinstance(data, dict):
            is_active = str(data.get("status", "")).lower() == "active"
            return _make_response(
                "OFFICIAL", "OFFICIAL_UDYAM", "ACTIVE" if is_active else "INACTIVE", is_active,
                f"Official Udyam registration verification for {clean_no}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (UDYAM)",
                data=data,
                udyam=clean_no,
                enterprise_name=data.get("enterprise_name"),
                classification=data.get("msme_classification")
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_UDYAM", "UNAVAILABLE", None,
                f"Official Udyam API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)",
                udyam=clean_no
            )

    # MOCK mode
    fixture = _load_mock_fixture("udyam.json")
    if fixture and "records" in fixture:
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
        f"Udyam number {clean_no} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"udyam": clean_no}
    )

# -------------------------------------------------------------------------
# 4. MCA Adapter
# -------------------------------------------------------------------------
def verify_mca(cin_or_pan):
    mode = _handle_mode("MCA_MODE")
    clean_id = (cin_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "MCA21_PORTAL", "UNAVAILABLE", None,
            "MCA portal unavailable. Manual verification required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("MCA_API_KEY")
        api_url = os.environ.get("MCA_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_MCA", "UNAVAILABLE", None,
                "Official MCA credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://mca.gov.in/api/v1/company'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("MCA", target_url, api_key)
        if ok and isinstance(data, dict):
            is_active = str(data.get("status", "")).lower() == "active"
            return _make_response(
                "OFFICIAL", "OFFICIAL_MCA", "ACTIVE" if is_active else "INACTIVE", is_active,
                f"Official MCA entity verification for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (MCA21)",
                data=data,
                company_name=data.get("company_name"),
                cin=data.get("cin")
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_MCA", "UNAVAILABLE", None,
                f"Official MCA API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("mca.json")
    if fixture and "records" in fixture:
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
        f"MCA record for {clean_id} not found.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"id": clean_id}
    )

# -------------------------------------------------------------------------
# 5. EPFO Adapter
# -------------------------------------------------------------------------
def verify_epfo(establishment_id_or_pan):
    mode = _handle_mode("EPFO_MODE")
    clean_id = (establishment_id_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "EPFO_PORTAL", "UNAVAILABLE", None,
            "EPFO unified portal unavailable. Manual inspection required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("EPFO_API_KEY")
        api_url = os.environ.get("EPFO_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_EPFO", "UNAVAILABLE", None,
                "Official EPFO API credentials absent. Officer review required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://epfindia.gov.in/api/v1/establishment'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("EPFO", target_url, api_key)
        if ok and isinstance(data, dict):
            comp = str(data.get("compliance_status", "")).lower() == "compliant"
            return _make_response(
                "OFFICIAL", "OFFICIAL_EPFO", "COMPLIANT" if comp else "NON_COMPLIANT", comp,
                f"Official EPFO compliance record retrieved for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (EPFO)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_EPFO", "UNAVAILABLE", None,
                f"Official EPFO API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("epfo.json")
    if fixture and "records" in fixture:
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
        f"EPFO establishment {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 6. ESIC Adapter
# -------------------------------------------------------------------------
def verify_esic(code_or_pan):
    mode = _handle_mode("ESIC_MODE")
    clean_id = (code_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "ESIC_PORTAL", "UNAVAILABLE", None,
            "ESIC portal unavailable. Manual verification required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("ESIC_API_KEY")
        api_url = os.environ.get("ESIC_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_ESIC", "UNAVAILABLE", None,
                "Official ESIC API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://esic.gov.in/api/v1/employer'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("ESIC", target_url, api_key)
        if ok and isinstance(data, dict):
            comp = str(data.get("compliance_status", "")).lower() == "compliant"
            return _make_response(
                "OFFICIAL", "OFFICIAL_ESIC", "COMPLIANT" if comp else "NON_COMPLIANT", comp,
                f"Official ESIC compliance verification for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (ESIC)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_ESIC", "UNAVAILABLE", None,
                f"Official ESIC API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("esic.json")
    if fixture and "records" in fixture:
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
        f"ESIC code {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 7. Startup India Adapter
# -------------------------------------------------------------------------
def verify_startup(dipp_or_pan):
    mode = _handle_mode("STARTUP_MODE")
    clean_id = (dipp_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "STARTUP_INDIA_PORTAL", "UNAVAILABLE", None,
            "Startup India verification service unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("STARTUP_API_KEY")
        api_url = os.environ.get("STARTUP_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_STARTUP_INDIA", "UNAVAILABLE", None,
                "Official Startup India credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://startupindia.gov.in/api/v1/recognition'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("STARTUP_INDIA", target_url, api_key)
        if ok and isinstance(data, dict):
            is_rec = bool(data.get("recognized", False))
            return _make_response(
                "OFFICIAL", "OFFICIAL_STARTUP_INDIA", "RECOGNIZED" if is_rec else "NOT_RECOGNIZED", is_rec,
                f"Official Startup India verification for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (STARTUP INDIA)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_STARTUP_INDIA", "UNAVAILABLE", None,
                f"Official Startup India API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("startup.json")
    if fixture and "records" in fixture:
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
        f"Startup India identifier {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 8. NSIC Adapter
# -------------------------------------------------------------------------
def verify_nsic(reg_no_or_pan):
    mode = _handle_mode("NSIC_MODE")
    clean_id = (reg_no_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "NSIC_PORTAL", "UNAVAILABLE", None,
            "NSIC portal verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("NSIC_API_KEY")
        api_url = os.environ.get("NSIC_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_NSIC", "UNAVAILABLE", None,
                "Official NSIC credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://nsic.co.in/api/v1/sprs'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("NSIC", target_url, api_key)
        if ok and isinstance(data, dict):
            reg = bool(data.get("is_registered", True))
            return _make_response(
                "OFFICIAL", "OFFICIAL_NSIC", "REGISTERED" if reg else "NOT_FOUND", reg,
                f"Official NSIC registration verified for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (NSIC)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_NSIC", "UNAVAILABLE", None,
                f"Official NSIC API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
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
    clean_id = (license_no_or_pan or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "BIS_PORTAL", "UNAVAILABLE", None,
            "BIS Manakonline verification unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("BIS_API_KEY")
        api_url = os.environ.get("BIS_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_BIS", "UNAVAILABLE", None,
                "Official BIS API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://manakonline.in/api/v1/license'}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("BIS", target_url, api_key)
        if ok and isinstance(data, dict):
            is_valid = str(data.get("status", "")).lower() == "operative"
            return _make_response(
                "OFFICIAL", "OFFICIAL_BIS", "OPERATIVE" if is_valid else "EXPIRED", is_valid,
                f"Official BIS License verification for {clean_id}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (BIS)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_BIS", "UNAVAILABLE", None,
                f"Official BIS API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("bis.json")
    if fixture and "records" in fixture:
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
        f"BIS registration {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )

# -------------------------------------------------------------------------
# 10. Blacklisting / Debarment Adapter
# -------------------------------------------------------------------------
def verify_blacklisting(pan=None, gstin=None):
    mode = _handle_mode("BLACKLIST_MODE")
    clean_pan = (pan or "").upper().strip()
    clean_gstin = (gstin or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "DEBARMENT_DATABASE", "UNAVAILABLE", None,
            "Central Debarment / CVC Blacklist database unavailable. Officer manual check required.",
            "UNAVAILABLE STATE — NOT TREATED AS BLACKLISTED",
            pan=clean_pan,
            gstin=clean_gstin
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("BLACKLIST_API_KEY")
        api_url = os.environ.get("BLACKLIST_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_CVC", "UNAVAILABLE", None,
                "Official CVC / GeM debarment portal credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan,
                gstin=clean_gstin
            )
        target_url = f"{api_url or 'https://cvc.gov.in/api/v1/debarred'}?pan={clean_pan}&gstin={clean_gstin}"
        ok, code, data, err = _execute_official_http_get("CVC_BLACKLIST", target_url, api_key)
        if ok and isinstance(data, dict):
            is_debarred = bool(data.get("debarred", False))
            return _make_response(
                "OFFICIAL", "OFFICIAL_CVC", "BLACKLISTED" if is_debarred else "CLEARED", not is_debarred,
                f"Official CVC debarment check completed for PAN {clean_pan}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (CVC/GeM)",
                data=data,
                debarred=is_debarred
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_CVC", "UNAVAILABLE", None,
                f"Official Debarment API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan,
                gstin=clean_gstin
            )

    # MOCK mode
    fixture = _load_mock_fixture("blacklist.json")
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
        data={"pan": clean_pan, "gstin": clean_gstin},
        debarred=False
    )

# -------------------------------------------------------------------------
# 11. DigiLocker Adapter
# -------------------------------------------------------------------------
def verify_digilocker(doc_uri_or_id):
    mode = _handle_mode("DIGILOCKER_MODE", default_mode="MOCK")
    clean_uri = (doc_uri_or_id or "").strip()

    if not clean_uri:
        return _make_response(
            "UNAVAILABLE", "DIGILOCKER", "UNAVAILABLE", None,
            "No DigiLocker URI provided for verification.",
            "DIGILOCKER URI ABSENT"
        )

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "DIGILOCKER", "UNAVAILABLE", None,
            "DigiLocker service integration is currently unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("DIGILOCKER_API_KEY")
        api_url = os.environ.get("DIGILOCKER_API_URL")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_DIGILOCKER", "UNAVAILABLE", None,
                "Official DigiLocker credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url or 'https://digilocker.gov.in/api/v1/verify'}/{clean_uri}"
        ok, code, data, err = _execute_official_http_get("DIGILOCKER", target_url, api_key)
        if ok and isinstance(data, dict):
            sig_valid = bool(data.get("signature_valid", True))
            return _make_response(
                "OFFICIAL", "OFFICIAL_DIGILOCKER", "VERIFIED" if sig_valid else "FAILED", sig_valid,
                f"Official DigiLocker electronic verification for {clean_uri}.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (DIGILOCKER)",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_DIGILOCKER", "UNAVAILABLE", None,
                f"Official DigiLocker API call failed: {err or f'Status code {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "VERIFIED", True,
        f"DigiLocker document {clean_uri} electronically verified with digital signature.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"uri": clean_uri, "signature_valid": True}
    )

# -------------------------------------------------------------------------
# 12. GeM Portal Bid Fetcher Adapter
# -------------------------------------------------------------------------
def fetch_gem_bid(gem_bid_id):
    mode = _handle_mode("GEM_MODE", default_mode="MOCK")
    clean_id = (gem_bid_id or "").strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "GEM_PORTAL", "UNAVAILABLE", None,
            "GeM portal integration is currently marked unavailable.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID OR FAILED"
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("GEM_API_KEY")
        api_url = os.environ.get("GEM_API_URL", "https://gem.gov.in/api/v1/bids")
        if not api_key:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GEM_PORTAL", "UNAVAILABLE", None,
                "Official GeM API credentials not configured.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("GEM_PORTAL", target_url, api_key)
        if ok and isinstance(data, dict) and data.get("gem_bid_id"):
            return _make_response(
                "OFFICIAL", "OFFICIAL_GEM_PORTAL", "FOUND", True,
                f"Official GeM bid details retrieved for {clean_id}.",
                "OFFICIAL GeM GOVERNMENT PORTAL DATA",
                data=data
            )
        else:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GEM_PORTAL", "UNAVAILABLE", None,
                f"Official GeM API call failed: {err or f'HTTP status {code}'}",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )

    # MOCK mode
    fixture = _load_mock_fixture("gem.json")
    if fixture and "records" in fixture:
        for rec in fixture["records"]:
            if rec.get("gem_bid_id", "").upper() == clean_id.upper():
                return _make_response(
                    "MOCK", "MOCK_GEM_FIXTURE", "FOUND", True,
                    f"GeM bid details retrieved for {clean_id} from mock dataset.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec
                )
    return _make_response(
        "MOCK", "MOCK_GEM_FIXTURE", "NOT_FOUND", False,
        f"GeM Bid {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"
    )
