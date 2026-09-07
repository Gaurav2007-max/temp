# GeM Bid Compliance Platform

Flask + SQLite decision-support application for document-first GeM bid compliance review. It preserves evidence, portal responses, deterministic eligibility rules, rule-based current-bid risk, clarification, re-verification, tender versions, and officer decisions.

## Architecture

Tender import creates a tender snapshot and structured requirement rows. Tender PDFs are processed through text extraction/OCR, then source-grounded requirements are extracted by the configured LLM or deterministic fallback. Each extracted clause retains page evidence, confidence, extraction source, and a pending human-review state. Officers can approve, edit, add, or reject requirements before verification.

Bidder documents are validated, SHA-256 fingerprinted, duplicate-flagged, expiry-checked, quality-checked, processed through PDF text/OCR, and optionally enriched by schema-validated LLM extraction. Authoritative identifiers are retained from deterministic extraction and official/mock adapter results. The compliance engine evaluates applicability and explicit exemptions, builds a requirement matrix, cross-validates identity/address data, calculates configurable weights, and emits explainable risk/issues. Officers can request clarification, receive corrected documents, create an immutable verification version, and record the final decision.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_USERNAME = "admin@example.test"
$env:ADMIN_PASSWORD = "change-this-password"
python app.py
```

Open `http://localhost:5000`. The application initializes SQLite migrations on startup. For production, set a strong `SECRET_KEY`, use HTTPS, set `COOKIE_SECURE=1`, and use a real WSGI server.

## Environment variables

- `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_NAME`
- `GEMINI_API_KEY` and optional `GEMINI_MODEL`; Gemini is the only implemented LLM provider
- `COOKIE_SECURE=1` when served over HTTPS
- Adapter mode variables such as `GST_MODE=MOCK`, `GST_MODE=UNAVAILABLE`, or `GST_MODE=OFFICIAL`
- If a mode is omitted and its `*_API_KEY` exists, the adapter tries the official integration first. Local development defaults to `MOCK`; explicit `OFFICIAL` mode requires both credentials and an endpoint and returns `UNAVAILABLE` when either is missing or fails.
- Optional provider URL variables are status/configuration signals only. No undocumented government endpoint is invented by this project.
- SMTP/Twilio variables are optional for bidder registration OTP delivery.

## Mock and live modes

Every government-source adapter has a common normalized response contract. Local fixture responses are explicitly marked `MOCK` and include a disclaimer. `OFFICIAL` and `UNAVAILABLE` modes do not silently become compliance failures; they produce an unavailable/review state. GeM supports mock lookup and manual JSON/CSV import. Authorized official integrations must be supplied and reviewed separately before production use.

## Admin assignment workflow

Admins import GeM tenders from the configured official adapter or the mock fixture, create officers, and assign one or more officers to each tender from `/admin/assignments`. Officers see only their assigned tenders, bidder documents, verification reports, clarifications, and decision controls. Admins can inspect all tenders and reports. Every assignment and decision is recorded in the audit log.

Requirement extraction is available through `POST /tenders/<tender_id>/requirements/extract`; review actions use the requirement review routes in `app.py`. Notifications are persisted locally and exposed through `/api/notifications`; email delivery is intentionally optional.

## Verification and security

The requirement matrix separates eligibility, score, current-bid rule-based risk, system recommendation, and the final procurement officer decision. Evidence traces link requirements to rules, documents/pages, portal values, authority, conflicts, and final status. Uploaded files are extension/signature checked, stored with generated names, and served only after bidder ownership or officer authorization. Sessions use HTTP-only/SameSite cookies and authenticated POST forms use CSRF tokens. Do not commit `.env`, credentials, databases, uploads, or generated charts.

## OCR and LLM limitations

OCR records page/source/confidence metadata and poor quality is routed to `WARNING`, `NEEDS_REVIEW`, or `INVALID`; it is not automatically a bidder rejection. LLM output is schema-validated JSON and cannot override authoritative extracted identifiers or government results. LLM/API availability depends on local configuration.

The bundled government adapters are MOCK unless an approved integration is configured. MOCK responses are never represented as official verification. No production government endpoint or credential is included in this repository. Digital signature markers are reported as detected-but-unverified; the system does not claim cryptographic authenticity without a real signature verifier. Applicability and exemption rules are configurable prototype rules and must be validated against applicable procurement policy before production use. Historical bidder intelligence is not implemented because no historical dataset is available.

## Validation

```powershell
python -m compileall -q app.py database services verification
python -m pytest -q
```

Final procurement qualification or disqualification always remains with the authorized Procurement Officer.
