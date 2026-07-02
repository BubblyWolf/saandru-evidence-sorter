"""Read a document (txt / docx / pdf / xlsx / csv / pptx / images) into plain text.

Local only. Golden rule: NEVER crash, NEVER silently skip. Every unreadable or
unsupported file returns a clear bracketed marker string so the pipeline can
route it to the human-review bucket with a plain-English reason.
"""
import csv
import io
import os
import shutil

MAX_XLSX_CHARS = 4000
OCR_TEXT_PER_PAGE_THRESHOLD = 40  # avg chars/page below this => treat PDF as scanned

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


# Standard places the Tesseract binary lands on Windows when it is NOT on PATH.
# Checked as a fallback so OCR still works on a college PC where the installer
# ran but PATH was never updated (a very common fieldwork situation).
_TESSERACT_FALLBACK_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""),
                 r"Programs\Tesseract-OCR\tesseract.exe"),
)


def _resolve_tesseract_binary():
    """Return a full path to the tesseract binary, or None. Checks PATH first,
    then the standard Windows install folders. Never raises."""
    try:
        on_path = shutil.which("tesseract")
        if on_path:
            return on_path
    except Exception:
        pass
    for candidate in _TESSERACT_FALLBACK_PATHS:
        try:
            if candidate and os.path.isfile(candidate):
                return candidate
        except Exception:
            continue
    return None


def _tesseract_available():
    """Return True only if BOTH pytesseract is importable AND the tesseract
    binary can be found (PATH or a known install folder). If the binary is
    found off-PATH, point pytesseract at it. Never raises."""
    try:
        import pytesseract
    except Exception:
        return False
    binary = _resolve_tesseract_binary()
    if not binary:
        return False
    try:
        # Only override if pytesseract's default ("tesseract") isn't on PATH.
        if shutil.which("tesseract") is None:
            pytesseract.pytesseract.tesseract_cmd = binary
    except Exception:
        pass
    return True


def _ocr_image(pil_image):
    """Run OCR on a PIL image. Caller must have already checked availability.
    Returns extracted text (may be empty string)."""
    import pytesseract
    return pytesseract.image_to_string(pil_image) or ""


def _read_txt(path):
    return open(path, encoding="utf-8", errors="ignore").read()


def _read_docx(path):
    from docx import Document
    return "\n".join(p.text for p in Document(path).paragraphs)


def _read_csv(path):
    rows_text = []
    with open(path, encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows_text.append(", ".join(cell for cell in row))
    return "\n".join(rows_text)


def _read_xlsx(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        parts = []
        total_len = 0
        for sheet in wb.worksheets:
            parts.append(f"[Sheet: {sheet.title}]")
            total_len += len(parts[-1])
            if total_len >= MAX_XLSX_CHARS:
                break
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c) for c in row if c is not None and str(c).strip() != ""]
                if not cells:
                    continue
                line = " | ".join(cells)
                parts.append(line)
                total_len += len(line)
                if total_len >= MAX_XLSX_CHARS:
                    break
            if total_len >= MAX_XLSX_CHARS:
                break
        text = "\n".join(parts)
        return text[:MAX_XLSX_CHARS]
    finally:
        wb.close()


def _read_pptx(path):
    try:
        import pptx  # noqa: F401
    except Exception:
        return "[UNSUPPORTED FORMAT: .pptx — python-pptx not installed]"
    from pptx import Presentation
    prs = Presentation(path)
    parts = []
    for i, slide in enumerate(prs.slides, start=1):
        slide_text = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text:
                slide_text.append(shape.text)
        if slide_text:
            parts.append(f"[Slide {i}]\n" + "\n".join(slide_text))
    return "\n".join(parts)


def _read_pdf(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        page_texts = [(pg.extract_text() or "") for pg in pdf.pages]
        num_pages = len(pdf.pages)
        text = "\n".join(page_texts)

        if num_pages == 0:
            return text

        avg_chars_per_page = len(text) / num_pages
        if avg_chars_per_page >= OCR_TEXT_PER_PAGE_THRESHOLD:
            return text

        # Looks scanned. Try OCR if truly available; else return a clear marker.
        if not _tesseract_available():
            return "[NEEDS OCR: scanned document — install Tesseract OCR to read this file]"

        try:
            ocr_parts = []
            for pg in pdf.pages:
                im = pg.to_image(resolution=200).original
                ocr_parts.append(_ocr_image(im))
            ocr_text = "\n".join(ocr_parts)
            # Prefer OCR text if it beats the original extraction; still may be empty.
            return ocr_text if len(ocr_text.strip()) > len(text.strip()) else text
        except Exception as e:
            return f"[NEEDS OCR: scanned document — OCR attempt failed ({e})]"


def _read_image(path):
    if not _tesseract_available():
        return "[NEEDS OCR: scanned document — install Tesseract OCR to read this file]"
    try:
        from PIL import Image
        with Image.open(path) as im:
            return _ocr_image(im)
    except Exception as e:
        return f"[NEEDS OCR: scanned document — OCR attempt failed ({e})]"


_KNOWN_MARKER_PREFIXES = (
    "[UNSUPPORTED FORMAT:",
    "[NEEDS OCR:",
    "[UNREADABLE:",
    "[EMPTY DOCUMENT:",
)


def _is_marker(result):
    """True only for strings WE generated as routing markers (known prefixes),
    never for arbitrary extracted text that happens to start with '['."""
    return isinstance(result, str) and result.startswith(_KNOWN_MARKER_PREFIXES)


def read_document(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            result = _read_txt(path)
        elif ext == ".docx":
            result = _read_docx(path)
        elif ext == ".doc":
            return "[UNSUPPORTED FORMAT: .doc — please re-save as .docx]"
        elif ext == ".csv":
            result = _read_csv(path)
        elif ext == ".xlsx":
            result = _read_xlsx(path)
        elif ext == ".pptx":
            result = _read_pptx(path)
        elif ext == ".pdf":
            result = _read_pdf(path)
        elif ext in IMAGE_EXTS:
            result = _read_image(path)
        else:
            return f"[UNSUPPORTED FORMAT: {ext}]"
    except Exception as e:
        return f"[UNREADABLE: {e}]"

    # Already one of our own routing markers (e.g. pptx-not-installed, needs-ocr) -> pass through.
    if _is_marker(result):
        return result

    if result is None or not str(result).strip():
        return "[EMPTY DOCUMENT: no readable text found]"

    return result
