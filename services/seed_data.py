"""Database and sample documents seeder for GeM Compliance Verification Platform.
Generates realistic PDF documents for Bidder A (Compliant), Bidder B (Warnings/Conflicts),
and Bidder C (Mandatory Failure).
"""
import os
import sys
import json

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from werkzeug.security import generate_password_hash
from database.db import get_db, init_db, utc_now_iso
from services.pdf_service import generate_sample_tender_pdf, generate_sample_bidder_pdf
from services.tender_service import TenderService
from services.document_service import DocumentService
from services.verification_engine import VerificationEngine

SAMPLE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sample_data", "bidders")

def seed_users():
    """Seed initial system users: admin, officer, and bidders."""
    conn = get_db()
    cursor = conn.cursor()
    now = utc_now_iso()

    users = [
        ("admin", "admin123", "GeM System Administrator", "admin", "Government e-Marketplace (GeM)", "admin@gem.gov.in"),
        ("bidder_a", "bidder123", "Bharat Tech Solutions Pvt Ltd", "bidder", "Bharat Tech Solutions Pvt Ltd", "compliance@bharattech.example"),
        ("bidder_b", "bidder123", "Precision Components India LLP", "bidder", "Precision Components India LLP", "tenders@precisionindia.example"),
        ("bidder_c", "bidder123", "Uttar Pradesh Agro Machines", "bidder", "Uttar Pradesh Agro Machines", "contact@upagro.example"),
    ]

    for username, pwd, name, role, org, email in users:
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if not cursor.fetchone():
            cursor.execute("""
            INSERT INTO users (username, password_hash, full_name, role, organization, email, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (username, generate_password_hash(pwd), name, role, org, email, now))

    # Seed bidder profile details
    bidders_data = [
        ("bidder_a", "Bharat Tech Solutions Private Limited", "BharatTech", "AABCU9603R", "07AABCU9603R1ZM", "UDYAM-DL-03-0012345", "U72900DL2018PTC331245", "A-12, Okhla Industrial Area Phase II, New Delhi 110020", "compliance@bharattech.example", "9876543210"),
        ("bidder_b", "Precision Components India LLP", "Precision Components", "AAACP1234A", "27AAACP1234A1Z5", "UDYAM-MH-01-0098765", "AAA-1234", "Floor 4, Nariman Point, Mumbai 400021", "tenders@precisionindia.example", "9876543211"), # Note: address mismatch with GST portal MIDC Pune
        ("bidder_c", "Uttar Pradesh Agro Machines", "UP Agro", "AAGCS5521K", "09AAGCS5521K1ZL", "UDYAM-UP-02-0054321", "U29210UP2020PTC128456", "Sector 62, Noida 201301", "contact@upagro.example", "9876543212")
    ]

    for uname, legal_name, trade, pan, gstin, udyam, cin, addr, email, phone in bidders_data:
        cursor.execute("SELECT id FROM users WHERE username = ?", (uname,))
        u = cursor.fetchone()
        if u:
            uid = u["id"]
            cursor.execute("SELECT id FROM bidders WHERE user_id = ?", (uid,))
            if not cursor.fetchone():
                cursor.execute("""
                INSERT INTO bidders (user_id, legal_name, trade_name, pan, gstin, udyam_reg, cin, registered_address, contact_email, contact_phone, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (uid, legal_name, trade, pan, gstin, udyam, cin, addr, email, phone, now))

    conn.commit()
    conn.close()

def generate_sample_documents():
    """Generates realistic PDFs for Bidder A, Bidder B, Bidder C."""
    os.makedirs(SAMPLE_DIR, exist_ok=True)

    # 1. BIDDER A DOCUMENTS (12+ documents demonstrating multi-doc workflow)
    dir_a = os.path.join(SAMPLE_DIR, "bidder_a")
    os.makedirs(dir_a, exist_ok=True)

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "GST_Certificate.pdf"),
        "FORM GST REG-06 • GOVERNMENT OF INDIA",
        [
            "REGISTRATION CERTIFICATE",
            "Registration Number (GSTIN): 07AABCU9603R1ZM",
            "Legal Name: Bharat Tech Solutions Private Limited",
            "Trade Name: BharatTech",
            "Constitution of Business: Private Limited Company",
            "Principal Place of Business: A-12, Okhla Industrial Area Phase II, New Delhi 110020",
            "Date of Liability: 2018-04-12 | Period of Validity: Regular / Active"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "GSTR1_Return_FY2025_Q4.pdf"),
        "GOODS AND SERVICES TAX RETURN • FORM GSTR-1",
        [
            "GSTR-1 Return Filing Confirmation for Quarter Ending March 2026",
            "GSTIN: 07AABCU9603R1ZM",
            "Filing Period: 2026-07 | Status: Filed Successfully",
            "Total Outward Supplies Declared: INR 1,85,00,000",
            "Acknowledgement Number: AA070326001234R"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "GSTR3B_Return_FY2025_Q4.pdf"),
        "MONTHLY SUMMARY RETURN • FORM GSTR-3B",
        [
            "GSTR-3B Self-Assessed Tax Return Confirmation",
            "GSTIN: 07AABCU9603R1ZM",
            "Filing Period: 2026-07 | Status: Filed On Time",
            "Net Tax Paid via Electronic Cash Ledger: INR 22,50,000",
            "Pending Tax Dues: Nil"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "PAN_Card.pdf"),
        "INCOME TAX DEPARTMENT • GOVERNMENT OF INDIA",
        [
            "PERMANENT ACCOUNT NUMBER CARD",
            "Permanent Account Number: AABCU9603R",
            "Name: BHARAT TECH SOLUTIONS PRIVATE LIMITED",
            "Incorporation Date: 12/04/2018",
            "Aadhaar / MCA Verification: Verified & Active"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Udyam_Registration_Certificate.pdf"),
        "UDYAM REGISTRATION CERTIFICATE • MINISTRY OF MSME",
        [
            "Udyam Registration Number: UDYAM-DL-03-0012345",
            "Name of Enterprise: M/S BHARAT TECH SOLUTIONS PRIVATE LIMITED",
            "Type of Enterprise: Medium Enterprise",
            "Major Activity: Services & Network Equipment Deployment",
            "Date of Commencement: 12/04/2018"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "ITR_FY2023_24.pdf"),
        "INCOME TAX RETURN ACKNOWLEDGEMENT • AY 2024-25",
        [
            "Income Tax Return for Financial Year: FY 2023-24",
            "PAN: AABCU9603R",
            "Form: ITR-6 (Companies)",
            "Annual Turnover / Gross Revenue: INR 5,10,00,000 (5.1 Crore)",
            "Net Tax Payable: INR 38,25,000 | e-Verification Status: Successfully Verified"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "ITR_FY2024_25.pdf"),
        "INCOME TAX RETURN ACKNOWLEDGEMENT • AY 2025-26",
        [
            "Income Tax Return for Financial Year: FY 2024-25",
            "PAN: AABCU9603R",
            "Annual Turnover / Gross Receipts: INR 6,00,00,000 (6.0 Crore)",
            "Gross Total Income: INR 85,00,000",
            "Status: Assessment Completed Without Demand"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "ITR_FY2025_26.pdf"),
        "INCOME TAX RETURN ACKNOWLEDGEMENT • AY 2026-27",
        [
            "Income Tax Return for Financial Year: FY 2025-26",
            "PAN: AABCU9603R",
            "Annual Turnover / Total Revenue: INR 6,40,00,000 (6.4 Crore)",
            "Audited under Section 44AB: Yes",
            "Average 3-Year Turnover Result: Exceeds INR 5 Crore"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Work_Order_1_BSNL.pdf"),
        "AWARD OF CONTRACT • BHARAT SANCHAR NIGAM LIMITED (BSNL)",
        [
            "Work Order Number: BSNL/NOIDA/NET/2023/102",
            "Client: BSNL Corporate Office, New Delhi",
            "Contract Value: INR 2,10,00,000 (2.1 Crore)",
            "Scope: Supply and commissioning of Managed Gigabit Switches and Routers",
            "Award Date: 15/09/2023 | Completion Period: 120 Days"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Work_Order_2_RailTel.pdf"),
        "PURCHASE ORDER • RAILTEL CORPORATION OF INDIA LIMITED",
        [
            "Purchase Order Ref: RCIL/NR/PO/2024/77",
            "Client: RailTel Corporation of India",
            "Contract Value: INR 2,40,00,000 (2.4 Crore)",
            "Scope: Optical Network Terminal and Core Switch Upgrades",
            "Award Date: 10/06/2024"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Work_Order_3_NIC.pdf"),
        "WORK ORDER • NATIONAL INFORMATICS CENTRE (NIC)",
        [
            "Order Ref: NIC/PROC/EQUIP/2025/441",
            "Client: National Informatics Centre Services Inc (NICSI)",
            "Contract Value: INR 1,80,00,000 (1.8 Crore)",
            "Scope: Enterprise LAN Core Routing deployment across 8 data centres",
            "Award Date: 05/02/2025"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Completion_Certificate_BSNL.pdf"),
        "WORK COMPLETION & PERFORMANCE CERTIFICATE",
        [
            "This is to certify that M/s Bharat Tech Solutions Pvt Ltd has successfully completed",
            "Work Order Ref: BSNL/NOIDA/NET/2023/102",
            "Total Contract Value: INR 2,10,00,000 (2.1 Crore)",
            "Completion Date: 20/01/2024",
            "Performance Rating: Excellent — Satisfactory operation through warranty."
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "OEM_Authorization_Cisco.pdf"),
        "MANUFACTURER AUTHORIZATION FORM (MAF)",
        [
            "To: The Procurement Officer, Department of Digital Infrastructure / GeM",
            "Tender Ref: GEM/2026/B/1234567",
            "Authorization Number: MAF-CISCO-IND-2026-8812",
            "Manufacturer: Cisco Systems India Private Limited",
            "Authorized Bidder: Bharat Tech Solutions Private Limited",
            "Product Line: Enterprise Routing & Switching hardware",
            "Valid Till / Expiry Date: 2027-12-31",
            "We hereby guarantee full technical support and warranty fulfillment."
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "Local_Content_Declaration.pdf"),
        "MAKE IN INDIA (MII) LOCAL CONTENT DECLARATION",
        [
            "Self-Declaration under Public Procurement (Preference to Make in India) Order 2017",
            "Tender Bid Number: GEM/2026/B/1234567",
            "Bidder Legal Name: Bharat Tech Solutions Private Limited",
            "Percentage of Local Content: 78.5%",
            "Classification: Class-I Local Supplier (>= 50%)",
            "Location of Local Value Addition: Okhla Industrial Area, New Delhi"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_a, "BIS_CRS_Registration.pdf"),
        "BUREAU OF INDIAN STANDARDS • CRS REGISTRATION",
        [
            "Compulsory Registration Scheme Certificate",
            "Registration Number: R-41001234",
            "Product: Network Switching Equipment (IS 13252 Part 1)",
            "Status: Valid and Active through 2028-03-31"
        ]
    )

    # 2. BIDDER B DOCUMENTS (Warnings, address conflict, expiring OEM, missing doc)
    dir_b = os.path.join(SAMPLE_DIR, "bidder_b")
    os.makedirs(dir_b, exist_ok=True)

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "GST_Certificate.pdf"),
        "FORM GST REG-06 • GOVERNMENT OF INDIA",
        [
            "REGISTRATION CERTIFICATE",
            "Registration Number (GSTIN): 27AAACP1234A1Z5",
            "Legal Name: Precision Components India LLP",
            "Trade Name: Precision Components",
            "Principal Place of Business: Plot 45, MIDC Industrial Estate, Pune 411019" # Differs from profile Mumbai address!
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "PAN_Card.pdf"),
        "INCOME TAX DEPARTMENT • GOVERNMENT OF INDIA",
        [
            "PERMANENT ACCOUNT NUMBER: AAACP1234A",
            "Name: PRECISION COMPONENTS INDIA LLP",
            "Status: Active | Tax dues pending: INR 12,500"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "ITR_FY2024_25.pdf"),
        "INCOME TAX RETURN ACKNOWLEDGEMENT • AY 2025-26",
        [
            "Income Tax Return for Financial Year: FY 2024-25",
            "PAN: AAACP1234A",
            "Annual Turnover: INR 5,20,00,000 (5.2 Crore)",
            "Note: Only 1 financial year submitted; FY23-24 and FY25-26 missing."
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "Work_Order_1.pdf"),
        "PURCHASE ORDER • MAHARASHTRA STATE ELECTRICITY",
        [
            "Work Order Ref: MSEDCL/PO/2024/911",
            "Client: Maharashtra State Electricity Distribution Co",
            "Contract Value: INR 3,20,00,000 (3.2 Crore)",
            "Completion Date: 2025-01-10"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "OEM_Authorization_Expiring.pdf"),
        "MANUFACTURER AUTHORIZATION LETTER",
        [
            "Authorization Number: MAF-JUNIPER-2025-09",
            "Manufacturer: Juniper Networks India",
            "Authorized Bidder: Precision Components India LLP",
            "Valid Till / Expiry Date: 2026-10-15", # Expiring in ~40 days -> EXPIRING_SOON warning!
            "Note: Subject to timely annual renewal."
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_b, "Local_Content_Declaration.pdf"),
        "MAKE IN INDIA SELF-DECLARATION",
        [
            "Percentage of Local Content: 52.0%",
            "Class of Supplier: Class-I Local Supplier (>= 50%)"
        ]
    )

    # 3. BIDDER C DOCUMENTS (Mandatory Failure: Cancelled GST, Blacklist, Low Local Content)
    dir_c = os.path.join(SAMPLE_DIR, "bidder_c")
    os.makedirs(dir_c, exist_ok=True)

    generate_sample_bidder_pdf(
        os.path.join(dir_c, "GST_Certificate_Cancelled.pdf"),
        "FORM GST REG-06 • GOVERNMENT OF INDIA",
        [
            "REGISTRATION CERTIFICATE",
            "Registration Number (GSTIN): 09AAGCS5521K1ZL",
            "Legal Name: Uttar Pradesh Agro Machines",
            "Status: CANCELLED on GST Portal w.e.f 2025-11-01"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_c, "PAN_Card.pdf"),
        "PERMANENT ACCOUNT NUMBER: AAGCS5521K",
        [
            "Name: UTTAR PRADESH AGRO MACHINES",
            "IT Compliance: Default on assessment demand INR 4,50,000"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_c, "ITR_Deficient.pdf"),
        "INCOME TAX RETURN ACKNOWLEDGEMENT",
        [
            "Income Tax Return for Financial Year: FY 2023-24",
            "Annual Turnover: INR 1,20,00,000 (1.2 Crore) — Fails 5.0 Cr threshold"
        ]
    )

    generate_sample_bidder_pdf(
        os.path.join(dir_c, "Local_Content_Low.pdf"),
        "LOCAL CONTENT DECLARATION",
        [
            "Declared Local Content: 35.0% — Fails 50% minimum threshold"
        ]
    )

def seed_sample_submissions():
    """
    Submits Bidder A, Bidder B, Bidder C to the mock GeM tender and runs verifications.
    """
    conn = get_db()
    cursor = conn.cursor()

    # Get tender
    cursor.execute("SELECT id FROM tenders LIMIT 1")
    t = cursor.fetchone()
    if not t:
        conn.close()
        return
    tender_id = t["id"]
    now = utc_now_iso()
    conn.commit()

    # Process Bidder A Submission
    cursor.execute("SELECT b.id FROM bidders b JOIN users u ON b.user_id = u.id WHERE u.username = 'bidder_a'")
    ba = cursor.fetchone()
    if ba:
        bidder_a_id = ba["id"]
        cursor.execute("SELECT id FROM bid_submissions WHERE tender_id = ? AND bidder_id = ?", (tender_id, bidder_a_id))
        sub_a = cursor.fetchone()
        if not sub_a:
            cursor.execute("""
            INSERT INTO bid_submissions (tender_id, bidder_id, tender_version_submitted, status, submission_timestamp)
            VALUES (?, ?, 1, 'SUBMITTED', ?)
            """, (tender_id, bidder_a_id, now))
            sub_a_id = cursor.lastrowid
            conn.commit()

            # Ingest Bidder A files
            dir_a = os.path.join(SAMPLE_DIR, "bidder_a")
            for fname in sorted(os.listdir(dir_a)):
                fpath = os.path.join(dir_a, fname)
                class MockFile:
                    def __init__(self, path, name):
                        self.path = path
                        self.filename = name
                    def save(self, dst):
                        import shutil
                        shutil.copy2(self.path, dst)
                DocumentService.process_uploaded_document(MockFile(fpath, fname), bidder_a_id, tender_id, sub_a_id)

            # Run verification
            VerificationEngine.run_verification(sub_a_id, tender_id, bidder_a_id)

    # Process Bidder B Submission
    cursor.execute("SELECT b.id FROM bidders b JOIN users u ON b.user_id = u.id WHERE u.username = 'bidder_b'")
    bb = cursor.fetchone()
    if bb:
        bidder_b_id = bb["id"]
        cursor.execute("SELECT id FROM bid_submissions WHERE tender_id = ? AND bidder_id = ?", (tender_id, bidder_b_id))
        sub_b = cursor.fetchone()
        if not sub_b:
            cursor.execute("""
            INSERT INTO bid_submissions (tender_id, bidder_id, tender_version_submitted, status, submission_timestamp)
            VALUES (?, ?, 1, 'SUBMITTED', ?)
            """, (tender_id, bidder_b_id, now))
            sub_b_id = cursor.lastrowid
            conn.commit()

            dir_b = os.path.join(SAMPLE_DIR, "bidder_b")
            for fname in sorted(os.listdir(dir_b)):
                fpath = os.path.join(dir_b, fname)
                class MockFile:
                    def __init__(self, path, name):
                        self.path = path
                        self.filename = name
                    def save(self, dst):
                        import shutil
                        shutil.copy2(self.path, dst)
                DocumentService.process_uploaded_document(MockFile(fpath, fname), bidder_b_id, tender_id, sub_b_id)

            VerificationEngine.run_verification(sub_b_id, tender_id, bidder_b_id)

    # Process Bidder C Submission
    cursor.execute("SELECT b.id FROM bidders b JOIN users u ON b.user_id = u.id WHERE u.username = 'bidder_c'")
    bc = cursor.fetchone()
    if bc:
        bidder_c_id = bc["id"]
        cursor.execute("SELECT id FROM bid_submissions WHERE tender_id = ? AND bidder_id = ?", (tender_id, bidder_c_id))
        sub_c = cursor.fetchone()
        if not sub_c:
            cursor.execute("""
            INSERT INTO bid_submissions (tender_id, bidder_id, tender_version_submitted, status, submission_timestamp)
            VALUES (?, ?, 1, 'SUBMITTED', ?)
            """, (tender_id, bidder_c_id, now))
            sub_c_id = cursor.lastrowid
            conn.commit()

            dir_c = os.path.join(SAMPLE_DIR, "bidder_c")
            for fname in sorted(os.listdir(dir_c)):
                fpath = os.path.join(dir_c, fname)
                class MockFile:
                    def __init__(self, path, name):
                        self.path = path
                        self.filename = name
                    def save(self, dst):
                        import shutil
                        shutil.copy2(self.path, dst)
                DocumentService.process_uploaded_document(MockFile(fpath, fname), bidder_c_id, tender_id, sub_c_id)

            VerificationEngine.run_verification(sub_c_id, tender_id, bidder_c_id)

    conn.close()

def run_seed():
    """Execute complete seeding process."""
    init_db()
    seed_users()
    tender_id = TenderService.ingest_mock_gem_tender()
    generate_sample_documents()
    seed_sample_submissions()
    print("Seed complete: Users, Mock GeM Tender, Sample PDFs, and 3 Bidder submissions created.")

if __name__ == "__main__":
    run_seed()
