"""
make_torture_corpus.py

Builds samples/messy_torture/ -- a "torture" corpus that simulates a real
college PC folder: messy names, nested folders, mixed formats, scans, junk.

Uses ONLY offline resources:
  - copies of real PDFs already in samples/jjcet_dvv/
  - files generated with libs installed locally (PIL, python-docx, openpyxl,
    reportlab if present, python-pptx if present)

Re-runnable: wipes and recreates samples/messy_torture/ every run.

Usage:
    python src/make_torture_corpus.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_PDFS = ROOT / "samples" / "jjcet_dvv"
OUT = ROOT / "samples" / "messy_torture"

# ---- optional libs -----------------------------------------------------
HAVE_REPORTLAB = False
try:
    import reportlab  # noqa: F401

    HAVE_REPORTLAB = True
except ImportError:
    pass

HAVE_PPTX = False
try:
    import pptx  # noqa: F401
    from pptx import Presentation
    from pptx.util import Inches

    HAVE_PPTX = True
except ImportError:
    pass

from PIL import Image, ImageDraw, ImageFont
import openpyxl

expected_entries: list[dict] = []
skipped_notes: list[str] = []


def record(path: Path, expect: str, note: str, true_criterion: str):
    rel = path.relative_to(OUT).as_posix()
    expected_entries.append(
        {
            "path": rel,
            "expect": expect,
            "note": note,
            "true_criterion": true_criterion,
        }
    )


def ensure_parent(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------- #
def make_nested_real_pdfs():
    """Copy real jjcet PDFs into nested paths with realistic bad names."""
    mapping = [
        ("3.3.1.pdf", "Criterion 3 evidence/mous and collabs/scan0001.pdf", "3"),
        ("5.1.1.pdf", "NAAC WORK/final/New Document (2).pdf", "5"),
        ("6.2.2.pdf", "NAAC WORK/final/final FINAL updated.pdf", "6"),
        ("7.1.2.pdf", "backup old laptop/xerox101.pdf", "7"),
    ]
    for src_name, dest_rel, crit in mapping:
        src = SRC_PDFS / src_name
        dest = OUT / dest_rel
        ensure_parent(dest)
        shutil.copy2(src, dest)
        record(dest, "readable", f"real PDF ({src_name}) with junk nested name", crit)


def draw_multiline(img: Image.Image, text: str, xy=(20, 20)):
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    draw.multiline_text(xy, text, fill="black", font=font, spacing=8)


def make_scanned_pdf():
    """Image-only PDF (no text layer) -- tests OCR-inside-PDF path."""
    text = (
        "SPORTS ACHIEVEMENT CERTIFICATE\n\n"
        "This is to certify that the college team participated in the\n"
        "Anna University Zonal Tournament 2023-24.\n\n"
        "Criterion 5.3 - Student Participation in Sports & Extracurricular\n"
        "Activities. Issued by the Department of Physical Education."
    )
    img = Image.new("RGB", (1240, 900), "white")  # ~150dpi-ish A4-ish canvas
    draw_multiline(img, text, xy=(40, 60))
    dest = OUT / "scans" / "certificate_scan.pdf"
    ensure_parent(dest)
    img.save(dest, "PDF")
    record(dest, "marker", "image-only scanned PDF, no text layer (OCR path test)", "5")


def make_photographed_certificate():
    """Photographed-looking JPEG certificate."""
    text = (
        "GREEN AUDIT REPORT 2023\n\n"
        "Tree plantation drive conducted on campus.\n"
        "150 saplings planted by NSS volunteers.\n\n"
        "Criterion 7 - Institutional Values and Best Practices."
    )
    img = Image.new("RGB", (1024, 768), "white")
    draw_multiline(img, text, xy=(30, 50))
    dest = OUT / "scans" / "IMG_20240311_142250.jpg"
    ensure_parent(dest)
    img.save(dest, "JPEG", quality=85)
    record(dest, "marker", "photographed certificate as JPEG", "7")


def make_excel():
    dest = OUT / "student data" / "scholarship list 23-24.xlsx"
    ensure_parent(dest)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Scholarships"
    ws.append(["Name", "Dept", "Scheme", "Amount"])
    fake_names = [
        "Arun Kumar", "Bhavani S", "Charan R", "Divya P", "Elango M",
        "Farida N", "Gowtham V", "Harini K", "Ilango T", "Jyothi R",
    ]
    depts = ["CSE", "ECE", "MECH", "EEE", "CIVIL"]
    schemes = ["First Graduate", "SC/ST Scholarship", "Merit Scholarship", "Sports Quota"]
    for i, name in enumerate(fake_names):
        ws.append([name, depts[i % len(depts)], schemes[i % len(schemes)], 5000 + i * 500])
    wb.save(dest)
    record(dest, "readable", "xlsx scholarship list (fake data)", "5")


def make_csv():
    dest = OUT / "student data" / "placement_2024.csv"
    ensure_parent(dest)
    fake_students = [
        "Nithya S", "Om Prakash", "Priya D", "Qadir M", "Ravi Teja",
        "Sangeetha L", "Tarun B", "Uma Devi", "Vignesh R", "Yamini K",
    ]
    companies = ["TCS", "Infosys", "Wipro", "Zoho", "Cognizant"]
    with open(dest, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Package", "Student"])
        for i, student in enumerate(fake_students):
            package = f"{3 + (i % 5)} LPA"
            w.writerow([companies[i % len(companies)], package, student])
    record(dest, "readable", "csv placement list (fake data)", "5")


def make_fake_doc():
    """OLE-header fake .doc -- tool should mark unreadable/unsupported, not crash."""
    dest = OUT / "office docs" / "committee minutes 2022.doc"
    ensure_parent(dest)
    ole_header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    junk = bytes((i % 256 for i in range(512)))
    with open(dest, "wb") as f:
        f.write(ole_header + junk)
    record(
        dest,
        "marker",
        "fake OLE .doc header + junk bytes; tool must not crash. "
        "A REAL .doc test needs an actual file from the scholar.",
        "?",
    )


def make_extension_lies():
    # (a) real PDF, no extension
    dest_a = OUT / "misc" / "report_final"
    ensure_parent(dest_a)
    shutil.copy2(SRC_PDFS / "1.4.1.pdf", dest_a)
    record(dest_a, "readable", "real PDF with NO extension at all", "1")

    # (b) plain text pretending to be .pdf
    dest_b = OUT / "misc" / "notes.pdf"
    ensure_parent(dest_b)
    dest_b.write_text(
        "NSS Camp Report\n\n"
        "The National Service Scheme camp was conducted from 10th to 16th "
        "December. Students participated in blood donation, cleanliness "
        "drives, and awareness programs. Criterion 3 - Extension Activities.\n",
        encoding="utf-8",
    )
    record(dest_b, "marker", "plain text file named .pdf (extension lie)", "3")

    # (c) CSV text pretending to be .xls
    dest_c = OUT / "misc" / "data.xls"
    ensure_parent(dest_c)
    with open(dest_c, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ID", "Value"])
        w.writerow([1, "fake"])
        w.writerow([2, "data"])
    record(dest_c, "marker", "CSV text content named .xls (extension lie)", "?")


def make_corrupt_empty():
    # truncated pdf
    src = SRC_PDFS / "2.4.1.pdf"
    dest_a = OUT / "misc" / "broken.pdf"
    ensure_parent(dest_a)
    with open(src, "rb") as f:
        head = f.read(2048)
    with open(dest_a, "wb") as f:
        f.write(head)
    record(dest_a, "marker", "truncated PDF (first 2KB only) -- corrupt file test", "?")

    # empty docx
    dest_b = OUT / "misc" / "empty.docx"
    ensure_parent(dest_b)
    dest_b.touch()
    record(dest_b, "marker", "0-byte .docx file -- empty file test", "?")


def make_junk_to_skip():
    entries = [
        (OUT / "~$minutes.docx", b"junk"),
        (OUT / "Thumbs.db", b"\x00\x01\x02junkdbdata"),
        (OUT / "misc" / "shortcut.lnk", b"L\x00\x00\x00junklnkdata"),
    ]
    for dest, content in entries:
        ensure_parent(dest)
        dest.write_bytes(content)
        record(dest, "excluded", "OS/office junk file that discovery must skip", "?")


def make_tamil_text():
    dest = OUT / "misc" / "tamil_notice.txt"
    ensure_parent(dest)
    text = (
        "கல்லூரி கலை விழா அறிவிப்பு\n"
        "எங்கள் கல்லூரியில் இந்த ஆண்டு கலை மற்றும் பண்பாட்டு விழா "
        "நடைபெறவுள்ளது.\n"
        "மாணவர்கள் அனைவரும் பங்கேற்க வேண்டுகிறோம்.\n"
        "தேதி: 15.08.2024, இடம்: கல்லூரி அரங்கம்.\n"
    )
    dest.write_text(text, encoding="utf-8")
    record(
        dest,
        "marker",
        "Tamil-language notice text; tests non-English behaviour "
        "(low-similarity abstention expected)",
        "?",
    )


def make_pptx():
    if not HAVE_PPTX:
        skipped_notes.append("python-pptx not importable -- iqac_presentation.pptx skipped")
        return
    dest = OUT / "office docs" / "iqac_presentation.pptx"
    ensure_parent(dest)
    prs = Presentation()
    slide_layout = prs.slide_layouts[0]
    slide = prs.slides.add_slide(slide_layout)
    slide.shapes.title.text = "IQAC Annual Report 2023-24"
    subtitle = slide.placeholders[1]
    subtitle.text = "Internal Quality Assurance Cell"

    slide2 = prs.slides.add_slide(prs.slide_layouts[1])
    slide2.shapes.title.text = "Criterion 7 - Best Practices"
    body = slide2.placeholders[1].text_frame
    body.text = "Green campus initiatives"
    p = body.add_paragraph()
    p.text = "Solar panel installation, rainwater harvesting"

    prs.save(dest)
    record(dest, "readable", "pptx IQAC presentation (python-pptx)", "7")


# --------------------------------------------------------------------- #
def build():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    if not SRC_PDFS.exists():
        print(f"ERROR: source PDFs not found at {SRC_PDFS}", file=sys.stderr)
        sys.exit(1)

    make_nested_real_pdfs()
    make_scanned_pdf()
    make_photographed_certificate()
    make_excel()
    make_csv()
    make_fake_doc()
    make_extension_lies()
    make_corrupt_empty()
    make_junk_to_skip()
    make_tamil_text()
    make_pptx()

    expected_path = OUT / "_expected.json"
    with open(expected_path, "w", encoding="utf-8") as f:
        json.dump(expected_entries, f, indent=2, ensure_ascii=False)

    return expected_path


def verify(expected_path: Path):
    assert OUT.exists(), "output tree missing"
    with open(expected_path, encoding="utf-8") as f:
        entries = json.load(f)
    missing = []
    for e in entries:
        p = OUT / e["path"]
        if not p.exists():
            missing.append(e["path"])
    if missing:
        print("MISSING FILES:", missing, file=sys.stderr)
        sys.exit(1)
    print(f"Verified {len(entries)} entries against _expected.json -- all present.")


def print_tree(base: Path, prefix: str = ""):
    entries = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        print(prefix + connector + entry.name)
        if entry.is_dir():
            ext = "    " if i == len(entries) - 1 else "│   "
            print_tree(entry, prefix + ext)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    expected_path = build()
    verify(expected_path)

    print()
    print(f"samples/messy_torture/ (root: {OUT})")
    print_tree(OUT)

    all_files = [p for p in OUT.rglob("*") if p.is_file()]
    print()
    print(f"Total files created: {len(all_files)}")
    print(f"reportlab available: {HAVE_REPORTLAB}")
    print(f"python-pptx available: {HAVE_PPTX}")
    if skipped_notes:
        print("Skipped:")
        for n in skipped_notes:
            print(f"  - {n}")


if __name__ == "__main__":
    main()
