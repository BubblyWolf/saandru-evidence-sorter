"""Saandru -- gap report as PDF (and an HTML fallback for PCs without reportlab).

Both functions take the SAME `report_dict` shape that gap_report.build_gap_report()
returns (see gap_report.py's module docstring for the exact keys) plus the pack
name, so the numbers on the PDF/HTML always match the numbers already shown on
screen and in gap_report.txt -- no re-counting, no invented fields.

Golden rule for this file: a college office PC is not a developer machine. If
reportlab is missing, or a font is missing, or the data has an unexpected shape,
this module must NEVER crash the caller (run.py / app.py). Worst case it writes
a small "something went wrong" PDF/HTML instead of raising.
"""
import datetime as _dt
import html as _html
import os

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    _REPORTLAB_OK = True
except ImportError:
    _REPORTLAB_OK = False


# --------------------------------------------------------------------------
# Shared helpers (no reportlab / no html needed for these)
# --------------------------------------------------------------------------
def _now_str():
    return _dt.datetime.now().strftime("%d %b %Y, %I:%M %p")


def _missing_and_tentative(crit):
    """Same split app.py already does on screen -- kept in one place so PDF/HTML
    and the on-screen expander never disagree about what counts as missing."""
    missing_rows = [row for ki in crit["kis"] for row in ki["metrics"]
                     if row["evidence_count"] == 0 and row["tentative_count"] == 0]
    tentative_rows = [row for ki in crit["kis"] for row in ki["metrics"]
                       if row["evidence_count"] == 0 and row["tentative_count"] > 0]
    return missing_rows, tentative_rows


def _what_to_do_next(report, limit=10):
    """Same ranking gap_report.format_gap_report_text() uses: weakest criteria
    first, first `limit` fully-missing metrics."""
    ranked = sorted(report["criteria"], key=lambda c: c["coverage_pct"])
    todo = []
    for crit in ranked:
        for ki in crit["kis"]:
            for row in sorted(ki["metrics"], key=lambda r: r["id"]):
                if row["evidence_count"] == 0 and row["tentative_count"] == 0:
                    todo.append(row)
    return todo[:limit]


# --------------------------------------------------------------------------
# TASK 1a -- PDF (reportlab, guarded)
# --------------------------------------------------------------------------
_WIN_FONTS_DIR = r"C:\Windows\Fonts"
_FONT_CANDIDATES = ["Nirmala.ttf", "NirmalaUI.ttf", "arial.ttf"]


def _register_unicode_font():
    """Try to register a Windows font that can draw Tamil filenames (Nirmala /
    Nirmala UI cover Tamil; Arial is Latin-only but is at least a safe generic
    fallback). Returns the registered font name to use, or None if nothing could
    be registered (caller then falls back to built-in Helvetica + ASCII-safing)."""
    for fname in _FONT_CANDIDATES:
        path = os.path.join(_WIN_FONTS_DIR, fname)
        if os.path.exists(path):
            try:
                font_name = "SaandruUnicodeFont"
                pdfmetrics.registerFont(TTFont(font_name, path))
                return font_name
            except Exception:
                continue
    return None


def _safe_text(s, unicode_font_ok):
    """If we could not register a Unicode-capable font, Helvetica cannot draw
    Tamil (or any non-Latin-1) characters -- replace them with '?' rather than
    let reportlab raise or silently drop the whole string."""
    s = "" if s is None else str(s)
    if unicode_font_ok:
        return s
    return s.encode("latin-1", errors="replace").decode("latin-1")


def _write_error_pdf(out_path, message):
    """Last-resort fallback: if the real PDF build fails for any reason, still
    leave SOME valid PDF file at out_path so the caller's download button never
    points at a missing/corrupt file."""
    try:
        c = canvas.Canvas(out_path, pagesize=A4)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, 270 * mm, "Saandru -- Gap Report")
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, 255 * mm, "This report could not be fully built.")
        c.drawString(20 * mm, 248 * mm, "Please open the gap_report.txt or gap_report.html")
        c.drawString(20 * mm, 241 * mm, "file next to this one instead -- the numbers are the same.")
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(20 * mm, 20 * mm, _safe_text(f"Technical detail: {message}"[:180], False))
        c.save()
    except Exception:
        # truly nothing reportlab can do -- write raw minimal PDF bytes so at
        # least a file with %PDF magic bytes exists.
        with open(out_path, "wb") as f:
            f.write(b"%PDF-1.4\n%%EOF\n")


def write_gap_report_pdf(report_dict, pack_name, out_path, college_name=""):
    """Render the gap report as a nicely laid out PDF. NEVER raises -- on any
    failure it still writes SOME valid PDF at out_path (an error-notice PDF)."""
    if not _REPORTLAB_OK:
        # No reportlab on this machine -- caller (run.py/app.py) is expected to
        # check for the file's existence / offer the HTML fallback instead.
        return False

    try:
        unicode_font = _register_unicode_font()
        unicode_font_ok = unicode_font is not None
        body_font = unicode_font if unicode_font_ok else "Helvetica"
        bold_font = unicode_font if unicode_font_ok else "Helvetica-Bold"
        # TTFont() only registers one weight -- reuse it for "bold" spots too
        # rather than register a second (probably missing) bold file.

        def T(s):
            return _safe_text(s, unicode_font_ok)

        c = canvas.Canvas(out_path, pagesize=A4)
        page_w, page_h = A4
        margin = 18 * mm
        y = page_h - margin

        # ---- Title block ----
        c.setFillColor(colors.HexColor("#2e7d32"))
        c.setFont(bold_font, 26)
        c.drawString(margin, y, "Saandru")
        c.setFillColor(colors.black)
        c.setFont(body_font, 10)
        c.drawString(margin, y - 14, "Accreditation Evidence Sorter -- Gap Report")
        y -= 26

        c.setFont(body_font, 10)
        if college_name:
            c.drawString(margin, y, T(f"College: {college_name}"))
            y -= 13
        c.drawString(margin, y, T(f"Accreditation pack: {pack_name}"))
        y -= 13
        c.drawString(margin, y, T(f"Generated: {_now_str()}"))
        y -= 13
        # signature line -- this PDF travels to principals/IQAC desks; the author's name on
        # it is the tool's calling card during college visits. linkURL makes the name
        # clickable in PDF viewers (the rect must cover the drawn text area).
        sig_text = "Built by Chitranjan Jegadeesan"
        c.drawString(margin, y, T(sig_text))
        try:
            sig_w = c.stringWidth(T(sig_text), body_font, 10)
            c.linkURL("https://chitranjanjegadeesan.in/",
                      (margin, y - 2, margin + sig_w, y + 10), relative=0)
        except Exception:
            pass  # a link is a nicety; never let it break PDF generation
        y -= 10
        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.line(margin, y, page_w - margin, y)
        y -= 16

        # ---- Summary card table ----
        ov = report_dict["overall"]
        docs = ov["docs_scanned"]

        def pct(n):
            return f"{round(100 * n / docs)}%" if docs else "0%"

        c.setFont(bold_font, 12)
        c.drawString(margin, y, "Summary")
        y -= 14
        c.setFont(body_font, 10)
        summary_lines = [
            f"Total files looked at: {docs}",
            f"Sorted automatically: {ov['committed']} ({pct(ov['committed'])})",
            f"Needs a human to check: {ov['in_review']} ({pct(ov['in_review'])})",
            f"Could not be read: {ov['unreadable']} ({pct(ov['unreadable'])})",
        ]
        for line in summary_lines:
            c.drawString(margin, y, T(line))
            y -= 13
        y -= 6

        if report_dict["criteria"]:
            strongest = max(report_dict["criteria"], key=lambda cc: cc["coverage_pct"])
            weakest = min(report_dict["criteria"], key=lambda cc: cc["coverage_pct"])
            c.setFont(body_font, 10)
            c.drawString(margin, y, T(
                f"Strongest area: C{strongest['id']} {strongest['name']} "
                f"({strongest['coverage_pct']:.0f}%)."))
            y -= 13
            c.drawString(margin, y, T(
                f"Biggest gap: C{weakest['id']} {weakest['name']} "
                f"({weakest['coverage_pct']:.0f}%)."))
            y -= 18

        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.line(margin, y, page_w - margin, y)
        y -= 16

        def _new_page():
            nonlocal y
            c.showPage()
            y = page_h - margin

        def _need(space):
            if y - space < margin:
                _new_page()

        # ---- Per-criterion sections with a coverage bar ----
        bar_w = 90 * mm
        bar_h = 4 * mm
        for crit in report_dict["criteria"]:
            _need(30 * mm)
            c.setFont(bold_font, 12)
            c.setFillColor(colors.black)
            c.drawString(margin, y, T(f"Criterion {crit['id']}: {crit['name']}"))
            y -= 14

            # coverage bar: unfilled rectangle, then a filled rectangle on top
            # scaled by coverage_pct -- drawn with plain reportlab shapes.
            c.setFillColor(colors.HexColor("#e0e0e0"))
            c.rect(margin, y - bar_h, bar_w, bar_h, stroke=0, fill=1)
            filled_w = bar_w * max(0, min(100, crit["coverage_pct"])) / 100.0
            bar_color = "#2e7d32" if crit["coverage_pct"] >= 70 else (
                "#f9a825" if crit["coverage_pct"] >= 40 else "#c62828")
            c.setFillColor(colors.HexColor(bar_color))
            c.rect(margin, y - bar_h, filled_w, bar_h, stroke=0, fill=1)
            c.setFillColor(colors.black)
            c.setFont(body_font, 9)
            c.drawString(margin + bar_w + 4 * mm, y - bar_h,
                         T(f"{crit['metrics_strong']}/{crit['total_metrics']} "
                           f"({crit['coverage_pct']:.0f}%)"))
            y -= (bar_h + 10)

            missing_rows, tentative_rows = _missing_and_tentative(crit)

            if not missing_rows and not tentative_rows:
                c.setFont(body_font, 9)
                c.setFillColor(colors.HexColor("#2e7d32"))
                c.drawString(margin + 4 * mm, y, T("Every point here has at least one strong document. Good."))
                c.setFillColor(colors.black)
                y -= 14
            else:
                if missing_rows:
                    _need(14)
                    c.setFont(bold_font, 9)
                    c.setFillColor(colors.HexColor("#c62828"))
                    c.drawString(margin + 4 * mm, y, T("MISSING -- no evidence found:"))
                    c.setFillColor(colors.black)
                    y -= 12
                    c.setFont(body_font, 9)
                    for row in missing_rows:
                        _need(12)
                        c.drawString(margin + 8 * mm, y, T(f"- {row['id']}: {row['text'][:80]}"))
                        y -= 11
                    y -= 4
                if tentative_rows:
                    _need(14)
                    c.setFont(bold_font, 9)
                    c.setFillColor(colors.HexColor("#f9a825"))
                    c.drawString(margin + 4 * mm, y, T("TENTATIVE -- needs human confirmation:"))
                    c.setFillColor(colors.black)
                    y -= 12
                    c.setFont(body_font, 9)
                    for row in tentative_rows:
                        _need(12)
                        c.drawString(margin + 8 * mm, y, T(f"- {row['id']}: {row['text'][:80]}"))
                        y -= 11
                    y -= 4
            y -= 6

        # ---- What to do next ----
        _need(30 * mm)
        c.setFont(bold_font, 13)
        c.setFillColor(colors.HexColor("#2e7d32"))
        c.drawString(margin, y, "What to do next")
        c.setFillColor(colors.black)
        y -= 14
        c.setFont(body_font, 9)
        todo = _what_to_do_next(report_dict)
        if not todo:
            c.drawString(margin, y, T("Nothing missing -- every metric already has at least one document."))
            y -= 12
        else:
            c.drawString(margin, y, T("The most useful documents to go collect first:"))
            y -= 12
            for row in todo:
                _need(12)
                c.drawString(margin + 4 * mm, y, T(f"- {row['id']}: {row['text'][:85]}"))
                y -= 11

        c.save()
        return True

    except Exception as e:
        # Never let a rendering bug crash the caller -- leave a valid,
        # if apologetic, PDF at out_path instead.
        try:
            _write_error_pdf(out_path, str(e))
        except Exception:
            pass
        return False


# --------------------------------------------------------------------------
# TASK 1b -- HTML (no reportlab dependency, always works, print-ready)
# --------------------------------------------------------------------------
_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Saandru Gap Report -- {pack_name}</title>
<style>
  body {{ font-family: "Nirmala UI", "Segoe UI", Arial, sans-serif; color: #222;
         max-width: 900px; margin: 24px auto; padding: 0 16px; }}
  h1 {{ color: #2e7d32; margin-bottom: 0; }}
  .subtitle {{ color: #555; margin-top: 2px; }}
  .meta {{ color: #555; font-size: 0.95em; margin: 10px 0 18px; }}
  .summary {{ background: #f7f7f7; border-radius: 8px; padding: 14px 18px; margin-bottom: 18px; }}
  .summary table {{ border-collapse: collapse; width: 100%; }}
  .summary td {{ padding: 3px 6px; }}
  .verdict {{ margin-top: 8px; }}
  .criterion {{ border-top: 1px solid #ddd; padding-top: 14px; margin-top: 14px; }}
  .bar-track {{ background: #e0e0e0; border-radius: 4px; height: 12px; width: 100%; max-width: 400px; }}
  .bar-fill {{ background: #2e7d32; border-radius: 4px; height: 12px; }}
  .bar-row {{ display: flex; align-items: center; gap: 10px; margin: 4px 0 8px; }}
  .missing {{ color: #c62828; }}
  .tentative {{ color: #b98900; }}
  .ok {{ color: #2e7d32; }}
  ul {{ margin: 4px 0 10px 0; }}
  .todo {{ background: #fff8e1; border-radius: 8px; padding: 14px 18px; margin-top: 22px; }}
  @media print {{
    body {{ margin: 0; max-width: 100%; }}
    .criterion {{ page-break-inside: avoid; }}
    a[href]:after {{ content: ""; }}
  }}
</style>
</head>
<body>
<h1>Saandru</h1>
<div class="subtitle">Accreditation Evidence Sorter -- Gap Report</div>
<div class="meta">
  {college_line}
  Accreditation pack: <b>{pack_name}</b><br>
  Generated: {generated}<br>
  Built by <b><a href="https://chitranjanjegadeesan.in/">Chitranjan Jegadeesan</a></b>
</div>

<div class="summary">
  <table>
    <tr><td>Total files looked at</td><td><b>{docs}</b></td></tr>
    <tr><td>Sorted automatically</td><td><b>{committed}</b> ({committed_pct})</td></tr>
    <tr><td>Needs a human to check</td><td><b>{in_review}</b> ({in_review_pct})</td></tr>
    <tr><td>Could not be read</td><td><b>{unreadable}</b> ({unreadable_pct})</td></tr>
  </table>
  <div class="verdict">{verdict}</div>
</div>

{criteria_html}

<div class="todo">
  <h2>What to do next</h2>
  {todo_html}
</div>

</body>
</html>
"""


def _esc(s):
    return _html.escape("" if s is None else str(s))


def write_gap_report_html(report_dict, pack_name, out_path, college_name=""):
    """Self-contained, print-ready HTML version of the same report. No external
    dependency -- this always works, even on a PC without reportlab, and doubles
    as a nicer on-screen view than the .txt file."""
    ov = report_dict["overall"]
    docs = ov["docs_scanned"]

    def pct(n):
        return f"{round(100 * n / docs)}%" if docs else "0%"

    verdict = ""
    if report_dict["criteria"]:
        strongest = max(report_dict["criteria"], key=lambda cc: cc["coverage_pct"])
        weakest = min(report_dict["criteria"], key=lambda cc: cc["coverage_pct"])
        verdict = (
            f"Strongest area: <b>C{_esc(strongest['id'])} {_esc(strongest['name'])}</b> "
            f"({strongest['coverage_pct']:.0f}%). "
            f"Biggest gap: <b>C{_esc(weakest['id'])} {_esc(weakest['name'])}</b> "
            f"({weakest['coverage_pct']:.0f}%)."
        )

    crit_blocks = []
    for crit in report_dict["criteria"]:
        missing_rows, tentative_rows = _missing_and_tentative(crit)
        parts = [
            f'<div class="criterion">',
            f'<h3>Criterion {_esc(crit["id"])}: {_esc(crit["name"])}</h3>',
            f'<div class="bar-row"><div class="bar-track">'
            f'<div class="bar-fill" style="width:{max(0, min(100, crit["coverage_pct"]))}%"></div>'
            f'</div><span>{crit["metrics_strong"]}/{crit["total_metrics"]} '
            f'({crit["coverage_pct"]:.0f}%)</span></div>',
        ]
        if not missing_rows and not tentative_rows:
            parts.append('<p class="ok">Every point here has at least one strong document. Good.</p>')
        else:
            if missing_rows:
                parts.append('<p class="missing"><b>MISSING -- no evidence found:</b></p><ul>')
                for row in missing_rows:
                    parts.append(f'<li>{_esc(row["id"])}: {_esc(row["text"][:100])}</li>')
                parts.append('</ul>')
            if tentative_rows:
                parts.append('<p class="tentative"><b>TENTATIVE -- needs human confirmation:</b></p><ul>')
                for row in tentative_rows:
                    parts.append(f'<li>{_esc(row["id"])}: {_esc(row["text"][:100])} '
                                  f'({row["tentative_count"]} document(s) probably match this)</li>')
                parts.append('</ul>')
        parts.append('</div>')
        crit_blocks.append("\n".join(parts))

    todo = _what_to_do_next(report_dict)
    if not todo:
        todo_html = "<p>Nothing missing -- every metric already has at least one document.</p>"
    else:
        items = "".join(f'<li>{_esc(row["id"])}: {_esc(row["text"][:100])}</li>' for row in todo)
        todo_html = f"<p>The most useful documents to go collect first:</p><ol>{items}</ol>"

    html_out = _HTML_TEMPLATE.format(
        pack_name=_esc(pack_name),
        college_line=(f"College: <b>{_esc(college_name)}</b><br>" if college_name else ""),
        generated=_esc(_now_str()),
        docs=docs,
        committed=ov["committed"], committed_pct=pct(ov["committed"]),
        in_review=ov["in_review"], in_review_pct=pct(ov["in_review"]),
        unreadable=ov["unreadable"], unreadable_pct=pct(ov["unreadable"]),
        verdict=verdict,
        criteria_html="\n".join(crit_blocks),
        todo_html=todo_html,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return True
