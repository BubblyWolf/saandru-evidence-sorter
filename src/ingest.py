"""Read a document (txt / docx / pdf) into plain text. Local only."""
import os


def read_document(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            return open(path, encoding="utf-8", errors="ignore").read()
        if ext == ".docx":
            from docx import Document
            return "\n".join(p.text for p in Document(path).paragraphs)
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                return "\n".join((pg.extract_text() or "") for pg in pdf.pages)
    except Exception as e:
        return f"[UNREADABLE: {e}]"
    return f"[UNSUPPORTED FORMAT: {ext}]"
