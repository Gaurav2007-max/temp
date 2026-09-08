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

def _normalized_field_value(value):
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return re.sub(r"\s+", " ", str(value).strip()).casefold()

def extract_document_fields_consensus(doc_type, text):
    """Compare deterministic and Gemini extraction without allowing silent overrides."""
    deterministic = extract_document_fields_deterministic(doc_type, text)
    llm_fields = extract_document_fields_llm(doc_type, text)
    gemini_used = llm_fields.get("_source") == "GEMINI_LLM_EXTRACTION"

    if not gemini_used:
        deterministic["_extraction_consensus_status"] = "DETERMINISTIC_ONLY"
        deterministic["_extraction_conflicts"] = []
        return deterministic

    conflicts = []
    merged = dict(deterministic)
    for field, gemini_value in llm_fields.items():
        if field.startswith("_") or gemini_value is None or gemini_value == "":
            continue
        deterministic_value = deterministic.get(field)
        if deterministic_value is None or deterministic_value == "":
            merged[field] = gemini_value
            continue
        if _normalized_field_value(deterministic_value) != _normalized_field_value(gemini_value):
            conflicts.append({
                "field": field,
                "deterministic_value": deterministic_value,
                "gemini_value": gemini_value,
                "resolution": "DETERMINISTIC_VALUE_RETAINED"
            })

    merged["_source"] = "CONSENSUS_DETERMINISTIC_WITH_GEMINI"
    merged["_gemini_fields"] = {k: v for k, v in llm_fields.items() if not k.startswith("_")}
    merged["_extraction_conflicts"] = conflicts
    merged["_extraction_consensus_status"] = "CONFLICT" if conflicts else "AGREED"
    return merged

def extract_document_fields_deterministic(doc_type, text):
    """
    Reliable deterministic regex-based field extractor from document text.
    Extracts identifiers, financial years, turnover amounts, experience details,
    OEM parameters, and statutory fields without fabricating values.
    """
    extracted = {"_source": "DETERMINISTIC_PARSER"}
    t = text or ""

    # 1. GSTIN pattern: 2 digits + 5 alpha + 4 digits + 1 alpha + 1 alpha/num + Z + 1 alpha/num
    gst_match = re.search(r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1})\b", t)
    if gst_match:
        extracted["gstin"] = gst_match.group(1)
        extracted["pan"] = gst_match.group(1)[2:12]

    # Standalone PAN pattern: 5 uppercase letters, 4 digits, 1 uppercase letter
    if "pan" not in extracted:
        pan_match = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z]{1})\b", t)
        if pan_match:
            extracted["pan"] = pan_match.group(1)

    # 2. Udyam pattern: UDYAM-XX-00-0000000
    udyam_match = re.search(r"\b(UDYAM-[A-Z]{2}-\d{2}-\d{7})\b", t, re.IGNORECASE)
    if udyam_match:
        extracted["udyam_reg_no"] = udyam_match.group(1).upper()

    # 3. Enterprise / Legal Name
    name_match = re.search(r"(?:Legal\s*Name|Enterprise\s*Name|Name\s*of\s*the\s*Firm|Company\s*Name)\s*[:\-]\s*([A-Za-z0-9\s\.\,\(\)\&]{3,60}?)(?:\n|\r|Trade|Status|PAN|GSTIN|Address|$)", t, re.IGNORECASE)
    if name_match:
        extracted["legal_name"] = name_match.group(1).strip()

    # 4. Registered Address & PIN Code
    pin_match = re.search(r"\b([1-9][0-9]{5})\b", t)
    if pin_match:
        extracted["pin_code"] = pin_match.group(1)

    addr_match = re.search(r"(?:Principal\s*Place\s*of\s*Business|Registered\s*Address|Address)\s*[:\-]?\s*([^\n\r]{10,120})", t, re.IGNORECASE)
    if addr_match:
        extracted["registered_address"] = addr_match.group(1).strip()

    # 5. Local content pattern: e.g. "65% local content", "Percentage of Local Content: 52.0%"
    lc_match = re.search(r"(?:Percentage\s*of\s*Local\s*Content|Local\s*Content(?:\s*Percentage)?)\s*[:\-]?\s*(\d{1,3}(?:\.\d+)?)\s*%", t, re.IGNORECASE)
    if not lc_match:
        lc_match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:local\s*content|indigenous|domestic)", t, re.IGNORECASE)
    if lc_match:
        try:
            extracted["local_content_percentage"] = float(lc_match.group(1))
        except ValueError:
            pass

    # 6. Financial Year and Turnover Amounts
    fy_match = re.search(r"(?:Financial\s*Year|FY)\s*[:\-]?\s*(20\d{2}[-–/]\d{2,4})", t, re.IGNORECASE)
    if not fy_match:
        fy_match = re.search(r"\b(FY\s*20\d{2}[-–/]\d{2,4})\b", t, re.IGNORECASE)
    if fy_match:
        raw_fy = fy_match.group(1).upper().replace(" ", "")
        if not raw_fy.startswith("FY"):
            raw_fy = f"FY{raw_fy}"
        extracted["financial_year"] = raw_fy

    turnover_match = re.search(r"(?:Annual\s*Turnover|Gross\s*Revenue|Gross\s*Receipts|Turnover).*?(?:INR|Rs\.?|₹)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(Cr|Crore|Crores|Lakh|Lakhs)?", t, re.IGNORECASE)
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

    # 7. BIS License pattern: e.g. "CM/L - 1234567" or "R-12345678" or "IS 13252"
    bis_match = re.search(r"(?:CM/L\s*[-:]?\s*(\d+)|R\s*[-:]?\s*(\d{8})|IS\s*(\d+))", t, re.IGNORECASE)
    if bis_match:
        extracted["bis_standard"] = bis_match.group(0).strip()

    # 8. Experience, Work Orders & Completion Certificates
    wo_match = re.search(r"(?:Work\s*Order\s*(?:Number|Ref|No\.?)|PO\s*Number|Purchase\s*Order\s*Ref)\s*[:\-]?\s*([A-Za-z0-9\-_/]+)", t, re.IGNORECASE)
    if wo_match:
        extracted["work_order_no"] = wo_match.group(1).strip()

    client_match = re.search(r"(?:Client|Awarded\s*by|Customer|Purchaser)\s*[:\-]?\s*([^\n\r,]+)", t, re.IGNORECASE)
    if client_match:
        extracted["client_name"] = client_match.group(1).strip()

    val_match = re.search(r"(?:Total\s*Contract\s*Value|Contract\s*Value|Project\s*Value|Order\s*Value).*?(?:INR|Rs\.?|₹)?\s*(\d+(?:,\d+)*(?:\.\d+)?)\s*(Cr|Crore|Crores|Lakh|Lakhs)?", t, re.IGNORECASE)
    if val_match:
        v_str = val_match.group(1).replace(",", "")
        v_unit = (val_match.group(2) or "").lower()
        try:
            v = float(v_str)
            if "cr" in v_unit:
                v *= 10000000
            elif "lakh" in v_unit:
                v *= 100000
            extracted["project_value"] = v
        except ValueError:
            pass

    comp_match = re.search(r"(?:Completion\s*Date|Date\s*of\s*Completion)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2}|\d{2}[-/]\d{2}[-/]\d{4})", t, re.IGNORECASE)
    if comp_match:
        extracted["completion_date"] = comp_match.group(1).strip()
        extracted["is_completed"] = True
    elif "successfully completed" in t.lower() or "satisfactory completion" in t.lower():
        extracted["is_completed"] = True

    # 9. OEM Authorization Parameters
    if "OEM" in doc_type.upper() or "AUTHORIZATION" in doc_type.upper() or "MAF" in t.upper():
        mfg_match = re.search(r"(?:Manufacturer|Issued\s*by|OEM\s*Name|From)\s*[:\-]\s*(?:M/s\.?\s*)?([^\n\r,]+)", t, re.IGNORECASE)
        if mfg_match:
            extracted["oem_name"] = mfg_match.group(1).strip()

        partner_match = re.search(r"(?:Authorized\s*(?:Partner|Bidder|Distributor|Channel\s*Partner)|Authorizes\s+M/s\.?)\s*[:\-]\s*(?:M/s\.?\s*)?([^\n\r,]+)", t, re.IGNORECASE)
        if partner_match:
            extracted["authorized_bidder"] = partner_match.group(1).strip()

        valid_match = re.search(r"(?:Valid\s*Till(?:\s*/\s*Expiry\s*Date)?|Validity|Expires\s*on|Expiry\s*Date)\s*[:\-]?\s*(\d{4}-\d{2}-\d{2})", t, re.IGNORECASE)
        if valid_match:
            extracted["authorization_valid_till"] = valid_match.group(1).strip()

        auth_stmt = re.search(r"(authorizes\s+.*?to\s+bid|authorized\s+to\s+participate|official\s+manufacturer\s+authorization)", t, re.IGNORECASE)
        if auth_stmt:
            extracted["authorization_statement"] = auth_stmt.group(0).strip()
        else:
            extracted["authorization_statement"] = "Authorized to quote, bid, and supply OEM equipment."

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

def extract_tender_requirements_llm(pages):
    """Extract only source-grounded tender requirements when an LLM is configured."""
    client = get_gemini_client()
    if not client or not pages:
        return []

    page_text = "\n\n".join(
        f"PAGE {page.get('page_num', index + 1)}:\n{page.get('text', '')}"
        for index, page in enumerate(pages)
        if page.get("text")
    )
    if not page_text.strip():
        return []

    prompt = f"""You extract procurement eligibility requirements from a tender document.
Return ONLY a JSON array. Do not infer or invent requirements.
Include an item only when the requirement is explicitly stated in the source text.
Every item MUST include title, description, requirement_type, mandatory, source_clause,
source_page, confidence, required_documents, applicability_conditions, and exemption_conditions.
Use requirement_type STATUTORY, FINANCIAL, TECHNICAL, or DOCUMENTARY.
Use source_page as an integer from the provided pages and quote source_clause verbatim.
Set confidence between 0 and 1.

Tender pages:
{page_text[:24000]}
"""
    try:
        response = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            contents=prompt
        )
        raw_text = re.sub(r"^```json\s*|```$", "", response.text.strip(), flags=re.IGNORECASE).strip()
        parsed = json.loads(raw_text)
        if not isinstance(parsed, list):
            return []

        validated = []
        for item in parsed:
            if not isinstance(item, dict) or not item.get("title") or not item.get("source_clause"):
                continue
            try:
                source_page = int(item.get("source_page"))
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0))))
            except (TypeError, ValueError):
                continue
            if source_page < 1:
                continue
            page_source = next(
                (page.get("text", "") for page in pages if int(page.get("page_num", 0) or 0) == source_page),
                ""
            )
            if not page_source or item["source_clause"].strip() not in page_source:
                continue
            item["source_page"] = source_page
            item["confidence"] = confidence
            item["_source"] = "GEMINI_TENDER_EXTRACTION"
            validated.append(item)
        return validated
    except Exception:
        return []


def analyze_document_bundle_llm(bidder_name, documents_summary):
    """
    Analyzes a bidder's complete document bundle for cross-document inconsistencies,
    missing information, and suspicious patterns using Gemini LLM if available.
    Falls back to deterministic rule-based analysis if LLM is unavailable.

    documents_summary: list of dicts with keys: doc_type, original_filename, extracted_fields (dict)
    Returns: dict with keys: inconsistencies (list), missing_fields (list), risk_flags (list), summary (str)
    """
    if not documents_summary:
        return {
            "inconsistencies": [],
            "missing_fields": [],
            "risk_flags": ["No documents submitted for bundle analysis"],
            "summary": "No documents found in submission for cross-document analysis.",
            "source": "NO_DOCUMENTS"
        }

    client = get_gemini_client()
    if client and is_llm_configured():
        # Build a compact summary for the LLM
        doc_entries = []
        for d in documents_summary:
            fields = d.get("extracted_fields") or {}
            entry = {
                "doc_type": d.get("doc_type", "UNKNOWN"),
                "filename": d.get("original_filename", ""),
                "key_fields": {k: v for k, v in fields.items() if not k.startswith("_") and v is not None}
            }
            doc_entries.append(entry)

        prompt = f"""You are a GeM procurement compliance AI assistant.
Analyze the following document bundle submitted by bidder '{bidder_name}' and identify:
1. Cross-document inconsistencies (e.g., different PAN/GSTIN/company names across documents)
2. Missing mandatory fields in documents
3. Risk flags (e.g., expired documents, suspicious values, mismatched entities)

Return ONLY a strict valid JSON object with this exact schema:
{{
  "inconsistencies": ["string description of each inconsistency found"],
  "missing_fields": ["field_name: explanation for each missing critical field"],
  "risk_flags": ["risk description for each flag"],
  "summary": "2-sentence plain-language summary of the bundle quality"
}}

Do NOT output markdown. Output ONLY raw JSON.

Document bundle:
{json.dumps(doc_entries, indent=2)[:6000]}
"""
        try:
            response = client.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
                contents=prompt
            )
            raw_text = re.sub(r"^```json\s*|```$", "", response.text.strip(), flags=re.IGNORECASE).strip()
            result = json.loads(raw_text)
            if isinstance(result, dict):
                result["source"] = "GEMINI_LLM_ANALYSIS"
                return result
        except Exception:
            pass

    # Deterministic fallback: rule-based cross-document consistency checks
    inconsistencies = []
    missing_fields = []
    risk_flags = []

    # Collect all PANs, GSTINs, company names across docs
    seen_pans = {}
    seen_gstins = {}
    seen_names = {}

    for d in documents_summary:
        fields = d.get("extracted_fields") or {}
        fname = d.get("original_filename", d.get("doc_type", "doc"))
        pan = (fields.get("pan") or "").upper().strip()
        gstin = (fields.get("gstin") or "").upper().strip()
        name = (fields.get("legal_name") or fields.get("enterprise_name") or "").strip()

        if pan:
            if pan not in seen_pans:
                seen_pans[pan] = fname
            else:
                pass  # Same PAN — good

        if gstin:
            if gstin not in seen_gstins:
                seen_gstins[gstin] = fname
            else:
                pass

        if name:
            if name not in seen_names:
                seen_names[name] = fname

    # Check PAN consistency
    if len(seen_pans) > 1:
        pans_list = ", ".join(f"'{p}' (in {f})" for p, f in seen_pans.items())
        inconsistencies.append(f"Multiple PAN numbers found across documents: {pans_list}")
        risk_flags.append("PAN mismatch across submitted documents — possible impersonation or clerical error")

    # Check GSTIN consistency
    if len(seen_gstins) > 1:
        gstins_list = ", ".join(f"'{g}' (in {f})" for g, f in seen_gstins.items())
        inconsistencies.append(f"Multiple GSTINs found across documents: {gstins_list}")
        risk_flags.append("GSTIN mismatch across submitted documents")

    # Check entity name consistency
    if len(seen_names) > 2:
        risk_flags.append(f"Multiple entity names ({len(seen_names)}) detected across documents — verify legal entity consistency")

    # Check for missing critical document types
    doc_types_present = {d.get("doc_type") for d in documents_summary}
    if "ITR" not in doc_types_present and "BALANCE_SHEET" not in doc_types_present:
        missing_fields.append("Financial Year ITR/Balance Sheet: No audited financial statements found")
    if "GST_CERTIFICATE" not in doc_types_present:
        missing_fields.append("GST Certificate: GST registration certificate not found in submission")

    summary = (
        f"Bundle analysis for {bidder_name}: {len(documents_summary)} document(s) reviewed. "
        f"Found {len(inconsistencies)} inconsistency(ies), {len(missing_fields)} missing field(s), "
        f"{len(risk_flags)} risk flag(s)."
    )

    return {
        "inconsistencies": inconsistencies,
        "missing_fields": missing_fields,
        "risk_flags": risk_flags,
        "summary": summary,
        "source": "DETERMINISTIC_ANALYSIS"
    }
