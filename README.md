# GeM Bid Compliance Platform

Flask + SQLite decision-support application for document-first GeM bid compliance review. It preserves evidence, portal responses, deterministic eligibility rules, rule-based current-bid risk, clarification, re-verification, tender versions, and officer decisions.

## Architecture

Tender import creates a tender snapshot and structured requirement rows. Bidder documents are validated, quality-checked, processed through PDF text/OCR, and optionally enriched by schema-validated LLM extraction. Authoritative identifiers are retained from deterministic extraction and official/mock adapter results. The compliance engine builds a requirement matrix, cross-validates identity/address data, calculates a score, and emits explainable risk/issues. Officers can request clarification, receive corrected documents, create an immutable verification version, and record the final decision.

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
- `LLM_PROVIDER`, `GEMINI_API_KEY` or `OPENAI_API_KEY`, and the corresponding model variable
- `COOKIE_SECURE=1` when served over HTTPS
- Adapter mode variables such as `GST_MODE=MOCK`, `GST_MODE=UNAVAILABLE`, or `GST_MODE=OFFICIAL`
- Optional provider URL variables are status/configuration signals only. No undocumented government endpoint is invented by this project.
- SMTP/Twilio variables are optional for bidder registration OTP delivery.

## Mock and live modes

Every government-source adapter has a common normalized response contract. Local fixture responses are explicitly marked `MOCK` and include a disclaimer. `OFFICIAL` and `UNAVAILABLE` modes do not silently become compliance failures; they produce an unavailable/review state. GeM supports mock lookup and manual JSON/CSV import. Authorized official integrations must be supplied and reviewed separately before production use.

## Verification and security

The requirement matrix separates eligibility, score, current-bid rule-based risk, system recommendation, and the final procurement officer decision. Evidence traces link requirements to rules, documents/pages, portal values, authority, conflicts, and final status. Uploaded files are extension/signature checked, stored with generated names, and served only after bidder ownership or officer authorization. Sessions use HTTP-only/SameSite cookies and authenticated POST forms use CSRF tokens. Do not commit `.env`, credentials, databases, uploads, or generated charts.

## OCR and LLM limitations

OCR records page/source/confidence metadata and poor quality is routed to `WARNING`, `NEEDS_REVIEW`, or `INVALID`; it is not automatically a bidder rejection. LLM output is schema-validated JSON and cannot override authoritative extracted identifiers or government results. LLM/API availability depends on local configuration.

## Validation

```powershell
python -m compileall -q app.py database services verification
python -m pytest -q
```

Final procurement qualification or disqualification always remains with the authorized Procurement Officer.
