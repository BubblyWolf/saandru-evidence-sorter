"""Coverage test for ingest.read_document().

Generates small test files under samples/formats/, runs read_document on
each, and prints a coverage table. Every row must be either real extracted
text or one of our clear bracketed markers — zero exceptions allowed to
escape read_document (that is the whole point of the hardening).

Run: python src/test_ingest.py   (from the D:\\praman project root)
"""
import os
import sys
import shutil

# Windows consoles default to cp1252 -- Tamil filenames or unicode text in markers
# must never crash a run. Replace unprintable chars instead of raising.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from ingest import read_document, _tesseract_available  # noqa: E402
from discover import discover_files  # noqa: E402

FORMATS_DIR = os.path.join(os.path.dirname(__file__), "..", "samples", "formats")
FORMATS_DIR = os.path.abspath(FORMATS_DIR)


def _make_txt():
    p = os.path.join(FORMATS_DIR, "sample.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("This is a plain text sample document.\nLine two of content.\n")
    return p


def _make_empty_txt():
    p = os.path.join(FORMATS_DIR, "empty.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write("   \n\n   ")  # whitespace only
    return p


def _make_csv():
    p = os.path.join(FORMATS_DIR, "sample.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write("Criterion,Score,Remarks\n1.1,Good,On track\n1.2,Needs work,See notes\n")
    return p


def _make_xlsx():
    import openpyxl
    p = os.path.join(FORMATS_DIR, "sample.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Evidence"
    ws.append(["Criterion", "Score", "Remarks"])
    ws.append(["1.1", "Good", "On track"])
    ws.append(["1.2", "Needs work", "See notes"])
    wb.save(p)
    return p


def _make_docx():
    from docx import Document
    p = os.path.join(FORMATS_DIR, "sample.docx")
    doc = Document()
    doc.add_paragraph("This is a sample docx document.")
    doc.add_paragraph("Second paragraph with more content.")
    doc.save(p)
    return p


def _make_fake_doc():
    # Old binary .doc format, wearing a real OLE magic-byte header but NOT a real
    # Word document. _read_doc() tries COM automation (if Word/pywin32 are on this
    # PC) or falls back to a marker if not -- either way it must never raise, and
    # a fake OLE blob like this must fail gracefully (Word can't open it either).
    p = os.path.join(FORMATS_DIR, "sample.doc")
    with open(p, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1FAKE OLE HEADER BYTES NOT A REAL DOC")
    return p


def _make_noext_pdf():
    # A scan saved with NO extension at all that is really a PDF (common in messy
    # college folders). discover.py must still surface it, and ingest.py's magic-byte
    # sniff must dispatch it to the PDF reader despite the missing extension.
    p = os.path.join(FORMATS_DIR, "scan_no_extension")
    with open(p, "wb") as f:
        f.write(b"%PDF-1.4 THIS IS NOT REALLY A VALID PDF STRUCTURE AT ALL")
    return p


def _make_txt_renamed_pdf():
    # Plain text content wearing a ".pdf" extension (someone renamed a report by
    # hand). The extension lies; the sniff correctly finds no PDF magic bytes, so
    # this falls back to the (wrong) extension and pdfplumber fails on it --
    # acceptable outcomes are readable text OR an "[UNREADABLE:" marker, never a
    # crash and never a silent empty string.
    p = os.path.join(FORMATS_DIR, "mislabeled.pdf")
    with open(p, "w", encoding="utf-8") as f:
        f.write("This is actually plain text, just saved with a .pdf name.")
    return p


def _make_zero_byte():
    p = os.path.join(FORMATS_DIR, "zero_byte.txt")
    open(p, "wb").close()
    return p


def _make_tiny_png():
    # Minimal valid 1x1 PNG (raw bytes), enough to open with PIL and hit the
    # OCR-or-marker path without needing an image library to draw it.
    p = os.path.join(FORMATS_DIR, "tiny.png")
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000100"
        "0500010d0a2db40000000049454e44ae426082"
    )
    with open(p, "wb") as f:
        f.write(png_bytes)
    return p


def _make_corrupt_pdf():
    # Bytes that look nothing like a PDF -> should raise inside pdfplumber
    # and be caught, returning [UNREADABLE: ...].
    p = os.path.join(FORMATS_DIR, "corrupt.pdf")
    with open(p, "wb") as f:
        f.write(b"%PDF-1.4 THIS IS NOT REALLY A VALID PDF STRUCTURE AT ALL")
    return p


def _make_unknown_ext():
    p = os.path.join(FORMATS_DIR, "sample.xyz")
    with open(p, "w", encoding="utf-8") as f:
        f.write("some content in an unknown extension")
    return p


def main():
    os.makedirs(FORMATS_DIR, exist_ok=True)

    builders = [
        (".txt", _make_txt),
        (".txt (empty)", _make_empty_txt),
        (".csv", _make_csv),
        (".xlsx", _make_xlsx),
        (".docx", _make_docx),
        (".doc (old)", _make_fake_doc),
        (".png (tiny)", _make_tiny_png),
        (".pdf (corrupt)", _make_corrupt_pdf),
        (".xyz (unknown)", _make_unknown_ext),
        ("(no ext, real pdf)", _make_noext_pdf),
        (".pdf (really txt)", _make_txt_renamed_pdf),
        (".txt (0 bytes)", _make_zero_byte),
    ]

    rows = []
    exceptions = []
    problems = []  # hard assertion failures -- non-zero means the run fails

    for label, builder in builders:
        try:
            path = builder()
        except Exception as e:
            rows.append((label, f"[TEST SETUP FAILED: {e}]"))
            exceptions.append((label, "setup", e))
            continue

        try:
            result = read_document(path)
        except Exception as e:
            # This should NEVER happen -- read_document must not raise.
            result = f"[TEST FAILURE: read_document RAISED: {e}]"
            exceptions.append((label, "read_document", e))

        summary = str(result).replace("\n", " ")[:60]
        rows.append((label, summary))

        # ---- per-case hard assertions ----------------------------------------
        is_marker = isinstance(result, str) and result.startswith(
            ("[UNSUPPORTED FORMAT:", "[NEEDS OCR:", "[UNREADABLE:", "[EMPTY DOCUMENT:")
        )
        if label == "(no ext, real pdf)":
            # Sniff must win over the missing extension and actually try the PDF
            # reader -- the fake PDF bytes are truncated/invalid, so pdfplumber
            # itself will fail, but that failure MUST surface as our marker, not
            # as an unsupported-format fallback (that would mean the sniff was
            # never consulted at all).
            if not (isinstance(result, str) and result.startswith("[UNREADABLE:")):
                problems.append(f"no-extension real PDF: expected [UNREADABLE:..., got {summary!r}")
        elif label == ".pdf (really txt)":
            # Extension lies, sniff correctly finds nothing PDF-shaped, falls back
            # to the (wrong) extension. Never crash, never silently return "".
            ok = is_marker or (isinstance(result, str) and result.strip())
            if not ok:
                problems.append(f"mislabeled .pdf: expected marker or real text, got {summary!r}")
        elif label == ".txt (0 bytes)":
            if not (isinstance(result, str) and result.startswith(("[EMPTY DOCUMENT:", "[UNREADABLE:"))):
                problems.append(f"0-byte file: expected [EMPTY DOCUMENT: or [UNREADABLE:, got {summary!r}")

    print("\nCOVERAGE TABLE")
    print("-" * 90)
    print(f"{'extension':<18} | result summary (first 60 chars)")
    print("-" * 90)
    for label, summary in rows:
        print(f"{label:<18} | {summary}")
    print("-" * 90)

    print(f"\nTotal files tested: {len(rows)}")
    print(f"Exceptions escaped to caller: {len(exceptions)}")
    if exceptions:
        for label, where, e in exceptions:
            print(f"  - {label}: raised during {where}: {e}")
    else:
        print("Zero exceptions escaped read_document(). Golden rule holds.")

    tess = _tesseract_available()
    print("\nTesseract binary on PATH:", shutil.which("tesseract") is not None)
    try:
        import pytesseract  # noqa: F401
        pytesseract_importable = True
    except Exception:
        pytesseract_importable = False
    print("pytesseract importable:", pytesseract_importable)
    print("OCR available end-to-end (both required):", tess)

    # -----------------------------------------------------------------------
    # discover_files() coverage: junk filtering + nested subfolders.
    # -----------------------------------------------------------------------
    print("\nDISCOVER_FILES TEST")
    print("-" * 90)
    discover_root = os.path.join(FORMATS_DIR, "_discover_test")
    if os.path.isdir(discover_root):
        shutil.rmtree(discover_root)
    nested_dir = os.path.join(discover_root, "Criterion 3", "MoUs")
    os.makedirs(nested_dir, exist_ok=True)
    os.makedirs(os.path.join(discover_root, "Saandru_Sorted", "Criterion_1"), exist_ok=True)

    real_nested = os.path.join(nested_dir, "mou_2023.pdf")
    with open(real_nested, "w", encoding="utf-8") as f:
        f.write("fake pdf content for discover test")
    lock_file = os.path.join(discover_root, "~$temp.docx")
    with open(lock_file, "w", encoding="utf-8") as f:
        f.write("word lock file")
    thumbs = os.path.join(discover_root, "Thumbs.db")
    with open(thumbs, "wb") as f:
        f.write(b"junk")
    own_output = os.path.join(discover_root, "Saandru_Sorted", "Criterion_1", "old_copy.pdf")
    with open(own_output, "w", encoding="utf-8") as f:
        f.write("should never be re-discovered")
    # legacy output folder from a pre-rename (Praman) build must ALSO be excluded, so a
    # college that upgrades mid-project never re-ingests its old organized copies.
    os.makedirs(os.path.join(discover_root, "Praman_Sorted", "Criterion_1"), exist_ok=True)
    legacy_output = os.path.join(discover_root, "Praman_Sorted", "Criterion_1", "old_copy.pdf")
    with open(legacy_output, "w", encoding="utf-8") as f:
        f.write("legacy output -- should never be re-discovered either")

    found = discover_files(discover_root)
    expected_rel = os.path.join("Criterion 3", "MoUs", "mou_2023.pdf")
    if expected_rel not in found:
        problems.append(f"discover_files: nested file not found, got {found}")
    if any("~$temp.docx" in f for f in found):
        problems.append(f"discover_files: ~$temp.docx should have been excluded, got {found}")
    if any("Thumbs.db" in f for f in found):
        problems.append(f"discover_files: Thumbs.db should have been excluded, got {found}")
    if any("Saandru_Sorted" in f for f in found):
        problems.append(f"discover_files: Saandru_Sorted contents should have been excluded, got {found}")
    if any("Praman_Sorted" in f for f in found):
        problems.append(f"discover_files: legacy Praman_Sorted contents should have been excluded, got {found}")
    print(f"Found {len(found)} file(s), junk excluded correctly: {not problems}")
    for f in found:
        print(f"  - {f}")

    shutil.rmtree(discover_root, ignore_errors=True)

    print()
    if problems:
        print(f"SELF-TEST FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    else:
        print("SELF-TEST PASSED: all hard assertions held.")


if __name__ == "__main__":
    main()
