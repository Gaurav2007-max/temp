import os
import pypdf

def extract_pdf_content(file_path):
    """
    Extracts text and page information from a PDF file using pypdf.
    If extracted text is insufficient or empty (scanned/image PDF),
    falls back to rendering PDF pages to images and running real Tesseract OCR.
    Preserves page numbers, source methods, and confidence scores.
    """
    result = {
        "page_count": 0,
        "full_text": "",
        "pages": [],
        "ocr_status": "VALID",
        "ocr_confidence": 0.98,
        "ocr_quality": "HIGH",
        "extraction_method": "TEXT",
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
        raw_pages = []

        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            text = page.extract_text() or ""
            full_text_parts.append(text)
            raw_pages.append({
                "page_num": page_num,
                "text": text.strip(),
                "source": "TEXT",
                "confidence": 0.98
            })

        combined_text = "\n\n".join(full_text_parts).strip()
        result["full_text"] = combined_text
        result["pages"] = raw_pages

        # Check if text is sparse or empty (e.g. scanned/image PDF)
        if len(combined_text) < 50:
            ocr_res = _run_real_ocr(file_path)
            if ocr_res and ocr_res.get("full_text") and len(ocr_res["full_text"].strip()) > 20:
                result["full_text"] = ocr_res["full_text"]
                result["pages"] = ocr_res["pages"]
                result["page_count"] = len(ocr_res["pages"]) or result["page_count"]
                conf = ocr_res.get("confidence", 0.88)
                result["ocr_confidence"] = conf
                if conf >= 0.70:
                    result["ocr_status"] = "SUCCESS"
                    result["ocr_quality"] = "HIGH"
                else:
                    result["ocr_status"] = "NEEDS_REVIEW"
                    result["ocr_quality"] = "MEDIUM"
                result["extraction_method"] = "OCR"
            else:
                # Scanned or empty, but OCR could not extract readable text
                result["ocr_status"] = "FAILED"
                result["ocr_confidence"] = 0.0
                result["ocr_quality"] = "LOW"
                result["extraction_method"] = "OCR_FAILED"
                result["error"] = "Scanned document text could not be extracted. OCR extraction failed."
        else:
            result["extraction_method"] = "TEXT"
            result["ocr_status"] = "VALID"

        return result
    except Exception as e:
        # Fallback to OCR if pypdf reader crashed on scanned document
        try:
            ocr_res = _run_real_ocr(file_path)
            if ocr_res and ocr_res.get("full_text") and len(ocr_res["full_text"].strip()) > 20:
                conf = ocr_res.get("confidence", 0.85)
                return {
                    "page_count": len(ocr_res["pages"]),
                    "full_text": ocr_res["full_text"],
                    "pages": ocr_res["pages"],
                    "ocr_status": "SUCCESS" if conf >= 0.70 else "NEEDS_REVIEW",
                    "ocr_confidence": conf,
                    "ocr_quality": "HIGH" if conf >= 0.70 else "MEDIUM",
                    "extraction_method": "OCR",
                    "error": None
                }
        except Exception:
            pass

        result["ocr_status"] = "FAILED"
        result["ocr_confidence"] = 0.0
        result["ocr_quality"] = "LOW"
        result["extraction_method"] = "FAILED"
        result["error"] = str(e)
        return result

def _run_real_ocr(file_path):
    """
    Renders PDF pages to images and runs Tesseract OCR.
    Extracts text per page, preserves page numbers, and calculates average confidence.
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return None

    rendered_images = []
    # 1. Try pypdfium2 (fast, high-resolution rendering without external poppler binary)
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(file_path)
        for i in range(len(pdf)):
            # Render at 200 DPI equivalent (scale=2) for high OCR accuracy
            img = pdf[i].render(scale=2).to_pil()
            rendered_images.append(img)
    except Exception:
        # 2. Fallback to pdf2image if available
        try:
            from pdf2image import convert_from_path
            rendered_images = convert_from_path(file_path, dpi=200)
        except Exception:
            rendered_images = []

    if not rendered_images:
        return None

    ocr_pages = []
    full_text_list = []
    conf_scores = []

    for idx, img in enumerate(rendered_images):
        page_num = idx + 1
        page_text = ""
        page_conf = 0.85

        try:
            # Perform OCR on page image
            page_text = pytesseract.image_to_string(img, config="--oem 3 --psm 3")
            # Calculate word-level confidence if possible
            try:
                data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
                confs = [float(c) for c in data.get("conf", []) if str(c).replace(".", "", 1).isdigit() and float(c) > 0]
                if confs:
                    page_conf = round(sum(confs) / (len(confs) * 100.0), 2)
            except Exception:
                page_conf = 0.85
        except Exception:
            page_text = ""

        clean_text = page_text.strip()
        full_text_list.append(clean_text)
        conf_scores.append(page_conf)
        ocr_pages.append({
            "page_num": page_num,
            "text": clean_text,
            "source": "OCR",
            "confidence": page_conf
        })

    avg_conf = round(sum(conf_scores) / len(conf_scores), 2) if conf_scores else 0.85
    combined_ocr_text = "\n\n".join([f"--- Page {p['page_num']} ---\n{p['text']}" for p in ocr_pages]).strip()

    return {
        "full_text": combined_ocr_text,
        "pages": ocr_pages,
        "confidence": avg_conf
    }
