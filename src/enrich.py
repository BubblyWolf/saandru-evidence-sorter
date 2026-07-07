"""
Saandru -- enrichment helpers: academic-YEAR detection and SMART document naming.

Both functions are designed to be safe to call on every document in a batch:
  - extract_academic_year() is pure regex (no LLM) -- fast, deterministic, offline.
  - suggest_name() makes ONE small local-LLM call (qwen2.5:3b-instruct via Ollama)
    and always has a non-LLM fallback so a slow/broken model never blocks a run.

No cloud calls. No new dependencies -- stdlib `re` + the existing ollama_client.
"""
import re
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from ollama_client import generate_json

# Windows-illegal filename characters (same set organize.py guards against).
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

# --------------------------------------------------------------------------
# YEAR detection
# --------------------------------------------------------------------------

# "2023-24", "2023-2024", "2023 - 24" (with optional "AY"/"A.Y." prefix)
_RANGE_RE = re.compile(
    r'(?:\bA\.?Y\.?\s*)?\b(20[0-9]{2})\s*[-–/]\s*(\d{2,4})\b',
    re.IGNORECASE,
)

# standalone 4-digit year 2019..2027
_STANDALONE_RE = re.compile(r'\b(20(?:19|2[0-7]))\b')

# words nearby that suggest a standalone year IS an academic-year reference
_ACADEMIC_CONTEXT_WORDS = re.compile(
    r'\b(academic year|batch|semester|admission|admitted|A\.?Y\.?|session)\b',
    re.IGNORECASE,
)


def _normalize_range(start_yyyy, end_part):
    """Given a 4-digit start year and the raw end token ('24' or '2024'),
    return normalized 'YYYY-YY' form, or None if the pair is not a sane
    consecutive academic-year range."""
    start = int(start_yyyy)
    if len(end_part) == 4:
        end = int(end_part)
        if end != start + 1:
            return None  # not a real academic-year range (e.g. "2020-2035")
    elif len(end_part) == 2:
        end_yy = int(end_part)
        expected_yy = (start + 1) % 100
        if end_yy != expected_yy:
            return None
    else:
        return None
    end_yy_str = f"{(start + 1) % 100:02d}"
    return f"{start}-{end_yy_str}"


def extract_academic_year(text):
    """Find academic-year mentions in `text` using deterministic regex only.

    Returns {"year": "2023-24" | "2024" | None, "confidence": "high"|"low",
             "all_years": [normalized strings found, in order of first appearance]}
    """
    if not text:
        return {"year": None, "confidence": "low", "all_years": []}

    found = []  # normalized strings, order preserved, duplicates kept for frequency count

    # 1) range patterns first (these are the strongest signal)
    range_spans = []
    for m in _RANGE_RE.finditer(text):
        norm = _normalize_range(m.group(1), m.group(2))
        if norm:
            found.append(norm)
            range_spans.append((m.start(), m.end()))

    # 2) standalone 4-digit years -- skip any that were already consumed by a range match
    for m in _STANDALONE_RE.finditer(text):
        s, e = m.start(), m.end()
        if any(rs <= s < re_ for rs, re_ in range_spans):
            continue  # part of a range already counted above
        yyyy = m.group(1)
        window = text[max(0, s - 40):e + 40]
        if _ACADEMIC_CONTEXT_WORDS.search(window):
            # clearly an academic-year context -> treat as start of an AY range
            found.append(f"{yyyy}-{(int(yyyy) + 1) % 100:02d}")
        else:
            # ambiguous standalone year -> keep as bare year string
            found.append(yyyy)

    if not found:
        return {"year": None, "confidence": "low", "all_years": []}

    distinct = sorted(set(found))
    if len(distinct) == 1:
        return {"year": distinct[0], "confidence": "high", "all_years": distinct}

    # multiple distinct years -> pick most frequent; tie -> most recent
    counts = {}
    for y in found:
        counts[y] = counts.get(y, 0) + 1

    def _sort_key(y):
        # sort key for "most recent": use the first 4 digits of the string
        try:
            recency = int(y[:4])
        except ValueError:
            recency = 0
        return (counts[y], recency)

    best = max(counts, key=_sort_key)
    return {"year": best, "confidence": "low", "all_years": distinct}


# --------------------------------------------------------------------------
# SMART naming
# --------------------------------------------------------------------------

_NAME_PROMPT = """You are naming a scanned college accreditation document for a filing system.
Read the excerpt below and return a SHORT, plain-English document title: 4 to 8 words,
no more than 60 characters, describing WHAT the document is (e.g. "Scholarship Beneficiary List",
"MoU with Industry Partner", "Faculty Development Program Report"). Do not include dates,
file extensions, or criterion codes in the title. Return ONLY JSON in this exact form:
{{"title": "..."}}

Document excerpt:
\"\"\"{excerpt}\"\"\"
"""


def _clean_filename_piece(s):
    """Strip Windows-illegal chars and collapse whitespace/newlines."""
    if not s:
        return ""
    s = str(s).replace("\n", " ").replace("\r", " ")
    s = _ILLEGAL_CHARS_RE.sub("", s)
    s = " ".join(s.split())
    return s.strip()


def _fallback_title(text):
    """First non-empty line of the text, cleaned and trimmed to 60 chars."""
    for line in (text or "").splitlines():
        cleaned = _clean_filename_piece(line)
        if cleaned:
            return cleaned[:60]
    return "Untitled Document"


def suggest_name(text, criterion_name="", metric_id=""):
    """Ask the local LLM for a short plain-English title for this document.

    One generate_json call to qwen2.5:3b-instruct. On any failure (network error,
    empty/garbled response), falls back to the first non-empty line of the text.
    Always returns a filename-safe string, 4-8 words / <=60 chars where possible.
    """
    excerpt = (text or "")[:600]
    if not excerpt.strip():
        return "Untitled Document"

    title = None
    try:
        prompt = _NAME_PROMPT.format(excerpt=excerpt)
        result = generate_json(prompt)
        if isinstance(result, dict) and not result.get("_parse_error"):
            raw_title = result.get("title", "")
            cleaned = _clean_filename_piece(raw_title)
            if cleaned:
                title = cleaned[:60]
    except Exception:
        title = None  # any network/timeout/etc error -> fall back below

    if not title:
        title = _fallback_title(text)

    return title


if __name__ == "__main__":
    samples = [
        "Academic Year 2023-24\nScholarship beneficiary list for SC/ST students.\n"
        "The college disbursed scholarships during AY 2023-24 to 145 students.",
        "MoU signed on 12.06.2019 between XYZ College and ABC Industries for "
        "internship placement of final year students.",
        "This report covers the period 2021-2022 and lists faculty development "
        "programs conducted, along with 2021-2022 attendance figures.",
        "No year mentioned anywhere in this short note about the library.",
    ]
    for s in samples:
        print(extract_academic_year(s))
    print(suggest_name(samples[0]))
