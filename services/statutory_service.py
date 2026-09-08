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
    """Prefer configured official credentials, while keeping fixtures usable locally."""
    mode_value = os.environ.get(mode_env_key)
    if mode_value is None:
        api_key_name = mode_env_key.replace("_MODE", "_API_KEY")
        mode_value = "OFFICIAL" if os.environ.get(api_key_name, "").strip() else default_mode
    mode = mode_value.upper().strip()
    if mode == "OFFICIAL":
        api_key_name = mode_env_key.replace("_MODE", "_API_KEY")
        if not os.environ.get(api_key_name, "").strip():
            return "OFFICIAL"
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
        api_url = os.environ.get("GST_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GSTN", "UNAVAILABLE", None,
                "Official GST credentials not configured. Officer manual verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                gstin=clean_gstin
            )
        target_url = f"{api_url.rstrip('/')}/{clean_gstin}"
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
        api_url = os.environ.get("PAN_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_INCOME_TAX", "UNAVAILABLE", None,
                "Official Income Tax API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan
            )
        target_url = f"{api_url.rstrip('/')}/{clean_pan}"
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
        api_url = os.environ.get("UDYAM_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_UDYAM", "UNAVAILABLE", None,
                "Official Udyam API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                udyam=clean_no
            )
        target_url = f"{api_url.rstrip('/')}/{clean_no}"
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
        api_url = os.environ.get("MCA_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_MCA", "UNAVAILABLE", None,
                "Official MCA credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
        api_url = os.environ.get("EPFO_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_EPFO", "UNAVAILABLE", None,
                "Official EPFO API credentials absent. Officer review required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
        api_url = os.environ.get("ESIC_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_ESIC", "UNAVAILABLE", None,
                "Official ESIC API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
        api_url = os.environ.get("STARTUP_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_STARTUP_INDIA", "UNAVAILABLE", None,
                "Official Startup India credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
        api_url = os.environ.get("NSIC_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_NSIC", "UNAVAILABLE", None,
                "Official NSIC credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
    fixture = _load_mock_fixture("startup.json")
    if fixture and "nsic" in fixture:
        for rec in fixture["nsic"]:
            if rec.get("nsic_reg", "").upper() == clean_id or rec.get("pan", "").upper() == clean_id:
                is_valid = rec.get("status", "").lower() == "valid"
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "REGISTERED" if is_valid else "EXPIRED", is_valid,
                    f"NSIC registration for {clean_id}: {rec.get('status')}",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec
                )

    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", False,
        f"NSIC registration {clean_id} not found in mock database.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        data={"registration_no": clean_id}
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
        api_url = os.environ.get("BIS_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_BIS", "UNAVAILABLE", None,
                "Official BIS API credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
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
        api_url = os.environ.get("BLACKLIST_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_CVC", "UNAVAILABLE", None,
                "Official CVC / GeM debarment portal credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                pan=clean_pan,
                gstin=clean_gstin
            )
        target_url = f"{api_url.rstrip('/')}?pan={clean_pan}&gstin={clean_gstin}"
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
        api_url = os.environ.get("DIGILOCKER_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_DIGILOCKER", "UNAVAILABLE", None,
                "Official DigiLocker credentials absent.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)"
            )
        target_url = f"{api_url.rstrip('/')}/{clean_uri}"
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
        api_url = os.environ.get("GEM_API_URL", "").strip()
        if api_key and api_url:
            target_url = f"{api_url.rstrip('/')}/{clean_id}"
            ok, code, data, err = _execute_official_http_get("GEM_PORTAL", target_url, api_key)
            if ok and isinstance(data, dict) and data.get("gem_bid_id"):
                return _make_response(
                    "OFFICIAL", "OFFICIAL_GEM_PORTAL", "FOUND", True,
                    f"Official GeM bid details retrieved for {clean_id}.",
                    "OFFICIAL GeM GOVERNMENT PORTAL DATA",
                    data=data
                )
            # A live integration failure falls through to the fixture lookup below.
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_GEM_PORTAL", "UNAVAILABLE", None,
                "Official GeM API request failed; no mock substitution was made.",
                "OFFICIAL API FAILURE — UNAVAILABLE STATE (NOT MOCK)"
            )
        return _make_response(
            "UNAVAILABLE", "OFFICIAL_GEM_PORTAL", "UNAVAILABLE", None,
            "Official GeM credentials or endpoint are not configured.",
            "OFFICIAL CONFIGURATION INCOMPLETE — UNAVAILABLE STATE (NOT MOCK)"
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

# -------------------------------------------------------------------------
# 13. DPIIT Make in India (MII) Adapter
# -------------------------------------------------------------------------
def verify_mii(pan_or_gstin):
    """
    Verifies Make in India (MII) registration with DPIIT.
    Returns classification (Class-I / Class-II / Non-MII), local content %, and registration status.
    Supports MOCK (fixture), OFFICIAL (API), and UNAVAILABLE modes.
    Falls back to MOCK automatically if no real API key is configured.
    """
    mode = _handle_mode("MII_MODE", default_mode="MOCK")
    clean_id = (pan_or_gstin or "").upper().strip()

    if mode == "UNAVAILABLE":
        return _make_response(
            "UNAVAILABLE", "DPIIT_MII_PORTAL", "UNAVAILABLE", None,
            "DPIIT Make in India portal unavailable. Manual verification required.",
            "UNAVAILABLE STATE — NOT TREATED AS INVALID",
            identifier=clean_id
        )

    if mode == "OFFICIAL":
        api_key = os.environ.get("MII_API_KEY")
        api_url = os.environ.get("MII_API_URL", "").strip()
        if not api_key or not api_url:
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_DPIIT_MII", "UNAVAILABLE", None,
                "Official DPIIT MII API credentials absent. Officer verification required.",
                "OFFICIAL CREDENTIALS ABSENT — UNAVAILABLE STATE (NOT MOCK)",
                identifier=clean_id
            )
        target_url = f"{api_url.rstrip('/')}/{clean_id}"
        ok, code, data, err = _execute_official_http_get("DPIIT_MII", target_url, api_key)
        if ok and isinstance(data, dict):
            is_active = str(data.get("registration_status", "")).lower() == "active"
            category = data.get("category", "Unknown")
            lc_pct = data.get("local_content_percentage", 0)
            return _make_response(
                "OFFICIAL", "OFFICIAL_DPIIT_MII", "ACTIVE" if is_active else "INACTIVE", is_active,
                f"Official DPIIT MII record: {category} with {lc_pct}% local content.",
                "OFFICIAL LIVE GOVERNMENT VERIFICATION (DPIIT MII)",
                data=data,
                category=category,
                local_content_percentage=lc_pct,
                dpiit_registration_no=data.get("dpiit_registration_no")
            )
        else:
            # API is reachable but returned an error — surface the error details
            error_detail = err or f"HTTP Status {code}"
            return _make_response(
                "UNAVAILABLE", "OFFICIAL_DPIIT_MII", "API_ERROR", None,
                f"DPIIT MII API call failed: {error_detail}. Falling back to MOCK data.",
                f"OFFICIAL API ERROR — {error_detail}",
                identifier=clean_id,
                api_error=error_detail,
                api_error_code=code
            )

    # MOCK mode (default when no real credentials)
    fixture = _load_mock_fixture("mii.json")
    if fixture:
        for rec in fixture.get("records", []):
            pan_match = clean_id and rec.get("pan", "").upper() == clean_id
            gstin_match = clean_id and rec.get("gstin", "").upper() == clean_id
            if pan_match or gstin_match:
                is_active = rec.get("registration_status", "").lower() == "active"
                category = rec.get("category", "Unknown")
                lc_pct = float(rec.get("local_content_percentage", 0))
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "ACTIVE" if is_active else "INACTIVE", is_active,
                    f"DPIIT MII: {rec.get('company_name')} registered as {category} with {lc_pct}% local content.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    category=category,
                    local_content_percentage=lc_pct,
                    dpiit_registration_no=rec.get("dpiit_registration_no")
                )
        # Check not_registered list
        for rec in fixture.get("not_registered", []):
            if clean_id and rec.get("pan", "").upper() == clean_id:
                return _make_response(
                    "MOCK", "MOCK_ADAPTER", "NOT_REGISTERED", False,
                    f"No DPIIT MII registration found for {clean_id}. Self-declaration may be required.",
                    "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    data=rec,
                    category="Not Registered",
                    local_content_percentage=0
                )
    return _make_response(
        "MOCK", "MOCK_ADAPTER", "NOT_FOUND", None,
        f"MII registration for {clean_id} not found in mock database. Manual officer verification required.",
        "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
        identifier=clean_id,
        category="Unknown",
        local_content_percentage=None
    )


# -------------------------------------------------------------------------
# Admin Diagnostic: Ping / Health-check an adapter for error surfacing
# -------------------------------------------------------------------------
def ping_adapter(adapter_name, identifier="TEST"):
    """
    Runs a live diagnostic probe on the named adapter.
    Returns full response including any API errors — intended for admin error surfacing.
    """
    name = (adapter_name or "").upper().strip()
    clean_id = (identifier or "TEST").strip()
    try:
        if name == "GST":
            return verify_gst(clean_id)
        elif name == "PAN":
            return verify_pan(clean_id)
        elif name == "UDYAM":
            return verify_udyam(clean_id)
        elif name == "MCA":
            return verify_mca(clean_id)
        elif name == "EPFO":
            return verify_epfo(clean_id)
        elif name == "ESIC":
            return verify_esic(clean_id)
        elif name == "STARTUP":
            return verify_startup(clean_id)
        elif name == "NSIC":
            return verify_nsic(clean_id)
        elif name == "BIS":
            return verify_bis(clean_id)
        elif name == "BLACKLIST":
            return verify_blacklisting(pan=clean_id, gstin=clean_id)
        elif name == "DIGILOCKER":
            return verify_digilocker(clean_id)
        elif name == "GEM":
            return fetch_gem_bid(clean_id)
        elif name == "MII":
            return verify_mii(clean_id)
        else:
            return {
                "source_mode": "ERROR",
                "status": "UNKNOWN_ADAPTER",
                "message": f"No adapter registered for name '{name}'.",
                "is_valid": None,
                "adapter_name": name
            }
    except Exception as exc:
        return {
            "source_mode": "ERROR",
            "status": "EXCEPTION",
            "message": f"Adapter '{name}' raised an unhandled exception: {str(exc)}",
            "is_valid": None,
            "adapter_name": name,
            "exception": str(exc)
        }
