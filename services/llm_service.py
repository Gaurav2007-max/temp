import os
import json
import re

def is_llm_configured():
    api_key = os.environ.get("GEMINI_API_KEY")
    return bool(api_key and len(api_key.strip()) > 5)

def get_gemini_client():
    if not is_llm_configured():
        return None
    try:
        from google import genai
        return genai.Client(api_key=os.environ.get("GEMINI_API_KEY").strip())
    except Exception:
        return None

def extract_document_fields_llm(doc_type, text):
    """
    Extracts structured fields from raw document text using Gemini LLM if configured.
    Falls back to deterministic regex extraction if LLM is unavailable or fails.
    LLM results never override authoritative identifiers.
    """
    if not text or not text.strip():
        return {}

    client = get_gemini_client()
    model_name = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    if client:
        prompt = f"""You are an AI document parser for GeM procurement compliance.
Analyze the following document text for document type '{doc_type}'.
Extract key fields as a strict, valid JSON object.
Do NOT output markdown code blocks (such as ```json). Output ONLY raw JSON.

Fields to extract if present:
- legal_name or enterprise_name
- gstin (15 characters)
- pan (10 characters)
- udyam_reg_no
- financial_year (e.g. FY2023-24)
- turnover_amount (numeric INR)
- local_content_percentage (number 0-100)
- oem_name
- authorized_bidder
- authorization_valid_till
- bis_standard
- project_name
- project_value (numeric INR)
- completion_date
- registered_address

Document text:
{text[:4000]}
"""
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            raw_text = response.text.strip()
            # Remove any markdown backticks if returned
            clean_json = re.sub(r"^```json\s*", "", raw_text, flags=re.IGNORECASE)
            clean_json = re.sub(r"```$", "", clean_json.strip()).strip()
            data = json.loads(clean_json)
            if isinstance(data, dict):
                data["_source"] = "GEMINI_LLM_EXTRACTION"
                return data
        except Exception:
            # Fall back to deterministic extraction
            pass

    # Deterministic fallback extraction
    return extract_document_fields_deterministic(doc_type, text)

def extract_document_fields_deterministic(doc_type, text):
    """
    Reliable deterministic regex-based field extractor from document text.
    """
    extracted = {"_source": "DETERMINISTIC_PARSER"}
    t = text or ""

    # GSTIN pattern: 2 digits + 5 alpha + 4 digits + 1 alpha + 1 alpha/num + Z + 1 alpha/num
    gst_match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", t)
    if gst_match:
        extracted["gstin"] = gst_match.group(1)
        extracted["pan"] = gst_match.group(1)[2:12]

    # Standalone PAN pattern: 5 uppercase letters, 4 digits, 1 uppercase letter
    if "pan" not in extracted:
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b", t)
        if pan_match:
            extracted["pan"] = pan_match.group(1)

    # Udyam pattern: UDYAM-XX-00-0000000
    udyam_match = re.search(r"\b(UDYAM-[A-Z]{2}-\d{2}-\d{7})\b", t, re.IGNORECASE)
    if udyam_match:
        extracted["udyam_reg_no"] = udyam_match.group(1).upper()

    # Local content pattern: e.g. "65% local content", "Class-I Local Supplier (65%)"
    lc_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:local\s*content|indigenous|domestic)", t, re.IGNORECASE)
    if lc_match:
        try:
            extracted["local_content_percentage"] = float(lc_match.group(1))
        except ValueError:
            pass

    # Financial year and turnover amounts
    fy_match = re.search(r"(FY\s*20\d{2}[-–/]\d{2,4}|20\d{2}[-–/]\d{2,4})", t, re.IGNORECASE)
    if fy_match:
        extracted["financial_year"] = fy_match.group(1).upper()

    turnover_match = re.search(r"(?:turnover|gross\s*receipts|revenue).*?(?:INR|Rs\.?|₹)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(Cr|Crore|Lakh|Lakhs)?", t, re.IGNORECASE)
    if turnover_match:
        num_str = turnover_match.group(1).replace(",", "")
        unit = (turnover_match.group(2) or "").lower()
        try:
            val = float(num_str)
            if "cr" in unit:
                val *= 10000000
            elif "lakh" in unit:
                val *= 100000
            extracted["turnover_amount"] = val
        except ValueError:
            pass

    # BIS License pattern: e.g. "CM/L - 1234567" or "R-12345678" or "IS 13252"
    bis_match = re.search(r"(?:CM/L\s*[-:]?\s*(\d+)|R\s*[-:]?\s*(\d{8})|IS\s*(\d+))", t, re.IGNORECASE)
    if bis_match:
        extracted["bis_standard"] = bis_match.group(0).strip()

    # OEM Authorization keyword detection
    if "OEM" in doc_type.upper() or "AUTHORIZATION" in doc_type.upper():
        oem_match = re.search(r"(?:authorized\s*distributor|channel\s*partner|authorizes\s+M/s\.?\s+([^,\n\.]+))", t, re.IGNORECASE)
        if oem_match:
            extracted["authorized_bidder"] = oem_match.group(1).strip()
        oem_name_match = re.search(r"(?:from\s+M/s\.?\s+([^,\n\.]+)|issued\s+by\s+([^,\n\.]+))", t, re.IGNORECASE)
        if oem_name_match:
            extracted["oem_name"] = (oem_name_match.group(1) or oem_name_match.group(2) or "").strip()

    return extracted

def generate_ai_compliance_explanation(bidder_name, score, eligibility, risk, issues):
    """
    Generates human-readable compliance summary and recommendations using LLM if available,
    or clear deterministic text otherwise.
    """
    client = get_gemini_client()
    if client and is_llm_configured():
        prompt = f"""Generate a concise, professional 2-paragraph decision-support assessment for a GeM Procurement Officer.
Bidder: {bidder_name}
Compliance Score: {score}/100
Mandatory Eligibility Status: {eligibility}
Current-bid Risk Level: {risk}
Identified Issues/Gaps: {', '.join(issues) if issues else 'None'}

Note: Final decision remains with the Procurement Officer. State this clearly.
"""
        try:
            res = client.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                contents=prompt
            )
            return res.text.strip()
        except Exception:
            pass

    # Deterministic fallback text
    recommendation = "RECOMMENDED FOR QUALIFICATION" if eligibility == "ELIGIBLE" and risk != "HIGH" else "REQUIRES OFFICER SCRUTINY OR CLARIFICATION"
    if eligibility == "NOT_ELIGIBLE":
        recommendation = "RECOMMENDED FOR DISQUALIFICATION DUE TO MANDATORY NON-COMPLIANCE"

    issue_str = f" Key concerns: {'; '.join(issues)}." if issues else " All statutory and technical checks passed satisfactorily."
    return f"System Assessment for {bidder_name}: Overall Compliance Score is {score}/100 with {risk} risk and eligibility '{eligibility}'.{issue_str} Final qualification decision remains strictly with the Procurement Officer."
