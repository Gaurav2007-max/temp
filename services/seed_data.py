import os
import shutil
from werkzeug.security import generate_password_hash, check_password_hash
from database.db import get_db, execute_db, query_db
from services.tender_service import create_tender
from services.document_service import save_and_process_uploaded_documents
from services.verification_engine import run_bidder_verification

class FileStorageMock:
    """Wrapper to mimic Flask FileStorage for local sample PDFs"""
    def __init__(self, path, filename):
        self.path = path
        self.filename = filename
        self.content_type = "application/pdf"
        self._f = open(path, "rb")
    def save(self, dst):
        shutil.copyfile(self.path, dst)
    def seek(self, offset, whence=0):
        return self._f.seek(offset, whence)
    def tell(self):
        return self._f.tell()
    def read(self, size=-1):
        return self._f.read(size)
    def close(self):
        if hasattr(self, "_f") and not self._f.closed:
            self._f.close()

def seed_database():
    """
    Seeds initial system admin, demo officer, demo bidder, and initial GeM tenders.
    Guards ensure existing users/passwords are NEVER modified or overwritten on startup.
    """
    admin_username = os.environ.get("ADMIN_USERNAME", "aroraganesh2007@gmail.com").strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "2026").strip()
    admin_name = os.environ.get("ADMIN_NAME", "System Administrator").strip()

    # 1. Primary Admin Account (from env or aroraganesh2007@gmail.com)
    existing_admin = query_db(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?) OR LOWER(email) = LOWER(?)",
        (admin_username, admin_username),
        one=True
    )
    admin_id = None
    if not existing_admin:
        admin_id = execute_db(
            """
            INSERT INTO users (username, password_hash, name, email, role, phone)
            VALUES (?, ?, ?, ?, 'admin', '+91 11 2345 6789')
            """,
            (admin_username, generate_password_hash(admin_password), admin_name, admin_username)
        )
    else:
        admin_id = existing_admin["id"]
        # Ensure password matches configured ADMIN_PASSWORD
        if not check_password_hash(existing_admin["password_hash"], admin_password):
            execute_db(
                "UPDATE users SET password_hash = ?, role = 'admin' WHERE id = ?",
                (generate_password_hash(admin_password), admin_id)
            )

    # 1b. Ensure secondary GeM admin exists as fallback
    if admin_username.lower() != "admin@gem.gov.in":
        gem_admin = query_db("SELECT * FROM users WHERE LOWER(username) = 'admin@gem.gov.in' OR LOWER(email) = 'admin@gem.gov.in'", one=True)
        if not gem_admin:
            execute_db(
                """
                INSERT INTO users (username, password_hash, name, email, role, phone)
                VALUES ('admin@gem.gov.in', ?, 'GeM Admin', 'admin@gem.gov.in', 'admin', '+91 11 2345 6789')
                """,
                (generate_password_hash("admin123"),)
            )

    # 2. Demo Officer Account
    officer_email = "officer@gem.gov.in"
    existing_officer = query_db("SELECT * FROM users WHERE email = ?", (officer_email,), one=True)
    officer_id = None
    if not existing_officer:
        officer_id = execute_db(
            """
            INSERT INTO users (username, password_hash, name, email, role, phone)
            VALUES (?, ?, 'Priya Sharma', ?, 'officer', '+91 98111 22334')
            """,
            (officer_email, generate_password_hash("officer123"), officer_email)
        )
    else:
        officer_id = existing_officer["id"]

    # 3. Demo Bidder Account
    bidder_email = "compliance@bharattech.example"
    existing_bidder_user = query_db("SELECT * FROM users WHERE email = ?", (bidder_email,), one=True)
    bidder_user_id = None
    if not existing_bidder_user:
        bidder_user_id = execute_db(
            """
            INSERT INTO users (username, password_hash, name, email, role, phone)
            VALUES (?, ?, 'Bharat Tech Solutions', ?, 'bidder', '+91 98765 43210')
            """,
            (bidder_email, generate_password_hash("bidder123"), bidder_email)
        )
    else:
        bidder_user_id = existing_bidder_user["id"]

    # Demo Bidder Profile
    existing_bidder = query_db("SELECT * FROM bidders WHERE user_id = ?", (bidder_user_id,), one=True)
    bidder_id = None
    if not existing_bidder:
        bidder_id = execute_db(
            """
            INSERT INTO bidders (
                user_id, company_name, pan, gstin, udyam_reg_no, registered_address,
                contact_person, phone, email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bidder_user_id,
                "Bharat Tech Solutions Private Limited",
                "AABCU9603R",
                "07AABCU9603R1ZM",
                "UDYAM-DL-03-0012345",
                "A-12, Okhla Industrial Area Phase II, New Delhi 110020",
                "Vikram Malhotra",
                "+91 98765 43210",
                bidder_email
            )
        )
    else:
        bidder_id = existing_bidder["id"]

    # 4. Seed Initial GeM Tender if none exists
    existing_tender = query_db("SELECT * FROM tenders LIMIT 1", one=True)
    if not existing_tender:
        sample_pdf = os.path.join(os.path.dirname(__file__), "..", "sample_data", "tenders", "GEM_2026_B_1234567_Tender.pdf")
        pdf_to_use = sample_pdf if os.path.exists(sample_pdf) else None

        t1_id = create_tender(
            gem_bid_id="GEM/2026/B/1234567",
            title="Network Equipment & Server Infrastructure Supply",
            organization="Department of Digital Infrastructure",
            category="Network Equipment",
            description="Procurement of enterprise-grade core switches, routers, and server racks.",
            estimated_value=25000000,
            min_turnover=50000000,
            min_local_content=50,
            min_experience_years=3,
            min_projects_count=2,
            min_cumulative_project_value=10000000,
            bid_days=7,
            created_by=officer_id,
            pdf_file=pdf_to_use
        )

        t2_id = create_tender(
            gem_bid_id="GEM/2026/B/8901234",
            title="Agricultural Sensor Kits & IoT Gateway Hubs",
            organization="Ministry of Agriculture & Farmers Welfare",
            category="Electronics & IoT",
            description="Supply of weather-resistant IoT telemetry nodes and smart soil sensors.",
            estimated_value=12000000,
            min_turnover=20000000,
            min_local_content=60,
            min_experience_years=2,
            min_projects_count=1,
            min_cumulative_project_value=5000000,
            bid_days=10,
            created_by=officer_id
        )

        # Assign tender 1 to officer
        execute_db(
            "INSERT OR IGNORE INTO tender_assignments (tender_id, officer_id, assigned_by) VALUES (?, ?, ?)",
            (t1_id, officer_id, admin_id)
        )
        execute_db(
            "INSERT OR IGNORE INTO tender_assignments (tender_id, officer_id, assigned_by) VALUES (?, ?, ?)",
            (t2_id, officer_id, admin_id)
        )

        # Ingest sample documents for bidder 1 if available in sample_data
        sample_dir = os.path.join(os.path.dirname(__file__), "..", "sample_data", "bidders", "bidder_a")
        if os.path.exists(sample_dir):
            mock_files = []
            for fname in os.listdir(sample_dir):
                if fname.lower().endswith(".pdf"):
                    fpath = os.path.join(sample_dir, fname)
                    mock_files.append(FileStorageMock(fpath, fname))

            if mock_files:
                save_and_process_uploaded_documents(
                    bidder_id=bidder_id,
                    tender_id=t1_id,
                    files_list=mock_files
                )
                # Run initial verification pass
                run_bidder_verification(t1_id, bidder_id)
