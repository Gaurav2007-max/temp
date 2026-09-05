"""PDF generation and text extraction service using pypdf and reportlab."""
import os
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

def extract_pdf_pages_and_text(pdf_path):
    """
    Extract text and page-by-page mapping from a PDF.
    Returns:
      total_pages (int),
      full_text (str),
      pages_dict (dict of {page_num (int): page_text (str)})
    """
    if not os.path.exists(pdf_path):
        return 0, "", {}
    
    try:
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        pages_dict = {}
        full_text_list = []
        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            text = page.extract_text() or ""
            pages_dict[page_num] = text
            full_text_list.append(f"--- PAGE {page_num} ---\n{text}")
        full_text = "\n\n".join(full_text_list)
        return total_pages, full_text, pages_dict
    except Exception as e:
        print(f"Error reading PDF {pdf_path}: {e}")
        return 0, "", {}

def generate_sample_tender_pdf(output_path, gem_bid_id="GEM/2026/B/1234567"):
    """
    Generates a realistic multi-page Mock GeM Tender Document.
    Clearly marked as: MOCK / SAMPLE DATA - NOT AN ACTUAL GOVERNMENT TENDER
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc = SimpleDocTemplate(output_path, pagesize=letter, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()

    # Custom styles
    header_style = ParagraphStyle(
        'TenderHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=18,
        alignment=1, # Center
        textColor=colors.HexColor("#0f2942")
    )
    disclaimer_style = ParagraphStyle(
        'Disclaimer',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9,
        leading=12,
        alignment=1,
        textColor=colors.HexColor("#b91c1c")
    )
    heading_style = ParagraphStyle(
        'SectionHeading',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor("#1e3a8a"),
        spaceBefore=10,
        spaceAfter=6
    )
    body_style = ParagraphStyle(
        'TenderBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1e293b")
    )

    story = []

    # Watermark / Disclaimer Banner
    story.append(Paragraph("MOCK / SAMPLE DATA — NOT AN ACTUAL GOVERNMENT TENDER", disclaimer_style))
    story.append(Paragraph("GeM BID DOCUMENT / REQUEST FOR PROPOSAL (RFP)", header_style))
    story.append(Paragraph("Government e-Marketplace (GeM) • Government of India", ParagraphStyle('Sub', parent=body_style, alignment=1)))
    story.append(Spacer(1, 15))

    # Tender Meta Table
    meta_data = [
        [Paragraph("<b>Bid Number:</b>", body_style), Paragraph(gem_bid_id, body_style), Paragraph("<b>Dated:</b>", body_style), Paragraph("2026-08-15", body_style)],
        [Paragraph("<b>Organization:</b>", body_style), Paragraph("Dept of Digital Infrastructure", body_style), Paragraph("<b>Ministry:</b>", body_style), Paragraph("Min. of Electronics & IT", body_style)],
        [Paragraph("<b>Title:</b>", body_style), Paragraph("Supply of Enterprise Network Equipment", body_style), Paragraph("<b>Estimated Value:</b>", body_style), Paragraph("INR 25,00,00,000 (25 Crore)", body_style)],
        [Paragraph("<b>Bidding Window:</b>", body_style), Paragraph("5 Calendar Days", body_style), Paragraph("<b>Clarification Window:</b>", body_style), Paragraph("5 Calendar Days", body_style)]
    ]
    t = Table(meta_data, colWidths=[100, 180, 100, 160])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#cbd5e1")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 15))

    # Page 1: Eligibility Clauses
    story.append(Paragraph("SECTION I: STATUTORY REGISTRATIONS & MANDATORY ELIGIBILITY", heading_style))
    story.append(Paragraph("<b>Clause 1.1: Goods & Services Tax (GST) Compliance</b><br/>"
                           "The bidder must have an active and valid GST registration. The bidder shall upload copy of GST Registration Certificate (Form GST REG-06) along with evidence of regular return filing for the preceding two quarters (GSTR-1 / GSTR-3B). Mismatched or cancelled GSTIN shall result in immediate rejection.", body_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Clause 1.2: Permanent Account Number (PAN)</b><br/>"
                           "The bidder must possess a valid Permanent Account Number (PAN) issued by the Income Tax Department of India, matching the legal entity name exactly.", body_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Clause 1.3: MSME Udyam / Startup Exemption</b><br/>"
                           "Bidders seeking turnover or experience exemptions as Micro or Small Enterprises must submit a valid Udyam Registration Certificate issued by MSME. Startups must provide DPIIT Certificate.", body_style))
    story.append(Spacer(1, 15))

    # Page Break to Page 2
    story.append(PageBreak())
    story.append(Paragraph("MOCK / SAMPLE DATA — NOT AN ACTUAL GOVERNMENT TENDER", disclaimer_style))
    story.append(Paragraph(f"Tender Ref: {gem_bid_id} • Page 2", ParagraphStyle('Page2Header', parent=body_style, alignment=2)))
    story.append(Spacer(1, 10))

    story.append(Paragraph("SECTION II: FINANCIAL CAPACITY & TECHNICAL EXPERIENCE", heading_style))
    story.append(Paragraph("<b>Clause 2.1: Annual Turnover (3 Financial Years)</b><br/>"
                           "The bidder must possess an Average Annual Financial Turnover of at least INR 5.00 Crore (Five Crores) during the last three financial years (FY2023-24, FY2024-25, FY2025-26). The bidder must upload audited balance sheets, profit & loss accounts, or Income Tax Returns (ITR Acknowledgements) covering all three consecutive financial years.", body_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Clause 2.2: Similar Work Experience</b><br/>"
                           "The bidder must have successfully executed at least 3 (three) similar projects of network infrastructure or IT equipment deployment in Central/State Government, PSUs, or reputed private firms within the last 5 years with an aggregate contract value of not less than INR 5.00 Crore. Upload work orders, contract copies, and respective completion certificates.", body_style))
    story.append(Spacer(1, 15))

    story.append(Paragraph("SECTION III: QUALITY, OEM & MAKE IN INDIA COMPLIANCE", heading_style))
    story.append(Paragraph("<b>Clause 3.1: Manufacturer Authorization (OEM Form)</b><br/>"
                           "If the bidder is not the original manufacturer of the offered network hardware, they must submit a valid Manufacturer's Authorization Form (MAF) from the OEM explicitly authorizing participation in this GeM bid, mentioning product line, authorization number, and valid through delivery completion date.", body_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Clause 3.2: Bureau of Indian Standards (BIS) Certification</b><br/>"
                           "All active network equipment must hold valid Bureau of Indian Standards (BIS / CRS) registration numbers conforming to safety standards (IS 13252).", body_style))
    story.append(Spacer(1, 6))

    story.append(Paragraph("<b>Clause 3.3: Make in India (MII) Preference</b><br/>"
                           "Minimum local content required for qualifying as Class-I Local Supplier is 50% (Fifty Percent). The bidder must submit a self-declaration stating the percentage of local content and location of local value addition.", body_style))
    story.append(Spacer(1, 15))

    story.append(Paragraph("SECTION IV: INTEGRITY PACT & NON-BLACKLISTING", heading_style))
    story.append(Paragraph("<b>Clause 4.1: Non-Debarment Undertaking</b><br/>"
                           "The bidder or its directors must not be debarred, blacklisted, or suspended by GeM, Ministry of Finance, or any Central/State Government department as on bid submission deadline.", body_style))

    doc.build(story)
    return output_path

def generate_sample_bidder_pdf(output_path, title, content_paragraphs):
    """Generates a realistic single or multi-page PDF document for bidder submissions."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc = SimpleDocTemplate(output_path, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
    styles = getSampleStyleSheet()

    header_style = ParagraphStyle(
        'DocHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=12,
        leading=16,
        alignment=1,
        textColor=colors.HexColor("#0f172a")
    )
    disclaimer_style = ParagraphStyle(
        'DocDisclaimer',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8,
        leading=10,
        alignment=1,
        textColor=colors.HexColor("#64748b")
    )
    body_style = ParagraphStyle(
        'DocBody',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#1e293b")
    )

    story = [
        Paragraph("MOCK SAMPLE DOCUMENT — FOR GEM PROCUREMENT VERIFICATION PROTOTYPE", disclaimer_style),
        Spacer(1, 8),
        Paragraph(title, header_style),
        Spacer(1, 12)
    ]

    for p in content_paragraphs:
        if p == "[PAGE_BREAK]":
            story.append(PageBreak())
            story.append(Paragraph(f"{title} (Contd.)", header_style))
            story.append(Spacer(1, 10))
        else:
            story.append(Paragraph(p, body_style))
            story.append(Spacer(1, 6))

    doc.build(story)
    return output_path
