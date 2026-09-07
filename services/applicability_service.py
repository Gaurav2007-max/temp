import json

APPLICABILITY_STATES = ("APPLICABLE", "NOT_APPLICABLE", "EXEMPTED", "WAIVED", "UNKNOWN")


def _json_list(value):
    if not value:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else [parsed]
    except (TypeError, ValueError):
        return [str(value)]


def evaluate_requirement_applicability(requirement, bidder, tender):
    """Evaluate explicit tender conditions without treating an exemption as compliance."""
    requirement = dict(requirement)
    bidder = dict(bidder)
    tender = dict(tender)
    criteria = {}
    try:
        criteria = json.loads(requirement.get("structured_criteria") or "{}")
    except (TypeError, ValueError):
        criteria = {}

    applicability = _json_list(requirement.get("applicability_conditions"))
    exemption = _json_list(requirement.get("exemption_conditions"))
    applicability.extend(criteria.get("applicability_conditions") or [])
    exemption.extend(criteria.get("exemption_conditions") or [])

    applies_to = criteria.get("applies_to") or []
    bidder_type = (bidder.get("bidder_type") or bidder.get("entity_type") or "").lower()
    if applies_to and bidder_type and bidder_type not in {str(value).lower() for value in applies_to}:
        return {"status": "NOT_APPLICABLE", "reason": f"Requirement applies only to bidder types: {', '.join(map(str, applies_to))}."}

    code = requirement.get("code", "")
    preference_codes = {"REQ_UDYAM", "REQ_STARTUP", "REQ_NSIC"}
    identity_field = {
        "REQ_UDYAM": "udyam_reg_no",
        "REQ_STARTUP": "startup_recognition_no",
        "REQ_NSIC": "nsic_registration_no",
    }.get(code)
    if code in preference_codes and not bidder.get(identity_field) and not bool(requirement.get("is_mandatory")):
        return {"status": "NOT_APPLICABLE", "reason": "Optional preference requirement is not claimed by this bidder."}

    if exemption and any(str(condition).lower() in {"msme", "mse", "startup", "nsic", bidder_type} for condition in exemption):
        return {"status": "EXEMPTED", "reason": "Bidder matches an explicit tender exemption condition; officer review remains required."}

    if not applicability and not exemption:
        return {"status": "APPLICABLE", "reason": "No exemption or exclusion condition was specified in the reviewed tender requirement."}

    return {"status": "UNKNOWN", "reason": "Tender applicability conditions require officer review."}
