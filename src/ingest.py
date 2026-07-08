"""Read a document (txt / docx / pdf / xlsx / csv / pptx / images) into plain text.

Local only. Golden rule: NEVER crash, NEVER silently skip. Every unreadable or
unsupported file returns a clear bracketed marker string so the pipeline can
route it to the human-review bucket with a plain-English reason.
"""
import csv
import io
import os
import shutil
import zipfile

MAX_XLSX_CHARS = 4000
OCR_TEXT_PER_PAGE_THRESHOLD = 40  # avg chars/page below this => treat PDF as scanned

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}

# Every extension read_document() can turn into real text (not counting the
# always-on "[UNSUPPORTED FORMAT: ...]" fallback for anything else). discover.py
# re-exports this so the whole app agrees on what "supported" means.
SUPPORTED_EXTS = frozenset({".txt", ".docx", ".doc", ".pdf", ".csv", ".xlsx", ".pptx"} | IMAGE_EXTS)


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


def _sniff_kind(path):
    """Read the first few bytes of `path` and guess its REAL kind from magic bytes,
    independent of whatever extension it happens to wear. Real college folders have
    files with wrong or missing extensions (a scan saved as "scan001" with no
    extension, a Word file someone renamed to ".pdf"), and we must not trust the
    extension blindly. Returns one of: "pdf", "docx", "xlsx", "pptx", "doc" (legacy
    OLE), "image", or None if nothing recognisable was sniffed (caller then falls
    back to the extension). Never raises -- any read/parse problem just means "None".
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except Exception:
        return None

    if not head:
        return None

    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"):
        return "image"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        # Legacy OLE compound file -- could be .doc, .xls, .ppt. We only implement
        # a .doc reader today, so this is treated as "doc" and _read_doc() itself
        # is defensive about content that turns out not to actually be a Word file.
        return "doc"
    if head.startswith(b"PK\x03\x04"):
        # Modern Office formats are zip archives with a telltale internal folder.
        try:
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
        except Exception:
            return None
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        return None

    return None


# Map a sniffed kind to the reader that already exists for that kind. Populated
# after the reader functions are defined (see bottom of the reader section).
_SNIFF_KIND_TO_EXT = {
    "pdf": ".pdf",
    "docx": ".docx",
    "xlsx": ".xlsx",
    "pptx": ".pptx",
    "doc": ".doc",
    "image": ".jpg",  # any image ext works -- _read_image() doesn't branch on it
}


def _read_txt(path):
    return open(path, encoding="utf-8", errors="ignore").read()


def _read_docx(path):
    """Extract paragraphs AND table cells AND header/footer text from a .docx.

    Real college documents (MoU lists, faculty/committee lists, attendance sheets)
    keep almost all their content in TABLES -- and letterheads/circular titles often
    sit in headers. The old reader took only Document(path).paragraphs, so a MoU
    list with a 110-cell table came back as ~60 characters and the classifier saw
    almost nothing. We now also walk every table (including tables nested inside
    cells) and each section's header/footer. Order isn't critical for
    classification (it is a bag-of-words match), so we simply append tables after
    paragraphs rather than reconstruct exact body order."""
    from docx import Document
    doc = Document(path)
    parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]

    def _walk_tables(tables):
        for table in tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
                # a cell can itself contain nested tables -- recurse so those aren't lost
                for cell in row.cells:
                    if cell.tables:
                        _walk_tables(cell.tables)

    _walk_tables(doc.tables)

    # headers/footers (letterhead, circular reference numbers, dates) -- best-effort,
    # never let a missing/odd section break the whole read.
    try:
        for section in doc.sections:
            for hf in (section.header, section.footer):
                for p in hf.paragraphs:
                    if p.text and p.text.strip():
                        parts.append(p.text)
    except Exception:
        pass

    return "\n".join(parts)


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


def _read_doc(path):
    """Legacy binary .doc reader. Word's binary format has no simple pure-Python
    parser worth depending on, so the pragmatic local-Windows-PC approach is to
    drive real Microsoft Word via COM automation (if installed) to pull the text,
    then close Word again. If pywin32 is not installed, Word is not installed, or
    COM automation fails for any reason (corrupt file, a real OLE file that isn't
    actually a Word doc, etc.) we return a clear marker instead of raising --
    college office PCs usually DO have Word, but must never be required to.
    """
    try:
        import win32com.client
    except Exception:
        return "[UNSUPPORTED FORMAT: .doc — could not auto-convert; please re-save as .docx]"

    word = None
    doc = None
    try:
        # DispatchEx starts a fresh, isolated Word process instead of reusing/
        # hijacking one the user may already have open with unsaved work.
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0  # 0 = wdAlertsNone -- never let a popup block us
        # ReadOnly + AddToRecentFiles=False keeps this invisible to the user and
        # never risks modifying the source file on disk.
        doc = word.Documents.Open(
            os.path.abspath(path), ReadOnly=True, AddToRecentFiles=False
        )
        text = doc.Content.Text
        # Word will "open" almost any byte soup and hand back mojibake instead of
        # raising -- the torture test showed such garbage sailing on to the
        # classifier and getting auto-committed with high confidence. Gate the
        # output: if the text doesn't look like readable words, mark it unreadable
        # so it lands in human review instead of a confident wrong folder. (A
        # genuine Tamil/Hindi .doc also fails this ASCII-leaning check -- that's
        # the safe direction: review bucket, never a wrong commit.)
        if _looks_garbled(text):
            return "[UNREADABLE: .doc opened but content looks garbled/corrupt — please re-save as .docx and retry]"
        return text
    except Exception:
        return "[UNSUPPORTED FORMAT: .doc — could not auto-convert; please re-save as .docx]"
    finally:
        # Must NEVER leave a hidden WINWORD.EXE process running on the college PC,
        # even if opening/reading the doc above raised partway through.
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:
            pass


def _looks_garbled(text, min_ratio=0.55):
    """Heuristic mojibake detector for legacy-.doc extraction ONLY (do not apply to
    .txt/.pdf paths, where non-English content is legitimate and handled elsewhere).
    Measures the fraction of characters that look like normal readable prose
    (letters, digits, whitespace, common punctuation). Word-decoded byte soup is
    dominated by symbols/CJK/control chars and lands far below any real document.
    Empty text is NOT garbled (the empty-document marker handles that case)."""
    stripped = text.strip() if text else ""
    if not stripped:
        return False
    sample = stripped[:4000]
    ok = sum(
        1 for ch in sample
        if ch.isascii() and (ch.isalnum() or ch.isspace() or ch in ".,;:!?()-'\"/&%@")
    )
    return (ok / len(sample)) < min_ratio


def _is_password_error(exc):
    """True if `exc` (or anything chained beneath it) is pdfminer's
    PDFPasswordIncorrect. pdfplumber.open() wraps the original pdfminer exception
    inside its own PdfminerException, but raises it from within the except block,
    so Python's implicit exception chaining (__context__) still carries the real
    cause -- walk that chain instead of relying on a fragile string match."""
    try:
        from pdfminer.pdfdocument import PDFPasswordIncorrect
    except Exception:
        return False
    seen = set()
    cur = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, PDFPasswordIncorrect):
            return True
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return False


def _read_pdf(path):
    import pdfplumber

    try:
        pdf_ctx = pdfplumber.open(path)
    except Exception as e:
        if _is_password_error(e):
            return "[UNREADABLE: password-protected PDF — remove the password and retry]"
        raise

    with pdf_ctx as pdf:
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


# Extensions we can dispatch on directly, mapped to the same "kind" vocabulary
# _sniff_kind() uses, so the two can be compared and reconciled. ".txt"/".csv"
# have no magic-byte signature, so sniffing never claims those kinds -- they are
# only ever reached via the extension.
_EXT_TO_KIND = {".docx": "docx", ".doc": "doc", ".xlsx": "xlsx", ".pptx": "pptx",
                ".pdf": "pdf", ".txt": "txt", ".csv": "csv"}
for _img_ext in IMAGE_EXTS:
    _EXT_TO_KIND[_img_ext] = "image"


def read_document(path):
    ext = os.path.splitext(path)[1].lower()
    ext_kind = _EXT_TO_KIND.get(ext)  # None for unknown/missing extensions

    # Real college folders have files whose extension lies: a scan saved with no
    # extension at all, or a Word file someone renamed to ".pdf". Sniff the real
    # magic bytes and use them whenever the extension is missing/unknown, or when
    # it flatly disagrees with what the bytes actually are ("trust the sniff").
    # ".txt"/".csv" have no magic-byte signature (sniff never returns those kinds),
    # so a plain text/csv file correctly sniffs as None and keeps its extension.
    sniffed = _sniff_kind(path)
    if sniffed is not None and sniffed != ext_kind:
        kind = sniffed
    else:
        kind = ext_kind

    try:
        if kind == "txt":
            result = _read_txt(path)
        elif kind == "docx":
            result = _read_docx(path)
        elif kind == "doc":
            result = _read_doc(path)
        elif kind == "csv":
            result = _read_csv(path)
        elif kind == "xlsx":
            result = _read_xlsx(path)
        elif kind == "pptx":
            result = _read_pptx(path)
        elif kind == "pdf":
            result = _read_pdf(path)
        elif kind == "image":
            result = _read_image(path)
        elif ext:
            return f"[UNSUPPORTED FORMAT: {ext}]"
        else:
            return "[UNSUPPORTED FORMAT: no extension — unrecognised file type]"
    except Exception as e:
        return f"[UNREADABLE: {e}]"

    # Already one of our own routing markers (e.g. pptx-not-installed, needs-ocr) -> pass through.
    if _is_marker(result):
        return result

    if result is None or not str(result).strip():
        return "[EMPTY DOCUMENT: no readable text found]"

    return result
