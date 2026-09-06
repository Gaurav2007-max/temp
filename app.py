import os
import secrets
import json
from datetime import datetime
from functools import wraps
from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file, abort, g, make_response
)
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from database.db import get_db, close_db, init_db, query_db, execute_db
from services.seed_data import seed_database
from services.statutory_service import (
    verify_gst, verify_pan, verify_udyam, verify_mca, verify_epfo,
    verify_esic, verify_startup, verify_nsic, verify_bis,
    verify_blacklisting, verify_digilocker, fetch_gem_bid
)
from services.document_service import (
    save_and_process_uploaded_documents,
    get_documents_by_bidder_and_tender
)
from services.verification_engine import (
    run_bidder_verification,
    get_latest_verification
)
from services.tender_service import (
    create_tender,
    import_tender_from_gem,
    is_bidding_open,
    update_tender_lifecycle_stage,
    create_corrigendum
)
from services.clarification_service import (
    create_clarification_request,
    submit_clarification_response,
    get_clarifications_by_tender,
    get_clarifications_by_bidder
)
from services.audit_service import log_audit_event, get_recent_audit_logs

from flask.sessions import SecureCookieSessionInterface

class IFrameSecureSessionInterface(SecureCookieSessionInterface):
    """
    Guarantees cookies persist in cross-site preview iframes (AI Studio):
    Forces SameSite=None; Secure; Partitioned
    """
    def get_cookie_secure(self, app):
        return True

    def get_cookie_samesite(self, app):
        return "None"

    def get_cookie_partitioned(self, app):
        return True

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.session_interface = IFrameSecureSessionInterface()
app.secret_key = os.environ.get("SECRET_KEY", "Hello@2026")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "None"
app.config["SESSION_COOKIE_PARTITIONED"] = True
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024 # 50 MB max payload

# Token serializers for stateless resilience in iframes
csrf_serializer = URLSafeTimedSerializer(app.secret_key, salt="gem-csrf-token")
auth_serializer = URLSafeTimedSerializer(app.secret_key, salt="gem-auth-session")

# Teardown database connection
app.teardown_appcontext(close_db)

# -------------------------------------------------------------------------
# CSRF & Security Helpers
# -------------------------------------------------------------------------
def generate_csrf_token():
    if "_csrf_token" not in session:
        uid = session.get("user_id", 0)
        session["_csrf_token"] = csrf_serializer.dumps({"uid": uid, "nonce": secrets.token_hex(12)})
    return session["_csrf_token"]

def get_current_auth_token():
    if "user_id" in session:
        return auth_serializer.dumps({
            "user_id": session["user_id"],
            "user_role": session.get("user_role"),
            "user_name": session.get("user_name")
        })
    return ""

@app.context_processor
def inject_globals():
    user = None
    if "user_id" in session:
        user = query_db("SELECT id, username, name, email, role FROM users WHERE id = ?", (session["user_id"],), one=True)
    return {
        "current_user": user,
        "csrf_token": generate_csrf_token,
        "auth_token": get_current_auth_token(),
        "config_get": lambda key, default="": os.environ.get(key, default)
    }

@app.before_request
def restore_session_from_token():
    """
    If third-party cookies are blocked by the browser inside the iframe,
    restore user session from signed auth_token passed via query, form, or header.
    """
    if "user_id" in session:
        return

    raw_token = (
        request.headers.get("X-Auth-Token") or
        request.args.get("auth_token") or
        request.form.get("auth_token") or
        request.cookies.get("gem_auth_token")
    )
    if raw_token:
        try:
            payload = auth_serializer.loads(raw_token, max_age=7 * 86400)
            if isinstance(payload, dict) and "user_id" in payload:
                session["user_id"] = payload["user_id"]
                session["user_role"] = payload.get("user_role")
                session["user_name"] = payload.get("user_name")
        except (BadSignature, SignatureExpired):
            pass

@app.before_request
def csrf_protect():
    """
    Verifies CSRF token on modifying HTTP methods.
    Supports session tokens and cryptographically signed tokens (for iframe resilience).
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.path.startswith("/api/public/"):
            return
        token = request.form.get("csrf_token") or request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        if not token:
            abort(400, description="CSRF token missing. Please refresh the page and try again.")

        # 1. Match session token if present
        if session.get("_csrf_token") and token == session.get("_csrf_token"):
            return

        # 2. Cryptographic signature check (handles cases where browser dropped session cookie)
        try:
            payload = csrf_serializer.loads(token, max_age=86400)
            if isinstance(payload, dict) and "nonce" in payload:
                return
        except (BadSignature, SignatureExpired):
            abort(400, description="CSRF validation failed or session expired. Please refresh the page and try again.")

# -------------------------------------------------------------------------
# Authentication & Authorization Decorators
# -------------------------------------------------------------------------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to access this page.", "error")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function

def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "user_id" not in session:
                flash("Please sign in to access this page.", "error")
                return redirect(url_for("login"))
            user_role = session.get("user_role")
            if user_role not in allowed_roles:
                abort(403, description="Access forbidden: Insufficient permissions.")
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# -------------------------------------------------------------------------
# Core Navigation Routes
# -------------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        role = session.get("user_role")
        if role == "admin":
            return redirect(url_for("admin_dashboard"))
        elif role == "officer":
            return redirect(url_for("officer_dashboard"))
        elif role == "bidder":
            return redirect(url_for("bidder_dashboard"))
    return redirect(url_for("login"))

# -------------------------------------------------------------------------
# Authentication Routes
# -------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        user = query_db(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
            (username, username),
            one=True
        )

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_role"] = user["role"]
            generate_csrf_token()

            auth_token = auth_serializer.dumps({
                "user_id": user["id"],
                "user_role": user["role"],
                "user_name": user["name"]
            })

            log_audit_event("USER_LOGIN", "users", user["id"], f"Logged in as {user['role']}", actor=user)
            flash(f"Welcome back, {user['name']}.", "success")

            target_route = "admin_dashboard" if user["role"] == "admin" else (
                "officer_dashboard" if user["role"] == "officer" else "bidder_dashboard"
            )
            target_url = url_for(target_route, auth_token=auth_token)
            resp = make_response(redirect(target_url))
            resp.set_cookie(
                "gem_auth_token",
                auth_token,
                secure=True,
                httponly=False,
                samesite="None",
                partitioned=True,
                max_age=7 * 86400
            )
            return resp
        else:
            flash("Invalid credentials. Please verify your email/username and password.", "error")

    demo_admin_user = os.environ.get("ADMIN_USERNAME", "aroraganesh2007@gmail.com")
    demo_admin_pass = os.environ.get("ADMIN_PASSWORD", "2026")
    return render_template("login.html", demo_admin_user=demo_admin_user, demo_admin_pass=demo_admin_pass)

@app.route("/logout", methods=["GET", "POST"])
def logout():
    user_id = session.get("user_id")
    if user_id:
        log_audit_event("USER_LOGOUT", "users", user_id, "User logged out")
    session.clear()
    flash("You have been signed out successfully.", "success")
    resp = make_response(redirect(url_for("login", logout=1)))
    resp.delete_cookie("gem_auth_token", path="/")
    resp.delete_cookie("session", path="/")
    return resp

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        phone = request.form.get("phone", "").strip()
        company_name = request.form.get("company_name", "").strip()
        gstin = request.form.get("gstin", "").strip().upper()
        pan = request.form.get("pan", "").strip().upper()
        udyam_reg_no = request.form.get("udyam_reg_no", "").strip().upper()
        registered_address = request.form.get("registered_address", "").strip()

        if not (email and password and company_name):
            flash("Please fill all mandatory fields.", "error")
            return render_template("bidder_register.html")

        # Check existing email
        existing = query_db("SELECT id FROM users WHERE email = ? OR username = ?", (email, email), one=True)
        if existing:
            flash("An account with this email address already exists. Please sign in.", "error")
            return redirect(url_for("login"))

        # Create user record
        user_id = execute_db(
            """
            INSERT INTO users (username, password_hash, name, email, role, phone)
            VALUES (?, ?, ?, ?, 'bidder', ?)
            """,
            (email, generate_password_hash(password), name, email, phone)
        )

        # Create bidder profile
        execute_db(
            """
            INSERT INTO bidders (
                user_id, company_name, pan, gstin, udyam_reg_no, registered_address,
                contact_person, phone, email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, company_name, pan, gstin, udyam_reg_no, registered_address, name, phone, email)
        )

        session["user_id"] = user_id
        session["user_name"] = name
        session["user_role"] = "bidder"
        generate_csrf_token()

        auth_token = auth_serializer.dumps({
            "user_id": user_id,
            "user_role": "bidder",
            "user_name": name
        })

        log_audit_event("BIDDER_REGISTER", "bidders", user_id, f"Registered company {company_name}")
        flash("Enterprise registration successful. Welcome to GeM Bid Compliance Platform!", "success")
        resp = make_response(redirect(url_for("bidder_dashboard", auth_token=auth_token)))
        resp.set_cookie(
            "gem_auth_token",
            auth_token,
            secure=True,
            httponly=False,
            samesite="None",
            partitioned=True,
            max_age=7 * 86400
        )
        return resp

    return render_template("bidder_register.html")

# -------------------------------------------------------------------------
# Admin Routes
# -------------------------------------------------------------------------
@app.route("/admin")
@login_required
@role_required("admin")
def admin_dashboard():
    tenders_count = query_db("SELECT COUNT(*) as c FROM tenders", one=True)["c"]
    officers_count = query_db("SELECT COUNT(*) as c FROM users WHERE role = 'officer'", one=True)["c"]
    bidders_count = query_db("SELECT COUNT(*) as c FROM bidders", one=True)["c"]
    audits_count = query_db("SELECT COUNT(*) as c FROM audit_logs", one=True)["c"]

    tenders = query_db(
        """
        SELECT t.*,
               GROUP_CONCAT(u.name, ', ') as assigned_officers
        FROM tenders t
        LEFT JOIN tender_assignments ta ON t.id = ta.tender_id
        LEFT JOIN users u ON ta.officer_id = u.id
        GROUP BY t.id
        ORDER BY t.id DESC
        """
    )

    audit_logs = get_recent_audit_logs(limit=30)

    stats = {
        "total_tenders": tenders_count,
        "total_officers": officers_count,
        "total_bidders": bidders_count,
        "total_audits": audits_count
    }

    return render_template("admin_dashboard.html", stats=stats, tenders=tenders, audit_logs=audit_logs)

@app.route("/admin/officers")
@login_required
@role_required("admin")
def admin_officers():
    officers = query_db("SELECT id, name, email, phone, created_at FROM users WHERE role = 'officer' ORDER BY id DESC")
    return render_template("admin_officers.html", officers=officers)

@app.route("/admin/officers/add", methods=["POST"])
@login_required
@role_required("admin")
def admin_add_officer():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    phone = request.form.get("phone", "").strip()

    if not (name and email and password):
        flash("Name, email and password are required.", "error")
        return redirect(url_for("admin_officers"))

    existing = query_db("SELECT id FROM users WHERE email = ?", (email,), one=True)
    if existing:
        flash("User with this email already exists.", "error")
        return redirect(url_for("admin_officers"))

    execute_db(
        """
        INSERT INTO users (username, password_hash, name, email, role, phone)
        VALUES (?, ?, ?, ?, 'officer', ?)
        """,
        (email, generate_password_hash(password), name, email, phone)
    )

    log_audit_event("OFFICER_CREATED", "users", None, f"Created officer {name} ({email})")
    flash(f"Officer {name} registered successfully.", "success")
    return redirect(url_for("admin_officers"))

@app.route("/admin/assignments")
@login_required
@role_required("admin")
def admin_assignments():
    tenders = query_db("SELECT id, gem_bid_id, title FROM tenders ORDER BY id DESC")
    officers = query_db("SELECT id, name, email FROM users WHERE role = 'officer' ORDER BY name ASC")
    assignments = query_db(
        """
        SELECT ta.id, ta.assigned_at, t.gem_bid_id, t.title as tender_title, u.name as officer_name
        FROM tender_assignments ta
        JOIN tenders t ON ta.tender_id = t.id
        JOIN users u ON ta.officer_id = u.id
        ORDER BY ta.id DESC
        """
    )
    return render_template("admin_assignments.html", tenders=tenders, officers=officers, assignments=assignments)

@app.route("/admin/assignments/add", methods=["POST"])
@login_required
@role_required("admin")
def admin_add_assignment():
    tender_id = request.form.get("tender_id")
    officer_id = request.form.get("officer_id")

    if not (tender_id and officer_id):
        flash("Tender and officer must both be selected.", "error")
        return redirect(url_for("admin_assignments"))

    execute_db(
        "INSERT OR IGNORE INTO tender_assignments (tender_id, officer_id, assigned_by) VALUES (?, ?, ?)",
        (tender_id, officer_id, session.get("user_id"))
    )

    log_audit_event("TENDER_ASSIGNED", "tenders", tender_id, f"Assigned tender #{tender_id} to officer #{officer_id}")
    flash("Tender assigned to officer successfully.", "success")
    return redirect(url_for("admin_assignments"))

@app.route("/admin/assignments/delete/<int:assignment_id>", methods=["POST"])
@login_required
@role_required("admin")
def admin_delete_assignment(assignment_id):
    execute_db("DELETE FROM tender_assignments WHERE id = ?", (assignment_id,))
    log_audit_event("TENDER_UNASSIGNED", "tender_assignments", assignment_id, "Removed assignment")
    flash("Assignment removed.", "success")
    return redirect(url_for("admin_assignments"))

@app.route("/api/status")
@login_required
def api_status():
    adapters = [
        {"name": "Goods & Services Tax Network (GSTN)", "mode": os.environ.get("GST_MODE", "MOCK"), "description": "Active registration & GSTR filing verification", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Income Tax Department (PAN)", "mode": os.environ.get("PAN_MODE", "MOCK"), "description": "PAN authenticity & income tax compliance dues", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Udyam MSME Portal", "mode": os.environ.get("UDYAM_MODE", "MOCK"), "description": "Micro/Small/Medium enterprise classification", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Ministry of Corporate Affairs (MCA)", "mode": os.environ.get("MCA_MODE", "MOCK"), "description": "Company active status & registered office", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Employees' Provident Fund Organization (EPFO)", "mode": os.environ.get("EPFO_MODE", "MOCK"), "description": "Establishment compliance & active member verification", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Employees' State Insurance (ESIC)", "mode": os.environ.get("ESIC_MODE", "MOCK"), "description": "Employer code & statutory insurance compliance", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Startup India (DPIIT)", "mode": os.environ.get("STARTUP_MODE", "MOCK"), "description": "DIPP startup recognition & exemption qualification", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "National Small Industries Corporation (NSIC)", "mode": os.environ.get("NSIC_MODE", "MOCK"), "description": "Single Point Registration Scheme (SPRS)", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "Bureau of Indian Standards (BIS)", "mode": os.environ.get("BIS_MODE", "MOCK"), "description": "CRS / ISI license status & standards validity", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "CVC / GeM Central Debarment Database", "mode": os.environ.get("BLACKLIST_MODE", "MOCK"), "description": "Debarment orders & blacklisting checks", "disclaimer": "MOCK DATA — NOT LIVE GOVERNMENT VERIFICATION"},
        {"name": "DigiLocker Verification", "mode": "MOCK", "description": "Document electronic URI authenticity", "disclaimer": "DIGILOCKER VERIFICATION CONTRACT"},
        {"name": "GeM Bid Management API", "mode": os.environ.get("GEM_MODE", "MOCK"), "description": "Official GeM tender data import and specifications", "disclaimer": "MOCK / OFFICIAL GEM BID FETCHER"}
    ]
    test_result = session.pop("adapter_test_result", None)
    return render_template("api_status.html", adapters=adapters, test_result=test_result)

@app.route("/api/test-adapter", methods=["POST"])
@login_required
def test_adapter():
    adapter_name = request.form.get("adapter_name")
    identifier = request.form.get("identifier", "").strip()
    res = {}
    if adapter_name == "GST":
        res = verify_gst(identifier)
    elif adapter_name == "PAN":
        res = verify_pan(identifier)
    elif adapter_name == "UDYAM":
        res = verify_udyam(identifier)
    elif adapter_name == "MCA":
        res = verify_mca(identifier)
    elif adapter_name == "BLACKLIST":
        res = verify_blacklisting(pan=identifier, gstin=identifier)
    elif adapter_name == "BIS":
        res = verify_bis(identifier)
    else:
        res = {"error": "Unknown adapter"}

    session["adapter_test_result"] = res
    return redirect(url_for("api_status"))

# -------------------------------------------------------------------------
# Officer Routes
# -------------------------------------------------------------------------
@app.route("/officer")
@login_required
@role_required("officer", "admin")
def officer_dashboard():
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    if user_role == "admin":
        tenders = query_db("SELECT * FROM tenders ORDER BY id DESC")
    else:
        tenders = query_db(
            """
            SELECT t.*
            FROM tenders t
            JOIN tender_assignments ta ON t.id = ta.tender_id
            WHERE ta.officer_id = ?
            ORDER BY t.id DESC
            """,
            (user_id,)
        )

    pending_clar = query_db("SELECT COUNT(*) as c FROM clarifications WHERE status = 'PENDING'", one=True)["c"]
    review_t = query_db("SELECT COUNT(*) as c FROM tenders WHERE lifecycle_stage = 'OFFICER_REVIEW'", one=True)["c"]
    decided_t = query_db("SELECT COUNT(*) as c FROM tenders WHERE lifecycle_stage = 'DECIDED'", one=True)["c"]

    return render_template(
        "officer_dashboard.html",
        tenders=tenders,
        pending_clarifications_count=pending_clar,
        review_tenders_count=review_t,
        decided_tenders_count=decided_t
    )

@app.route("/tenders/import", methods=["GET", "POST"])
@login_required
@role_required("officer", "admin")
def tender_import():
    if request.method == "POST":
        gem_bid_id = request.form.get("gem_bid_id", "").strip()
        try:
            tender_id = import_tender_from_gem(gem_bid_id, officer_id=session.get("user_id"))
            # Auto-assign to creating officer
            execute_db(
                "INSERT OR IGNORE INTO tender_assignments (tender_id, officer_id, assigned_by) VALUES (?, ?, ?)",
                (tender_id, session.get("user_id"), session.get("user_id"))
            )
            log_audit_event("TENDER_IMPORTED", "tenders", tender_id, f"Imported GeM bid {gem_bid_id}")
            flash(f"Tender {gem_bid_id} imported successfully.", "success")
            return redirect(url_for("tender_detail", tender_id=tender_id))
        except Exception as e:
            flash(f"Failed to import tender: {str(e)}", "error")
    return render_template("tender_import.html")

@app.route("/tenders/create-manual", methods=["POST"])
@login_required
@role_required("officer", "admin")
def tender_create_manual():
    gem_bid_id = request.form.get("gem_bid_id", "").strip()
    title = request.form.get("title", "").strip()
    organization = request.form.get("organization", "").strip()
    est_val = float(request.form.get("estimated_value") or 10000000)
    min_turnover = float(request.form.get("min_turnover") or 20000000)
    min_lc = float(request.form.get("min_local_content") or 50)
    pdf_file = request.files.get("pdf_file")

    try:
        tender_id = create_tender(
            gem_bid_id=gem_bid_id,
            title=title,
            organization=organization,
            category="Custom Procurement",
            estimated_value=est_val,
            min_turnover=min_turnover,
            min_local_content=min_lc,
            created_by=session.get("user_id"),
            pdf_file=pdf_file
        )
        execute_db(
            "INSERT OR IGNORE INTO tender_assignments (tender_id, officer_id, assigned_by) VALUES (?, ?, ?)",
            (tender_id, session.get("user_id"), session.get("user_id"))
        )
        log_audit_event("TENDER_CREATED", "tenders", tender_id, f"Created tender {gem_bid_id}")
        flash("Custom tender published successfully.", "success")
        return redirect(url_for("tender_detail", tender_id=tender_id))
    except Exception as e:
        flash(f"Error creating tender: {str(e)}", "error")
        return redirect(url_for("tender_import"))

@app.route("/tenders/<int:tender_id>")
@login_required
def tender_detail(tender_id):
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender:
        abort(404, description="Tender not found.")

    # Authorization check for officer: must be assigned or admin
    if user_role == "officer":
        is_assigned = query_db(
            "SELECT id FROM tender_assignments WHERE tender_id = ? AND officer_id = ?",
            (tender_id, user_id),
            one=True
        )
        if not is_assigned:
            abort(403, description="You are not authorized to evaluate this tender.")

    requirements = query_db(
        """
        SELECT * FROM requirements
        WHERE tender_id = ?
          AND (tender_version_id = (SELECT id FROM tender_versions WHERE tender_id = ? ORDER BY id DESC LIMIT 1)
               OR tender_version_id IS NULL)
        GROUP BY code
        ORDER BY id ASC
        """,
        (tender_id, tender_id)
    )
    versions = query_db("SELECT * FROM tender_versions WHERE tender_id = ? ORDER BY id ASC", (tender_id,))
    clarifications = get_clarifications_by_tender(tender_id)

    # Fetch bidders who submitted documents for this tender
    bidders_rows = query_db(
        """
        SELECT DISTINCT b.*
        FROM bidders b
        JOIN documents d ON b.id = d.bidder_id
        WHERE d.tender_id = ?
        """,
        (tender_id,)
    )

    bidders_with_verifications = []
    for b in bidders_rows:
        b_dict = dict(b)
        ver = get_latest_verification(tender_id, b["id"])
        b_dict["verification"] = ver
        b_dict["bidder_id"] = b["id"]
        bidders_with_verifications.append(b_dict)

    return render_template(
        "tender_detail.html",
        tender=tender,
        requirements=requirements,
        versions=versions,
        clarifications=clarifications,
        bidders_with_verifications=bidders_with_verifications
    )

@app.route("/tenders/<int:tender_id>/stage", methods=["POST"])
@login_required
@role_required("officer", "admin")
def tender_update_stage(tender_id):
    new_stage = request.form.get("stage")
    try:
        update_tender_lifecycle_stage(tender_id, new_stage, session.get("user_id"))
        log_audit_event("TENDER_STAGE_CHANGED", "tenders", tender_id, f"Transitioned stage to {new_stage}")
        flash(f"Tender lifecycle stage updated to {new_stage}.", "success")
    except Exception as e:
        flash(str(e), "error")
    return redirect(url_for("tender_detail", tender_id=tender_id))

@app.route("/tenders/<int:tender_id>/corrigendum", methods=["POST"])
@login_required
@role_required("officer", "admin")
def tender_corrigendum(tender_id):
    reason = request.form.get("reason", "").strip()
    turnover = request.form.get("min_turnover")
    local_content = request.form.get("min_local_content")
    description = request.form.get("description", "").strip()

    try:
        new_v = create_corrigendum(
            tender_id=tender_id,
            officer_id=session.get("user_id"),
            reason=reason,
            updated_description=description or None,
            updated_turnover=turnover or None,
            updated_local_content=local_content or None
        )
        log_audit_event("CORRIGENDUM_PUBLISHED", "tenders", tender_id, f"Published corrigendum {new_v}: {reason}")
        flash(f"Corrigendum published successfully as version {new_v}.", "success")
    except Exception as e:
        flash(f"Failed to publish corrigendum: {str(e)}", "error")

    return redirect(url_for("tender_detail", tender_id=tender_id))

# -------------------------------------------------------------------------
# Bidder Routes
# -------------------------------------------------------------------------
@app.route("/bidder")
@login_required
@role_required("bidder")
def bidder_dashboard():
    user_id = session.get("user_id")
    bidder = query_db("SELECT * FROM bidders WHERE user_id = ?", (user_id,), one=True)
    if not bidder:
        abort(404, description="Bidder profile not found.")

    open_tenders = query_db(
        "SELECT * FROM tenders WHERE lifecycle_stage = 'OPEN_FOR_BIDDING' ORDER BY id DESC"
    )

    # Submissions made by this bidder
    submissions_rows = query_db(
        """
        SELECT DISTINCT t.id as tender_id, t.gem_bid_id, t.title as tender_title,
               v.score, v.eligibility, v.risk_level, v.officer_decision
        FROM tenders t
        JOIN documents d ON t.id = d.tender_id
        LEFT JOIN verifications v ON (v.tender_id = t.id AND v.bidder_id = ? AND v.version_num = (
            SELECT MAX(version_num) FROM verifications WHERE tender_id = t.id AND bidder_id = ?
        ))
        WHERE d.bidder_id = ?
        GROUP BY t.id
        """,
        (bidder["id"], bidder["id"], bidder["id"])
    )

    clarifications = get_clarifications_by_bidder(bidder["id"])

    return render_template(
        "bidder_dashboard.html",
        bidder=bidder,
        open_tenders=open_tenders,
        submissions=submissions_rows,
        clarifications=clarifications
    )

@app.route("/bids/submit/<int:tender_id>", methods=["GET", "POST"])
@login_required
@role_required("bidder")
def bid_submit(tender_id):
    user_id = session.get("user_id")
    bidder = query_db("SELECT * FROM bidders WHERE user_id = ?", (user_id,), one=True)
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)

    if not bidder or not tender:
        abort(404, description="Tender or Bidder profile not found.")

    # Server-side bidding deadline enforcement
    is_open, msg = is_bidding_open(tender_id)
    if not is_open:
        flash(f"Submission rejected: {msg}", "error")
        return redirect(url_for("bidder_dashboard")), 400

    if request.method == "POST":
        # Check if pre-packaged sample bundle was requested
        use_sample = request.form.get("use_sample_bundle") == "1"
        files = []

        if use_sample:
            sample_dir = os.path.join(os.path.dirname(__file__), "sample_data", "bidders", "bidder_a")
            if os.path.exists(sample_dir):
                from services.seed_data import FileStorageMock
                for fname in os.listdir(sample_dir):
                    if fname.lower().endswith(".pdf"):
                        fpath = os.path.join(sample_dir, fname)
                        files.append(FileStorageMock(fpath, fname))
        else:
            files = request.files.getlist("documents")

        if not files:
            flash("No documents selected for submission.", "error")
            return render_template("upload.html", tender=tender)

        # Ingest multi-documents independently
        save_and_process_uploaded_documents(
            bidder_id=bidder["id"],
            tender_id=tender_id,
            files_list=files
        )

        # Run verification engine
        ver_res = run_bidder_verification(tender_id, bidder["id"])

        log_audit_event(
            "BID_SUBMITTED", "tenders", tender_id,
            f"Bidder #{bidder['id']} ({bidder['company_name']}) submitted {len(files)} docs. Ver score: {ver_res['score']}"
        )

        flash("Bid submitted successfully! Automated compliance verification completed.", "success")
        return redirect(url_for("view_report", tender_id=tender_id, bidder_id=bidder["id"]))

    return render_template("upload.html", tender=tender)

# -------------------------------------------------------------------------
# Verification & Audit Report Route
# -------------------------------------------------------------------------
@app.route("/reports/<int:tender_id>/<int:bidder_id>")
@login_required
def view_report(tender_id, bidder_id):
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    # Object-level authorization:
    # Bidder can ONLY view their own report!
    if user_role == "bidder":
        bidder = query_db("SELECT * FROM bidders WHERE user_id = ?", (user_id,), one=True)
        if not bidder or bidder["id"] != bidder_id:
            abort(403, description="Access forbidden: You cannot access another bidder's evaluation report.")
    else:
        bidder = query_db("SELECT * FROM bidders WHERE id = ?", (bidder_id,), one=True)

    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not bidder or not tender:
        abort(404, description="Report not found.")

    # Officer authorization check
    if user_role == "officer":
        is_assigned = query_db(
            "SELECT id FROM tender_assignments WHERE tender_id = ? AND officer_id = ?",
            (tender_id, user_id),
            one=True
        )
        if not is_assigned:
            abort(403, description="You are not authorized to evaluate this tender.")

    verification = get_latest_verification(tender_id, bidder_id)
    if not verification:
        # Run verification pass if not yet completed
        run_bidder_verification(tender_id, bidder_id)
        verification = get_latest_verification(tender_id, bidder_id)

    documents = get_documents_by_bidder_and_tender(bidder_id, tender_id)

    return render_template(
        "report.html",
        tender=tender,
        bidder=bidder,
        verification=verification,
        documents=documents
    )

@app.route("/verifications/<int:verification_id>/decision", methods=["POST"])
@login_required
@role_required("officer", "admin")
def record_decision(verification_id):
    decision = request.form.get("decision")
    remarks = request.form.get("remarks", "").strip()

    if decision not in ("QUALIFIED", "DISQUALIFIED"):
        flash("Invalid decision determination.", "error")
        return redirect(request.referrer or url_for("officer_dashboard"))

    if not remarks:
        flash("Officer remarks and justification are mandatory.", "error")
        return redirect(request.referrer or url_for("officer_dashboard"))

    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    execute_db(
        """
        UPDATE verifications SET
            officer_decision = ?,
            officer_remarks = ?,
            decided_by = ?,
            decided_at = ?
        WHERE id = ?
        """,
        (decision, remarks, session.get("user_id"), now_str, verification_id)
    )

    ver = query_db("SELECT tender_id, bidder_id FROM verifications WHERE id = ?", (verification_id,), one=True)
    log_audit_event("OFFICER_DECISION", "verifications", verification_id, f"Officer ruling: {decision}. Remarks: {remarks}")
    flash(f"Authoritative determination recorded: {decision}.", "success")
    return redirect(url_for("view_report", tender_id=ver["tender_id"], bidder_id=ver["bidder_id"]))

# -------------------------------------------------------------------------
# Clarification Routes
# -------------------------------------------------------------------------
@app.route("/clarifications/create", methods=["POST"])
@login_required
@role_required("officer", "admin")
def clarification_create():
    tender_id = request.form.get("tender_id")
    bidder_id = request.form.get("bidder_id")
    ver_id = request.form.get("verification_id")
    req_code = request.form.get("requirement_code")
    query_text = request.form.get("query_text", "").strip()

    if not (tender_id and bidder_id and query_text):
        flash("All clarification query fields are required.", "error")
        return redirect(request.referrer or url_for("officer_dashboard"))

    clar_id = create_clarification_request(
        tender_id=tender_id,
        bidder_id=bidder_id,
        verification_id=ver_id or 0,
        officer_id=session.get("user_id"),
        requirement_code=req_code,
        query_text=query_text
    )

    log_audit_event("CLARIFICATION_REQUESTED", "clarifications", clar_id, f"Clarification requested for {req_code}")
    flash("Clarification query issued to bidder successfully.", "success")
    return redirect(url_for("tender_detail", tender_id=tender_id))

@app.route("/clarifications/respond/<int:clar_id>", methods=["POST"])
@login_required
@role_required("bidder")
def clarification_respond(clar_id):
    user_id = session.get("user_id")
    bidder = query_db("SELECT id FROM bidders WHERE user_id = ?", (user_id,), one=True)
    if not bidder:
        abort(403)

    response_text = request.form.get("response_text", "").strip()
    uploaded_files = request.files.getlist("documents")

    try:
        new_ver = submit_clarification_response(
            clarification_id=clar_id,
            bidder_id=bidder["id"],
            response_text=response_text,
            uploaded_files=uploaded_files
        )
        log_audit_event("CLARIFICATION_RESPONDED", "clarifications", clar_id, f"Bidder responded. New ver #{new_ver['id']} created.")
        flash("Clarification response submitted. Re-verification pass completed successfully.", "success")
    except Exception as e:
        flash(f"Failed to submit response: {str(e)}", "error")

    return redirect(url_for("bidder_dashboard"))

# -------------------------------------------------------------------------
# Secure Authenticated File Access Routes
# -------------------------------------------------------------------------
@app.route("/documents/view/<int:doc_id>")
@login_required
def view_document_file(doc_id):
    """
    Secure file delivery. Verifies authorization before serving document bytes.
    No unauthenticated or cross-tenant access allowed.
    """
    user_id = session.get("user_id")
    user_role = session.get("user_role")

    doc = query_db("SELECT * FROM documents WHERE id = ?", (doc_id,), one=True)
    if not doc:
        abort(404, description="Document artifact not found.")

    # Object-level authorization check
    if user_role == "bidder":
        bidder = query_db("SELECT id FROM bidders WHERE user_id = ?", (user_id,), one=True)
        if not bidder or bidder["id"] != doc["bidder_id"]:
            abort(403, description="Access forbidden: You cannot view this document.")
    elif user_role == "officer":
        is_assigned = query_db(
            "SELECT id FROM tender_assignments WHERE tender_id = ? AND officer_id = ?",
            (doc["tender_id"], user_id),
            one=True
        )
        if not is_assigned:
            abort(403, description="Access forbidden: You are not assigned to this tender.")

    storage_path = doc["storage_path"]
    if not os.path.exists(storage_path):
        abort(404, description="Physical document file missing from storage.")

    return send_file(
        storage_path,
        mimetype=doc["mime_type"] or "application/pdf",
        as_attachment=False,
        download_name=doc["original_filename"]
    )

@app.route("/tenders/pdf/<int:tender_id>")
@login_required
def view_tender_pdf(tender_id):
    tender = query_db("SELECT * FROM tenders WHERE id = ?", (tender_id,), one=True)
    if not tender or not tender["pdf_storage_path"]:
        abort(404, description="Tender PDF document not available.")

    if not os.path.exists(tender["pdf_storage_path"]):
        abort(404, description="Physical tender PDF file missing.")

    return send_file(
        tender["pdf_storage_path"],
        mimetype="application/pdf",
        as_attachment=False,
        download_name=tender["pdf_filename"] or f"Tender_{tender['gem_bid_id'].replace('/', '_')}.pdf"
    )

# -------------------------------------------------------------------------
# Application Initialization
# -------------------------------------------------------------------------
with app.app_context():
    init_db()
    seed_database()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000, debug=False)
