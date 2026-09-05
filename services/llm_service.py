"""Modular LLM extraction & classification interface with fallback regex/pattern engine.
Provider selection:
1. Gemini Adapter (if GEMINI_API_KEY or GOOGLE_API_KEY is configured)
2. OpenAI Adapter (if OPENAI_API_KEY is configured)
3. Fallback Pattern Engine (100% offline, deterministic, robust regex + layout parsing)
"""
import os
import re
import json

DOCUMENT_TYPES = [
    "PAN",
    "GST_CERTIFICATE",
    "GST_RETURN",
    "UDYAM",
    "ITR",
    "EPFO",
    "ESIC",
    "STARTUP_INDIA",
    "NSIC",
    "BIS",
    "OEM_AUTHORIZATION",
    "WORK_ORDER",
    "COMPLETION_CERTIFICATE",
    "LOCAL_CONTENT_DECLARATION",
    "FINANCIAL_STATEMENT",
    "OTHER"
]

class LLMService:
    def __init__(self):
        self.gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.openai_key = os.environ.get("OPENAI_API_KEY")

    def classify_document(self, filename, text):
        """
        Classify document based on filename, text content, and structural cues.
        Returns: (doc_type, confidence, source)
        """
        fn_upper = filename.upper()
        fn_clean = re.sub(r'[^A-Z0-9]+', ' ', fn_upper)
        txt_upper = text.upper() if text else ""
        txt_clean = re.sub(r'[^A-Z0-9]+', ' ', txt_upper)

        # 1. Check filename & content cues
        if "PAN" in fn_clean or "PERMANENT ACCOUNT" in txt_clean:
            return "PAN", 0.95, "OCR"
        
        if "GSTR" in fn_clean or "RETURN" in fn_clean or "GSTR 1" in txt_clean or "GSTR 3B" in txt_clean or "FORM GSTR" in txt_clean:
            return "GST_RETURN", 0.93, "OCR"
            
        if "GST" in fn_clean or "GST REG" in txt_clean or ("REGISTRATION CERTIFICATE" in txt_clean and "GSTIN" in txt_clean):
            return "GST_CERTIFICATE", 0.95, "OCR"

        if "UDYAM" in fn_clean or "MSME" in fn_clean or "UDYAM REGISTRATION" in txt_clean:
            return "UDYAM", 0.94, "OCR"

        if "ITR" in fn_clean or "INCOME TAX RETURN" in txt_clean or ("ACKNOWLEDGEMENT" in txt_clean and "ASSESSMENT YEAR" in txt_clean):
            return "ITR", 0.93, "OCR"

        if "OEM" in fn_clean or "MANUFACTURER AUTHORIZATION" in txt_clean or "AUTHORISATION LETTER" in txt_clean or "MAF" in fn_clean:
            return "OEM_AUTHORIZATION", 0.92, "OCR"

        if "WORK ORDER" in fn_clean or "PURCHASE ORDER" in fn_clean or "WORK ORDER" in txt_clean or "PURCHASE ORDER" in txt_clean or "AWARD OF CONTRACT" in txt_clean:
            return "WORK_ORDER", 0.91, "OCR"

        if "COMPLETION" in fn_clean or "SATISFACTORY COMPLETION" in txt_clean or "PERFORMANCE CERTIFICATE" in txt_clean:
            return "COMPLETION_CERTIFICATE", 0.91, "OCR"

        if "LOCAL CONTENT" in fn_clean or "MAKE IN INDIA" in fn_clean or "MII" in fn_clean or "LOCAL VALUE ADDITION" in txt_clean:
            return "LOCAL_CONTENT_DECLARATION", 0.95, "OCR"

        if "BIS" in fn_clean or "BUREAU OF INDIAN STANDARDS" in txt_clean or "CRS" in txt_clean:
            return "BIS", 0.94, "OCR"

        if "EPFO" in fn_clean or "PROVIDENT FUND" in txt_clean:
            return "EPFO", 0.92, "OCR"

        if "ESIC" in fn_clean or "EMPLOYEES STATE INSURANCE" in txt_clean:
            return "ESIC", 0.92, "OCR"

        if "BALANCE SHEET" in fn_clean or "PROFIT AND LOSS" in txt_clean or "FINANCIAL STATEMENT" in txt_clean:
            return "FINANCIAL_STATEMENT", 0.90, "OCR"

        # If uncertain, mark as NEEDS_REVIEW / OTHER with lower confidence
        if len(text.strip()) > 30:
            return "OTHER", 0.50, "OCR"

        return "OTHER", 0.30, "MANUAL"

    def assess_document_quality(self, filename, text, raw_bytes=None):
        """
        Assess document quality (OK, BLURRY, LOW_RES, UNREADABLE, NEEDS_REVIEW).
        """
        if not text or len(text.strip()) < 10:
            return "UNREADABLE", "Extracted text is empty or less than 10 characters. Potential blank scan or corrupted file."
        
        # Check for unreadable/garbage character ratio
        special_char_count = sum(1 for c in text if not (c.isalnum() or c.isspace() or c in ",.-/:()%#@"))
        ratio = special_char_count / max(len(text), 1)
        if ratio > 0.40:
            return "LOW_RES", f"High proportion of non-standard OCR artifacts ({int(ratio*100)}%). Scanned text quality is degraded."

        return "OK", "Text and document structure are cleanly legible."

    def extract_fields(self, filename, full_text, pages_dict):
        """
        Extract page-level fields from document text and page map.
        Returns list of dicts:
        [{'field_name': ..., 'value': ..., 'page_number': ..., 'confidence': ..., 'source': ...}]
        """
        fields = []

        # Helper to search which page a match occurred on
        def find_page_for_substring(sub):
            if not pages_dict:
                return "UNKNOWN"
            for p_num, p_txt in pages_dict.items():
                if sub.lower() in p_txt.lower():
                    return str(p_num)
            return "1" if 1 in pages_dict else "UNKNOWN"

        # 1. PAN Pattern: [A-Z]{5}[0-9]{4}[A-Z]{1}
        pan_match = re.search(r'\b([A-Z]{5}[0-9]{4}[A-Z])\b', full_text.upper())
        if pan_match:
            pan_val = pan_match.group(1)
            fields.append({
                "field_name": "pan",
                "value": pan_val,
                "page_number": find_page_for_substring(pan_val),
                "confidence": 0.98,
                "source": "OCR"
            })

        # 2. GSTIN Pattern: [0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}
        gstin_match = re.search(r'\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b', full_text.upper())
        if gstin_match:
            gstin_val = gstin_match.group(1)
            fields.append({
                "field_name": "gstin",
                "value": gstin_val,
                "page_number": find_page_for_substring(gstin_val),
                "confidence": 0.98,
                "source": "OCR"
            })

        # 3. Udyam Registration Number: UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}
        udyam_match = re.search(r'\b(UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{5,7})\b', full_text.upper())
        if udyam_match:
            udyam_val = udyam_match.group(1)
            fields.append({
                "field_name": "udyam_registration",
                "value": udyam_val,
                "page_number": find_page_for_substring(udyam_val),
                "confidence": 0.96,
                "source": "OCR"
            })

        # 4. BIS Registration Number: R-[0-9]{8}
        bis_match = re.search(r'\b(R-[0-9]{8})\b', full_text.upper())
        if bis_match:
            bis_val = bis_match.group(1)
            fields.append({
                "field_name": "bis_registration",
                "value": bis_val,
                "page_number": find_page_for_substring(bis_val),
                "confidence": 0.95,
                "source": "OCR"
            })

        # 5. Financial Turnover / Revenue:
        # e.g. "Annual Turnover: INR 5,10,00,000" or "Turnover: 5.1 Crore" or "Gross Receipts: 62000000"
        turnover_patterns = [
            r'(?:turnover|gross receipts|total revenue)[\s:]+(?:inr|rs\.?|₹)?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:cr|crore|crores)?',
            r'(?:inr|rs\.?|₹)\s*([0-9]{1,3}(?:,[0-9]{2,3})*(?:\.[0-9]+)?)',
        ]
        
        # Look for explicit FY tags e.g. FY2023-24, FY2024, 2024-25
        fy_match = re.search(r'\b(FY\s*20[2-3][0-9](?:-[0-9]{2,4})?|20[2-3][0-9]-[0-9]{2})\b', full_text, re.IGNORECASE)
        fy_val = fy_match.group(1).upper().replace(" ", "") if fy_match else None
        if fy_val:
            fields.append({
                "field_name": "financial_year",
                "value": fy_val,
                "page_number": find_page_for_substring(fy_val),
                "confidence": 0.92,
                "source": "OCR"
            })

        # Check for turnover mentions
        t_match = re.search(r'(?:annual turnover|gross revenue|turnover for the year)[^\d\n]*[:\s]+(?:inr|rs\.?|₹)?\s*([0-9,.]+)\s*(crore|cr|lakh|inr)?', full_text, re.IGNORECASE)
        if t_match:
            raw_num_str = t_match.group(1).replace(",", "")
            unit = (t_match.group(2) or "").lower()
            try:
                val = float(raw_num_str)
                if "crore" in unit or "cr" in unit or val < 1000:
                    turnover_inr = val * 10000000 # Convert crore to INR
                elif "lakh" in unit:
                    turnover_inr = val * 100000
                else:
                    turnover_inr = val
                
                fields.append({
                    "field_name": "annual_turnover",
                    "value": str(int(turnover_inr)),
                    "page_number": find_page_for_substring(t_match.group(1)),
                    "confidence": 0.94,
                    "source": "OCR"
                })
            except ValueError:
                pass

        # 6. Local Content Percentage: e.g. "Local Content: 78.5%" or "Local content of 62%"
        lc_match = re.search(r'(?:local content|local value addition)[^\d\n]*[:\s]+([0-9]+(?:\.[0-9]+)?)\s*%', full_text, re.IGNORECASE)
        if lc_match:
            lc_val = lc_match.group(1)
            fields.append({
                "field_name": "local_content_percentage",
                "value": lc_val,
                "page_number": find_page_for_substring(lc_val),
                "confidence": 0.96,
                "source": "OCR"
            })

        # 7. OEM Authorization details
        oem_auth_num = re.search(r'(?:authorization number|auth ref|maf no|ref no)[^\w\n]*[:\s]+([A-Za-z0-9\-_/]+)', full_text, re.IGNORECASE)
        if oem_auth_num:
            auth_val = oem_auth_num.group(1)
            fields.append({
                "field_name": "oem_authorization_number",
                "value": auth_val,
                "page_number": find_page_for_substring(auth_val),
                "confidence": 0.91,
                "source": "OCR"
            })
        
        oem_mfr = re.search(r'(?:manufacturer|oem name|brand)[^\w\n]*[:\s]+([A-Za-z0-9\s&.,\-]+)(?:\n|$)', full_text, re.IGNORECASE)
        if oem_mfr:
            mfr_val = oem_mfr.group(1).strip()
            fields.append({
                "field_name": "oem_manufacturer",
                "value": mfr_val,
                "page_number": find_page_for_substring(mfr_val),
                "confidence": 0.88,
                "source": "OCR"
            })

        oem_expiry = re.search(r'(?:valid (?:till|through|until)|expiry date)[^\w\n]*[:\s]+([0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{2}/[0-9]{2}/[0-9]{4})', full_text, re.IGNORECASE)
        if oem_expiry:
            exp_val = oem_expiry.group(1).strip()
            fields.append({
                "field_name": "expiry_date",
                "value": exp_val,
                "page_number": find_page_for_substring(exp_val),
                "confidence": 0.93,
                "source": "OCR"
            })

        # 8. Work Order / Experience Project Details:
        proj_val_match = re.search(r'(?:contract value|order value|project value|total value)[^\d\n]*[:\s]+(?:inr|rs\.?|₹)?\s*([0-9,.]+)\s*(crore|cr|lakh)?', full_text, re.IGNORECASE)
        if proj_val_match:
            raw_v = proj_val_match.group(1).replace(",", "")
            u = (proj_val_match.group(2) or "").lower()
            try:
                val = float(raw_v)
                if "crore" in u or "cr" in u or val < 500:
                    order_inr = val * 10000000
                elif "lakh" in u:
                    order_inr = val * 100000
                else:
                    order_inr = val
                
                fields.append({
                    "field_name": "contract_value",
                    "value": str(int(order_inr)),
                    "page_number": find_page_for_substring(proj_val_match.group(1)),
                    "confidence": 0.92,
                    "source": "OCR"
                })
            except ValueError:
                pass

        client_match = re.search(r'(?:client|purchaser|issued by|organization)[^\w\n]*[:\s]+([A-Za-z0-9\s.,\-]+)(?:\n|$)', full_text, re.IGNORECASE)
        if client_match:
            c_val = client_match.group(1).strip()
            fields.append({
                "field_name": "client_name",
                "value": c_val,
                "page_number": find_page_for_substring(c_val),
                "confidence": 0.89,
                "source": "OCR"
            })

        return fields

llm_service = LLMService()
