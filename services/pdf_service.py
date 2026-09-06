import os
import pypdf

def extract_pdf_content(file_path):
    """
    Extracts text and page information from a PDF file using pypdf.
    Gracefully handles OCR fallback if text is sparse.
    """
    result = {
        "page_count": 0,
        "full_text": "",
        "pages": [],
        "ocr_status": "VALID",
        "ocr_confidence": 0.95,
        "ocr_quality": "HIGH",
        "extraction_method": "pypdf",
        "error": None
    }

    if not os.path.exists(file_path):
        result["ocr_status"] = "INVALID"
        result["error"] = "File not found"
        return result

    try:
        reader = pypdf.PdfReader(file_path)
        result["page_count"] = len(reader.pages)
        full_text_parts = []

        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            text = page.extract_text() or ""
            full_text_parts.append(text)
            result["pages"].append({
                "page_num": page_num,
                "text": text.strip()
            })

        combined_text = "\n\n".join(full_text_parts).strip()
        result["full_text"] = combined_text

        # If text is extremely short or empty, check if OCR is needed
        if len(combined_text) < 50:
            # Try OCR if pytesseract is installed and tesseract is in PATH
            ocr_text = _try_ocr_fallback(file_path)
            if ocr_text:
                result["full_text"] = ocr_text
                result["ocr_status"] = "VALID"
                result["ocr_confidence"] = 0.85
                result["ocr_quality"] = "MEDIUM"
                result["extraction_method"] = "tesseract_ocr"
            else:
                # Graceful fallback: Do not fail bidder, mark as NEEDS_REVIEW
                result["ocr_status"] = "NEEDS_REVIEW"
                result["ocr_confidence"] = 0.50
                result["ocr_quality"] = "LOW"
                result["extraction_method"] = "pypdf_sparse"

        return result
    except Exception as e:
        result["ocr_status"] = "NEEDS_REVIEW"
        result["ocr_confidence"] = 0.3
        result["ocr_quality"] = "LOW"
        result["error"] = str(e)
        return result

def _try_ocr_fallback(file_path):
    try:
        import pytesseract
        from PIL import Image
        # Check if tesseract is available
        pytesseract.get_tesseract_version()
        # If available, we could render pages, but for SIH prototypes without poppler, return None
        return None
    except Exception:
        return None
