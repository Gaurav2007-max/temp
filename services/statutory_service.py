"""Statutory verification service for Indian government identity and registration APIs.
Supports MOCK, OFFICIAL, UNAVAILABLE, and MANUAL adapter states.
Rules:
- Official government APIs must not be fabricated.
- If no authorized integration exists, OFFICIAL = UNAVAILABLE.
- UNAVAILABLE must never be treated as FAIL (routes to manual/officer review).
- Mock data must be clearly tagged: 'MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION'.
"""
import os
import json

from database.db import utc_now_iso

MOCK_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mock_api")

def _load_mock(filename):
    path = os.path.join(MOCK_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

class StatutoryVerificationService:
    @staticmethod
    def verify_gst(gstin, mode="MOCK"):
        """Verify GST registration and return filing status."""
        now_ts = utc_now_iso()
        if mode == "OFFICIAL":
            # Real GST API endpoint would require GSTN API credentials and ASP/GSP integration.
            return {
                "source_mode": "UNAVAILABLE",
                "source": "OFFICIAL_API",
                "status": "UNAVAILABLE",
                "is_valid": None,
                "timestamp": now_ts,
                "message": "Official verification unavailable — manual/officer verification required.",
                "disclaimer": "OFFICIAL GSTN API NOT CONFIGURED IN ENVIRONMENT",
                "data": None
            }
        
        # MOCK ADAPTER
        data = _load_mock("gst.json")
        records = data.get("records", [])
        clean_gstin = gstin.strip().upper() if gstin else ""
        for rec in records:
            if rec.get("gstin", "").upper() == clean_gstin:
                is_act = rec.get("status") == "Active"
                return {
                    "source_mode": "MOCK",
                    "source": "MOCK_ADAPTER",
                    "status": "VALID" if is_act else "CANCELLED",
                    "is_valid": is_act,
                    "timestamp": now_ts,
                    "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
                    "data": rec
                }
        
        return {
            "source_mode": "MOCK",
            "source": "MOCK_ADAPTER",
            "status": "NOT_FOUND",
            "is_valid": False,
            "timestamp": now_ts,
            "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: GSTIN not found in mock database.",
            "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
            "data": None
        }

    @staticmethod
    def verify_pan(pan, mode="MOCK"):
        """Verify PAN registration and IT compliance."""
        now_ts = utc_now_iso()
        if mode == "OFFICIAL":
            return {
                "source_mode": "UNAVAILABLE",
                "source": "OFFICIAL_API",
                "status": "UNAVAILABLE",
                "is_valid": None,
                "timestamp": now_ts,
                "message": "Official verification unavailable — manual/officer verification required.",
                "disclaimer": "OFFICIAL INCOME TAX PAN API NOT CONFIGURED IN ENVIRONMENT",
                "data": None
            }
        
        data = _load_mock("pan.json")
        records = data.get("records", [])
        clean_pan = pan.strip().upper() if pan else ""
        for rec in records:
            if rec.get("pan", "").upper() == clean_pan:
                is_act = rec.get("status") == "Active"
                return {
                    "source_mode": "MOCK",
                    "source": "MOCK_ADAPTER",
                    "status": "VALID" if is_act else "INACTIVE",
                    "is_valid": is_act,
                    "timestamp": now_ts,
                    "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
                    "data": rec
                }
        
        return {
            "source_mode": "MOCK",
            "source": "MOCK_ADAPTER",
            "status": "NOT_FOUND",
            "is_valid": False,
            "timestamp": now_ts,
            "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: PAN not found in mock database.",
            "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
            "data": None
        }

    @staticmethod
    def verify_udyam(udyam_num, mode="MOCK"):
        """Verify Udyam registration."""
        now_ts = utc_now_iso()
        if mode == "OFFICIAL":
            return {
                "source_mode": "UNAVAILABLE",
                "source": "OFFICIAL_API",
                "status": "UNAVAILABLE",
                "is_valid": None,
                "timestamp": now_ts,
                "message": "Official verification unavailable — manual/officer verification required.",
                "disclaimer": "OFFICIAL UDYAM API NOT CONFIGURED",
                "data": None
            }
        
        data = _load_mock("udyam.json")
        records = data.get("records", [])
        clean_udyam = udyam_num.strip().upper() if udyam_num else ""
        for rec in records:
            if rec.get("udyam_registration", "").upper() == clean_udyam:
                is_act = rec.get("status") == "Active"
                return {
                    "source_mode": "MOCK",
                    "source": "MOCK_ADAPTER",
                    "status": "VALID" if is_act else "INACTIVE",
                    "is_valid": is_act,
                    "timestamp": now_ts,
                    "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
                    "data": rec
                }
        
        return {
            "source_mode": "MOCK",
            "source": "MOCK_ADAPTER",
            "status": "NOT_FOUND",
            "is_valid": False,
            "timestamp": now_ts,
            "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: Udyam not found in mock database.",
            "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
            "data": None
        }

    @staticmethod
    def verify_blacklist(pan=None, gstin=None, mode="MOCK"):
        """Verify if entity is blacklisted/debarred by GeM or government ministries."""
        now_ts = utc_now_iso()
        if mode == "OFFICIAL":
            return {
                "source_mode": "UNAVAILABLE",
                "source": "OFFICIAL_API",
                "status": "UNAVAILABLE",
                "is_valid": None,
                "is_blacklisted": None,
                "timestamp": now_ts,
                "message": "Official verification unavailable — manual/officer verification required.",
                "disclaimer": "OFFICIAL CENTRAL DEBARMENT DATABASE API NOT CONFIGURED",
                "data": None
            }
        
        data = _load_mock("blacklist.json")
        records = data.get("records", [])
        c_pan = pan.strip().upper() if pan else ""
        c_gstin = gstin.strip().upper() if gstin else ""
        
        for rec in records:
            if (c_pan and rec.get("pan", "").upper() == c_pan) or (c_gstin and rec.get("gstin", "").upper() == c_gstin):
                return {
                    "source_mode": "MOCK",
                    "source": "MOCK_ADAPTER",
                    "status": "BLACKLISTED",
                    "is_valid": False,
                    "is_blacklisted": True,
                    "timestamp": now_ts,
                    "message": f"MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: Debarred by {rec.get('authority')} until {rec.get('valid_till')}",
                    "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
                    "data": rec
                }
        
        return {
            "source_mode": "MOCK",
            "source": "MOCK_ADAPTER",
            "status": "CLEAR",
            "is_valid": True,
            "is_blacklisted": False,
            "timestamp": now_ts,
            "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: No debarment order found.",
            "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
            "data": None
        }

    @staticmethod
    def verify_bis(license_num, mode="MOCK"):
        """Verify BIS CRS registration number."""
        if mode == "OFFICIAL":
            return {
                "source_mode": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "message": "Official verification unavailable — manual/officer verification required.",
                "disclaimer": "OFFICIAL BIS CRS API NOT CONFIGURED",
                "data": None
            }
        
        data = _load_mock("bis.json")
        records = data.get("records", [])
        c_lic = license_num.strip().upper() if license_num else ""
        for rec in records:
            if rec.get("registration_number", "").upper() == c_lic:
                return {
                    "source_mode": "MOCK",
                    "status": "VALID" if rec.get("status") == "Valid" else "EXPIRED",
                    "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION",
                    "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
                    "data": rec
                }
        return {
            "source_mode": "MOCK",
            "status": "NOT_FOUND",
            "message": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION: BIS number not found.",
            "disclaimer": "MOCK / SAMPLE DATA FOR EVALUATION ONLY",
            "data": None
        }
