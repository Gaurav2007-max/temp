# B.I.D.S.E.T.U.

Bidder Information &amp; Document Scrutiny Engine for Trustworthy Undertakings

Flask + SQLite decision-support application for document-first GeM bid compliance review. It preserves evidence, portal responses, deterministic eligibility rules, rule-based current-bid risk, clarification, re-verification, tender versions, and officer decisions.

## Architecture

Tender import creates a tender snapshot and structured requirement rows. Tender PDFs are processed through text extraction/OCR, then source-grounded requirements are extracted by the configured LLM or deterministic fallback. Each extracted clause retains page evidence, confidence, extraction source, and a pending human-review state. Officers can approve, edit, add, or reject requirements before verification.

Bidder documents are validated, SHA-256 fingerprinted, duplicate-flagged, expiry-checked, quality-checked, processed through PDF text/OCR, and optionally enriched by schema-validated LLM extraction. Authoritative identifiers are retained from deterministic extraction and official/mock adapter results. The compliance engine evaluates applicability and explicit exemptions, builds a requirement matrix, cross-validates identity/address data, calculates configurable weights, and emits explainable risk/issues. Officers can request clarification, receive corrected documents, create an immutable verification version, and record the final decision.

## Requirement coverage

- Government-source adapters: GeM, GSTN, PAN/Income Tax, Udyam/MSME, MCA21, EPFO, ESIC, Startup India/DPIIT, NSIC, BIS, Make in India/local content, DigiLocker, and blacklisting/debarment.
- Verification engine: tender-specific requirements, statutory checks, document evidence, OCR/LLM extraction, applicability and exemptions, cross-source conflict detection, compliance score, risk level, and recommendations.
- Governance workflow: admin tender import and officer assignment, bidder uploads, clarification and re-verification versions, audit logs, notifications, and officer-only qualification/disqualification decisions.

Official portal credentials and approved endpoints are deployment prerequisites. The bundled adapters run in MOCK mode by default; mock results are clearly marked and must not be treated as live government verification.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:ADMIN_USERNAME = "admin@example.test"
$env:ADMIN_PASSWORD = "change-this-password"
python app.py
```

Open `http://localhost:5000`. The application initializes SQLite migrations on startup. Set `PORT` to use another port. For production, set a strong `SECRET_KEY`, use HTTPS, set `COOKIE_SECURE=1`, and run the WSGI callable with Gunicorn, for example `gunicorn --workers 2 --bind 0.0.0.0:8000 app:app`.

## Environment variables

- `SECRET_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_NAME`
- `PORT` (defaults to `5000`)
- `GEMINI_API_KEY` and optional `GEMINI_MODEL`; Gemini is the only implemented LLM provider
- `EMAIL_NOTIFICATIONS_ENABLED=1` and SMTP settings (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_USE_TLS`) to email officers and bidders. In-app notifications remain enabled independently.
- `COOKIE_SECURE=1` when served over HTTPS
- Adapter mode variables such as `GST_MODE=MOCK`, `GST_MODE=UNAVAILABLE`, or `GST_MODE=OFFICIAL`
- If a mode is omitted and its `*_API_KEY` exists, the adapter tries the official integration first. Local development defaults to `MOCK`; explicit `OFFICIAL` mode requires both credentials and an endpoint and returns `UNAVAILABLE` when either is missing or fails.
- Optional provider URL variables are status/configuration signals only. No undocumented government endpoint is invented by this project.
- Gmail OTP registration uses the Gmail HTTPS API in hosted deployments. Configure `GMAIL_USERNAME`, `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and `GMAIL_REFRESH_TOKEN`. `GMAIL_APP_PASSWORD` is only a local SMTP fallback because Render may block outbound SMTP ports.

## Mock and live modes

Every government-source adapter has a common normalized response contract. Local fixture responses are explicitly marked `MOCK` and include a disclaimer. `OFFICIAL` and `UNAVAILABLE` modes do not silently become compliance failures; they produce an unavailable/review state. GeM supports mock lookup and manual JSON/CSV import. Authorized official integrations must be supplied and reviewed separately before production use.

## Admin assignment workflow

Admins import GeM tenders from the configured official adapter or the mock fixture, create officers, and assign one or more officers to each tender from `/admin/assignments`. Officers see only their assigned tenders, bidder documents, verification reports, clarifications, and decision controls. Admins can inspect all tenders and reports. Every assignment and decision is recorded in the audit log.

Requirement extraction is available through `POST /tenders/<tender_id>/requirements/extract`; review actions use the requirement review routes in `app.py`. Notifications are always persisted locally and exposed through `/api/notifications`. When SMTP is enabled, successful email delivery changes the notification channel to `IN_APP+EMAIL`; SMTP failures do not interrupt bid processing and leave the in-app alert available.

## Verification and security

The requirement matrix separates eligibility, score, current-bid rule-based risk, system recommendation, and the final procurement officer decision. Evidence traces link requirements to rules, documents/pages, portal values, authority, conflicts, and final status. Uploaded files are extension/signature checked, stored with generated names, and served only after bidder ownership or officer authorization. Sessions use HTTP-only/SameSite cookies and authenticated POST forms use CSRF tokens. Do not commit `.env`, credentials, databases, uploads, or generated charts.

## OCR and LLM limitations

OCR records page/source/confidence metadata and poor quality is routed to `WARNING`, `NEEDS_REVIEW`, or `INVALID`; it is not automatically a bidder rejection. LLM output is schema-validated JSON and cannot override authoritative extracted identifiers or government results. LLM/API availability depends on local configuration.

The bundled government adapters are MOCK unless an approved integration is configured. MOCK responses are never represented as official verification. No production government endpoint or credential is included in this repository. Digital signature markers are reported as detected-but-unverified; the system does not claim cryptographic authenticity without a real signature verifier. Applicability and exemption rules are configurable prototype rules and must be validated against applicable procurement policy before production use. Historical bidder intelligence is not implemented because no historical dataset is available.

## Frontend framework

The current templates use Tailwind utility classes loaded from the Tailwind CDN. Bootstrap is not currently interchangeable with those classes: replacing the CDN alone would remove the layout and status styling from the 15 templates. A Bootstrap-only migration therefore requires converting the template class markup and validating every role-specific page; it has not been performed as a partial CDN swap.

## Validation

```powershell
python -m compileall -q app.py database services
python -m pytest -q
```

Final procurement qualification or disqualification always remains with the authorized Procurement Officer.
