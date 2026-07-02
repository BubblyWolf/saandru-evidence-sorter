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

sys.path.insert(0, os.path.dirname(__file__))
from ingest import read_document, _tesseract_available  # noqa: E402

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
    # Old binary .doc format -- we never actually parse it, just route by extension.
    p = os.path.join(FORMATS_DIR, "sample.doc")
    with open(p, "wb") as f:
        f.write(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1FAKE OLE HEADER BYTES NOT A REAL DOC")
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
    ]

    rows = []
    exceptions = []

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


if __name__ == "__main__":
    main()
