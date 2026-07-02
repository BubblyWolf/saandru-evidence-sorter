"""Parse the NAAC Affiliated/Constituent Colleges manual text into a criteria YAML pack.
Source: official manual PDF (reference/NAAC_Affiliated_College_Manual.pdf), extracted to text.
QnM/QlM types are HEURISTIC (table layout lost in extraction) -> verify_type flag set for hand-check.
"""
import re, sys

SRC = sys.argv[1]
OUT = sys.argv[2]

text = open(SRC, encoding="utf-8").read()

CRITERIA = {
    "1": "Curricular Aspects",
    "2": "Teaching-Learning and Evaluation",
    "3": "Research, Innovations and Extension",
    "4": "Infrastructure and Learning Resources",
    "5": "Student Support and Progression",
    "6": "Governance, Leadership and Management",
    "7": "Institutional Values and Best Practices",
}

# ---- parse Key Indicator names (prefer the (A) affiliated variant) ----
ki_names = {}
for m in re.finditer(r"^(\d\.\d)\s*\*?\(?([UA])?\)?\s*[-–]\s*(.+)$|^(\d\.\d)\s+([A-Z][A-Za-z ,&()'-]{3,60})$", text, re.M):
    if m.group(1):
        ki, variant, name = m.group(1), m.group(2), m.group(3).strip()
    else:
        ki, variant, name = m.group(4), None, m.group(5).strip()
    if len(name) < 4 or len(name) > 80:
        continue
    if ki not in ki_names or variant == "A":  # affiliated variant wins
        ki_names[ki] = name

# ---- isolate the metrics section (first detailed metric to end of criterion 7) ----
start = re.search(r"^1\.1\.1\.", text, re.M)
metrics_text = text[start.start():] if start else text

# strip page furniture
lines = []
for ln in metrics_text.splitlines():
    if re.match(r"^===== PAGE \d+ =====$", ln): continue
    if ln.startswith("Manual for Affiliated/Constituent"): continue
    if ln.startswith("NAAC for Quality and Excellence"): continue
    if re.match(r"^\d{1,3}$", ln.strip()): continue  # bare page numbers
    lines.append(ln)

# ---- collect metrics: id at line start, text runs until next id/section header ----
metric_re = re.compile(r"^(\d\.\d{1,2}\.\d{1,2})(\.\d)?[.:]?\s+(.*)$")
stop_re = re.compile(r"^(Key Indicator|KEY INDICATORS|Criterion [IVX\d])")
metrics = {}   # id -> {"text": ..., "subs": [...]}
current = None
for ln in lines:
    m = metric_re.match(ln)
    if m:
        mid, sub, rest = m.group(1), m.group(2), m.group(3).strip()
        # drop trailing weightage number glued to first line (e.g. "... well 10")
        rest = re.sub(r"\s+\d{1,2}$", "", rest)
        if sub:  # sub-metric like 2.1.1.1 -> data requirement of parent
            if mid in metrics:
                metrics[mid]["subs"].append(f"{mid}{sub}: {rest}")
            current = None
        else:
            metrics[mid] = {"text": rest, "subs": []}
            current = mid
        continue
    if stop_re.match(ln.strip()):
        current = None
        continue
    if current and ln.strip():
        # continuation line; stop if it looks like a new heading/table furniture
        if len(metrics[current]["text"]) < 420:
            metrics[current]["text"] += " " + ln.strip()

QN_HINT = re.compile(r"^(Average|Percentage|Number|Total|Ratio|Student[- ]|Expenditure)", re.I)

def esc(s):
    s = re.sub(r"\s+", " ", s).strip()
    return s.replace('"', "'")

# ---- boilerplate stripper ----
# In the source manual, each metric is: ONE substantive sentence/paragraph describing what
# is being asked, followed by SSR-portal form furniture (weightage number, "QM"/"QnM"/"QlM"
# type glyphs, "Write/Upload/Describe ... 500 words" instructions, "File Description /
# Upload / Link for additional information" prompts, "Options: A...E" MCQ scaffolding, and
# data-entry table column headers like "Name of the... Year of... Sl. No....").
# The PDF-text extraction collapses all of this onto one line per metric. Rather than try to
# regex out each scattered fragment (fragile -- leaves orphan words like a stray "l" bullet),
# we find the EARLIEST point where form furniture starts and cut everything from there. This
# is robust because the furniture phrases never appear inside the genuine metric sentence.
WB = r"(?<![A-Za-z])"   # left-side word-boundary substitute
NB = r"(?![A-Za-z])"    # right-side word-boundary substitute
CUT_TRIGGERS = [
    WB + "Write description",
    WB + "Upload (?:a )?description",
    WB + r"Q\s*[nl]?\s*M\s*[nl]?" + NB,   # QM / QnM / QlM / "Q M n" / "Q n M" / "Q l M"
    "",                          # PDF private-use bullet glyph
    WB + "File Description",
    WB + "Upload",
    WB + r"(?:Link|Paste link|URL)\s*for",
    WB + "Any additional information",
    WB + "Any other information",
    WB + "Data Requirement",
    WB + r"Options:\s*A\.",
    WB + "Geotagged [Pp]hotographs?",
    WB + r"within (?:a )?(?:maximum(?: of)?|minimum(?: of)?)? ?\d+ words",
    WB + r"\(within \d+ words\)",
    # data-entry table column-header runs, e.g. "Sl. No. Name of the... Year of..."
    WB + r"(?:Sl\.?\s*No\.?|Name of the|Programme Code|Program Code)",
]
CUT_RE = re.compile("|".join(CUT_TRIGGERS))


def clean_metric_text(raw):
    """Keep only the substantive sentence(s) before SSR-portal form furniture begins."""
    s = raw
    m = CUT_RE.search(s)
    if m and m.start() > 20:   # keep at least a minimal real sentence before cutting
        s = s[:m.start()]
    # drop a bare leftover weightage number at the very end, e.g. "...within 5"
    s = re.sub(r"\s+\d{1,2}\s*$", "", s)
    # drop stray PDF-extraction mangled-encoding placeholder char
    s = s.replace("�", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(" .,-–—")
    if s and not s.endswith((".", ")")):
        s += "."
    return s


# ---- clean texts + drop duplicate-text metrics (keep the lower id) ----
seen_text = {}   # normalized cleaned text -> id already kept
dupe_ids = set()
for mid in sorted(metrics, key=lambda k: [int(x) for x in k.split(".")]):
    metrics[mid]["text"] = clean_metric_text(metrics[mid]["text"])
    norm = re.sub(r"\s+", " ", metrics[mid]["text"]).strip().lower()
    if not norm:
        continue
    if norm in seen_text:
        dupe_ids.add(mid)  # later (higher) id is the duplicate; sorted ascending so seen_text holds the lower id
    else:
        seen_text[norm] = mid
for mid in dupe_ids:
    del metrics[mid]

# ---- emit YAML ----
out = []
out.append("# NAAC criteria pack - Affiliated/Constituent UG & PG Colleges (RAF, manual ver. 1.3.2021)")
out.append("# Source: official manual (reference/NAAC_Affiliated_College_Manual.pdf). Auto-parsed; texts trimmed to ~420 chars.")
out.append("# Form/table boilerplate (Upload/File Description/Options/table headers) stripped; duplicate-text metrics dropped (lower id kept).")
out.append("# type: heuristic QnM/QlM (verify_type: true = hand-check against the manual tables before release).")
out.append("# NOTE: NAAC Binary/MBGL (10 attributes) announced 2025 but portal not live as of 2026-06; this RAF pack is what colleges currently use.")
out.append("pack: naac_affiliated_raf2021")
out.append("framework: NAAC Revised Accreditation Framework (RAF)")
out.append("institution_type: Affiliated/Constituent UG & PG Colleges")
out.append("criteria:")
for cid in sorted(CRITERIA):
    out.append(f'  - id: "{cid}"')
    out.append(f'    name: "{CRITERIA[cid]}"')
    out.append(f"    key_indicators:")
    kis = sorted({mid.rsplit(".",1)[0] for mid in metrics if mid.startswith(cid + ".")}, key=lambda k: [int(x) for x in k.split(".")])
    for ki in kis:
        kname = esc(ki_names.get(ki, "[VERIFY KI name]"))
        out.append(f'      - id: "{ki}"')
        out.append(f'        name: "{kname}"')
        out.append(f"        metrics:")
        mids = sorted([m for m in metrics if m.rsplit(".",1)[0] == ki], key=lambda k: [int(x) for x in k.split(".")])
        for mid in mids:
            mt = metrics[mid]
            qn = bool(QN_HINT.match(mt["text"]))
            out.append(f'          - id: "{mid}"')
            out.append(f'            type: "{"QnM" if qn else "QlM"}"')
            out.append(f"            verify_type: true")
            out.append(f'            text: "{esc(mt["text"])[:420]}"')
            if mt["subs"]:
                out.append(f"            data_requirements:")
                for s in mt["subs"][:6]:
                    out.append(f'              - "{esc(s)[:200]}"')
            out.append(f"            evidence_hints: []   # filled in Phase 0.5 (typical proof documents)")
open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"criteria: {len(CRITERIA)} | key indicators: {len({m.rsplit('.',1)[0] for m in metrics})} | "
      f"metrics: {len(metrics)} | dropped duplicates: {len(dupe_ids)} ({', '.join(sorted(dupe_ids)) or '-'})")
print("wrote", OUT)
