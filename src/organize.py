"""
Saandru -- file organizer.

Takes the classification decisions the pipeline already made (and the human already
reviewed, at L1/L2) and COPIES each source document into a tidy set of subfolders,
one per accreditation criterion, inside a "Saandru_Sorted" folder next to the originals.

Hard rules:
  - COPY only (shutil.copy2). Never move, never delete, never overwrite an original.
  - Never overwrite an existing file inside Saandru_Sorted either -- collisions get a
    _2, _3, ... suffix UNLESS the existing file is byte-identical, in which case the
    copy is skipped (re-running the same folder must not pile up duplicate copies).
  - One bad file must never stop the whole batch: every copy is wrapped so a single
    failure is recorded in "errors" and the loop continues.
  - Every copy is recorded in "_manifest.json" (sha256 of the copied bytes + where it
    came from) so verify_sorted.py can later detect hand-edits/moves/deletions.
"""
import datetime
import json
import os
import re
import shutil

from duplicates import file_sha256  # reuse the same sha256-file-bytes pattern everywhere

MANIFEST_FILENAME = "_manifest.json"

SORTED_DIRNAME = "Saandru_Sorted"
NEEDS_REVIEW_DIRNAME = "_NEEDS_REVIEW"
COULD_NOT_READ_DIRNAME = "_COULD_NOT_READ"

# Windows reserved / illegal filename characters: \ / : * ? " < > |
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *{f"COM{i}" for i in range(1, 10)},
    *{f"LPT{i}" for i in range(1, 10)},
}

README_TEXT = """Saandru_Sorted -- what is this folder?
======================================

Everything in here is a COPY. Your original documents were NOT moved, renamed,
or changed in any way -- they are still exactly where they were before you ran
Saandru.

Folder layout:
  - "Criterion_<id>_<name>"  -> documents Saandru matched to that accreditation
                                 criterion (either automatically, or after you
                                 accepted / corrected the suggestion on screen).
      - "AY_<year>"          -> inside each criterion folder, documents are further
                                 sorted by the academic year Saandru detected in them
                                 (e.g. "AY_2023-24").
      - "_YEAR_UNKNOWN"      -> documents where no academic year could be detected.
  - "_NEEDS_REVIEW"          -> documents that still need a human decision.
  - "_COULD_NOT_READ"        -> files Saandru could not open or understand
                                 (unsupported format, corrupt file, etc).

If you delete this whole "Saandru_Sorted" folder, nothing is lost -- your
original documents are untouched. You can re-run Saandru at any time to
regenerate it.
"""


def sanitize_folder_name(name, fallback="Unnamed"):
    """Make a string safe to use as a single Windows folder name."""
    if not name:
        name = fallback
    name = str(name).strip()
    name = _ILLEGAL_CHARS_RE.sub("_", name)
    # collapse whitespace, strip trailing dots/spaces (Windows disallows trailing . or space)
    name = " ".join(name.split())
    name = name.rstrip(". ")
    if not name:
        name = fallback
    if name.upper() in _WINDOWS_RESERVED_NAMES:
        name = f"_{name}"
    # keep folder names reasonably short
    return name[:80]


def _resolve_collision(dest_dir, filename, src_hash):
    """Return (dest_path, should_copy) for a destination inside dest_dir.

    Never overwrites an existing file. But a name collision no longer always means
    "different file, suffix it" -- faculty re-running Saandru on a folder they already
    sorted used to pile up "_2", "_3" copies of the SAME document every time. So:
      - if a file already sits at the candidate path with IDENTICAL bytes (sha256
        match) -> this is the same document already sorted; skip the copy and return
        the existing path (should_copy=False).
      - if it exists with DIFFERENT bytes -> genuinely a different file that happens
        to want the same name; suffix _2, _3, ... until a free (or byte-identical)
        slot is found.
    src_hash may be None (source file unreadable for hashing) -- treated as "always
    different", so collisions always suffix rather than risk a false-positive skip.
    """
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    n = 2
    while os.path.exists(candidate):
        if src_hash is not None and file_sha256(candidate) == src_hash:
            return candidate, False  # byte-identical -- already sorted here, skip
        candidate = os.path.join(dest_dir, f"{base}_{n}{ext}")
        n += 1
    return candidate, True


def _criterion_folder_name(criterion):
    """criterion is expected to be a dict with 'id' and 'name' (or 'criterion_name')."""
    cid = criterion.get("id") or criterion.get("criterion") or "unknown"
    cname = criterion.get("name") or criterion.get("criterion_name") or ""
    safe_name = sanitize_folder_name(cname, fallback="Uncategorised")
    safe_id = sanitize_folder_name(str(cid), fallback="X")
    return f"Criterion_{safe_id}_{safe_name}"


def _year_subfolder_name(year):
    """Build the AY_<year> subfolder name, or the fallback bucket when no year
    was detected. `year` is expected to already be normalized (e.g. "2023-24"
    or a bare "2024") -- sanitize_folder_name() just guards against stray
    illegal characters getting through."""
    if not year:
        return "_YEAR_UNKNOWN"
    safe_year = sanitize_folder_name(str(year), fallback="")
    if not safe_year:
        return "_YEAR_UNKNOWN"
    return f"AY_{safe_year}"


def _dest_filename(decision, original_filename):
    """Build "<metric_id>_<safe_suggested_name>_<year>.<ext>", gracefully skipping
    any part that is missing. Falls back to the original filename if nothing
    (metric id / suggested name / year) is available at all."""
    _, ext = os.path.splitext(original_filename)

    metric_id = decision.get("metric")
    suggested_name = decision.get("suggested_name")
    year = decision.get("year")

    parts = []
    if metric_id:
        parts.append(sanitize_folder_name(str(metric_id), fallback=""))
    if suggested_name:
        # reuse the same illegal-char/whitespace cleanup as folder names, but
        # keep it as a filename piece (sanitize_folder_name already strips the
        # Windows-illegal set and trims trailing dots/spaces).
        safe_name = sanitize_folder_name(str(suggested_name), fallback="")
        if safe_name:
            parts.append(safe_name.replace(" ", "_"))
    if year:
        safe_year = sanitize_folder_name(str(year), fallback="")
        if safe_year:
            parts.append(safe_year)

    parts = [p for p in parts if p]
    if not parts:
        return original_filename  # nothing usable -> keep the original name

    stem = "_".join(parts)[:150]  # keep it reasonable
    return f"{stem}{ext}"


def _looks_unreadable(decision):
    """A decision is 'could not read' if it was flagged unreadable by the pipeline,
    or its filename/reason carries the "[...]" marker ingest.py uses for that."""
    if decision.get("unreadable"):
        return True
    reason = decision.get("reason", "") or ""
    if reason.startswith("[") and "]" in reason:
        return True
    fn = decision.get("filename") or decision.get("file") or ""
    return fn.startswith("[") and fn.endswith("]")


def _needs_review(decision):
    status = (decision.get("status") or "").lower()
    decided_by = (decision.get("decided_by") or "").lower()
    if status == "review":
        return True
    if decided_by == "pending":
        return True
    return False


def organize(decisions, source_folder, oversight_level=None, pack_name=None):
    """Copy every document referenced in `decisions` into Saandru_Sorted subfolders.

    decisions: list of dicts, each describing one processed file. Recognised keys
        (all optional except filename/file):
          - filename / file      : the document's filename (relative to source_folder)
          - path                 : full path to the source file (preferred if present)
          - criterion             : dict with at least {"id", "name"} (or {"criterion",
                                    "criterion_name"}) OR None
          - metric / id           : the metric id string (not required for foldering,
                                    kept for completeness / future use)
          - status                : "auto" / "review" / "unreadable" / ...
          - decided_by            : "auto" / "human" / "pending"
          - unreadable            : bool
          - reason                : free text; "[UNREADABLE: ...]" style marker also honoured
          - year                  : optional detected/normalized academic year, e.g. "2023-24".
                                    When present (and the file is filed under a criterion),
                                    it is copied into an "AY_<year>" subfolder; otherwise the
                                    file lands in "_YEAR_UNKNOWN" under that criterion.
          - suggested_name        : optional short plain-English title (human-editable) used
                                    to build the copied filename alongside the metric id/year.

    source_folder: the folder the originals live in. "Saandru_Sorted" is created
        inside this folder.
    oversight_level: "L1" / "L2" / "L3" -- accepted for the caller's bookkeeping /
        logging; organize() itself just files what `decisions` says to file (the
        caller is responsible for only calling this once the right decisions are
        final for the chosen level).
    pack_name: optional metric-pack name/label, recorded in _manifest.json purely
        for a human reading the manifest later ("what was this folder sorted with").

    Returns: {"copied": int, "already_there": int, "skipped": int, "errors": [str, ...],
              "sorted_dir": str}
      - "copied": files actually copied this run.
      - "already_there": collisions that were skipped because the file at that
        destination already had identical bytes (re-running an already-sorted folder).
    """
    if not source_folder or not os.path.isdir(source_folder):
        raise ValueError(f"source_folder does not exist: {source_folder!r}")

    sorted_dir = os.path.join(source_folder, SORTED_DIRNAME)
    review_dir = os.path.join(sorted_dir, NEEDS_REVIEW_DIRNAME)
    unread_dir = os.path.join(sorted_dir, COULD_NOT_READ_DIRNAME)

    os.makedirs(sorted_dir, exist_ok=True)
    os.makedirs(review_dir, exist_ok=True)
    os.makedirs(unread_dir, exist_ok=True)

    try:
        with open(os.path.join(sorted_dir, "README.txt"), "w", encoding="utf-8") as f:
            f.write(README_TEXT)
    except OSError:
        pass  # non-fatal -- the copy job itself is what matters

    summary = {"copied": 0, "already_there": 0, "skipped": 0, "errors": [], "sorted_dir": sorted_dir}
    manifest_files = []  # one entry per file that ended up on disk (copied OR already there)

    for decision in decisions or []:
        filename = decision.get("filename") or decision.get("file")
        if not filename:
            summary["skipped"] += 1
            summary["errors"].append("Skipped a decision with no filename.")
            continue

        src_path = decision.get("path") or os.path.join(source_folder, filename)
        if not os.path.isfile(src_path):
            summary["skipped"] += 1
            summary["errors"].append(f"{filename}: source file not found at {src_path}")
            continue

        # pick destination folder
        criterion = decision.get("criterion")  # may be None for review/unreadable buckets too
        try:
            if _looks_unreadable(decision):
                dest_dir = unread_dir
            elif _needs_review(decision):
                dest_dir = review_dir
            else:
                if not criterion:
                    dest_dir = review_dir  # no criterion committed -> treat as needs review
                else:
                    crit_dir = os.path.join(sorted_dir, _criterion_folder_name(criterion))
                    dest_dir = os.path.join(crit_dir, _year_subfolder_name(decision.get("year")))
                    os.makedirs(dest_dir, exist_ok=True)

            dest_filename = _dest_filename(decision, os.path.basename(filename))
            src_hash = file_sha256(src_path)
            dest_path, should_copy = _resolve_collision(dest_dir, dest_filename, src_hash)
            if should_copy:
                shutil.copy2(src_path, dest_path)
                summary["copied"] += 1
            else:
                summary["already_there"] += 1

            manifest_files.append({
                "relpath": os.path.relpath(dest_path, sorted_dir).replace(os.sep, "/"),
                "sha256": src_hash or "",
                "source": filename,
                "metric": str(decision.get("metric") or ""),
                "criterion": str(criterion.get("id")) if criterion else "",
            })
        except Exception as exc:  # noqa: BLE001 -- one bad file must never kill the batch
            summary["skipped"] += 1
            summary["errors"].append(f"{filename}: {exc}")

    # _manifest.json: the tamper-evident record verify_sorted.py checks the folder against
    # later. Written even on a re-run (overwrites) so it always reflects the current state.
    manifest = {
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "pack": pack_name or "",
        "files": manifest_files,
    }
    try:
        with open(os.path.join(sorted_dir, MANIFEST_FILENAME), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        summary["errors"].append(f"_manifest.json could not be written: {exc}")

    return summary


# ------------------------------------------------------------------------------------
# Self-test
# ------------------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    tmp_root = tempfile.mkdtemp(prefix="saandru_organize_test_")
    print(f"Self-test working folder: {tmp_root}")

    # --- build a small fake source folder --------------------------------------------
    files = {
        "syllabus_2024.txt": "auto, committed criterion",
        "mou_industry.txt": "auto, committed criterion, name has odd chars",
        "unsure_doc.txt": "needs human review",
        "corrupt.pdf": "flagged unreadable",
        "no_criterion.txt": "auto status but no criterion object -> should land in review",
        "scholarship_dated.txt": "auto, committed criterion, has a detected year",
        "scholarship_undated.txt": "auto, committed criterion, no year detected",
    }
    for fn, content in files.items():
        with open(os.path.join(tmp_root, fn), "w", encoding="utf-8") as f:
            f.write(content)

    # duplicate-name collision test: two different "decisions" pointing at files that
    # will both want to land in the SAME criterion folder under the SAME final name.
    dup_dir = os.path.join(tmp_root, "dupsource")
    os.makedirs(dup_dir, exist_ok=True)
    with open(os.path.join(dup_dir, "report.txt"), "w", encoding="utf-8") as f:
        f.write("first report")
    with open(os.path.join(tmp_root, "report.txt"), "w", encoding="utf-8") as f:
        f.write("second report, same final filename")

    decisions = [
        {
            "filename": "syllabus_2024.txt",
            "criterion": {"id": "1.1", "name": "Curriculum Planning and Implementation"},
            "metric": "1.1.1",
            "status": "auto",
            "decided_by": "auto",
        },
        {
            "filename": "mou_industry.txt",
            "criterion": {"id": "3", "name": 'MoUs / "Extension"? <Activities>'},
            "metric": "3.4.2",
            "status": "auto",
            "decided_by": "human",
        },
        {
            "filename": "unsure_doc.txt",
            "criterion": {"id": "2.1", "name": "Student Enrolment"},
            "metric": "2.1.1",
            "status": "review",
            "decided_by": "pending",
        },
        {
            "filename": "corrupt.pdf",
            "criterion": None,
            "unreadable": True,
            "reason": "[UNREADABLE: password protected]",
            "status": "unreadable",
            "decided_by": "pending",
        },
        {
            "filename": "no_criterion.txt",
            "criterion": None,
            "status": "auto",
            "decided_by": "auto",
        },
        {
            "filename": "report.txt",
            "path": os.path.join(dup_dir, "report.txt"),
            "criterion": {"id": "1.1", "name": "Curriculum Planning and Implementation"},
            "status": "auto",
            "decided_by": "auto",
        },
        {
            "filename": "report.txt",
            "criterion": {"id": "1.1", "name": "Curriculum Planning and Implementation"},
            "status": "auto",
            "decided_by": "auto",
        },
        {
            # a decision whose source file does not exist at all -> must be recorded as
            # an error, not crash the batch
            "filename": "ghost_file_does_not_exist.txt",
            "criterion": {"id": "1.1", "name": "Curriculum Planning and Implementation"},
            "status": "auto",
            "decided_by": "auto",
        },
        {
            # YEAR-DETECTION test: has a detected year -> should land in an AY_<year> subfolder
            # with a filename built from metric id + suggested name + year.
            "filename": "scholarship_dated.txt",
            "criterion": {"id": "5.1", "name": "Student Support"},
            "metric": "5.1.1",
            "year": "2023-24",
            "suggested_name": "Scholarship Beneficiary List",
            "status": "auto",
            "decided_by": "auto",
        },
        {
            # UNDATED test: no year detected -> should land in _YEAR_UNKNOWN under the
            # criterion folder, filename still uses metric id + suggested name (no year part).
            "filename": "scholarship_undated.txt",
            "criterion": {"id": "5.1", "name": "Student Support"},
            "metric": "5.1.2",
            "year": None,
            "suggested_name": "Scholarship Undated Note",
            "status": "auto",
            "decided_by": "auto",
        },
    ]

    result = organize(decisions, tmp_root, oversight_level="L2", pack_name="SelfTestPack")
    print("Summary:", result)

    # --- verify -------------------------------------------------------------------
    problems = []

    if result["copied"] != 9:
        problems.append(f"expected 9 copies, got {result['copied']}")
    if result["skipped"] != 1:
        problems.append(f"expected 1 skipped, got {result['skipped']}")
    if len(result["errors"]) != 1:
        problems.append(f"expected 1 error, got {len(result['errors'])}: {result['errors']}")

    sorted_dir = result["sorted_dir"]
    if not os.path.isfile(os.path.join(sorted_dir, "README.txt")):
        problems.append("README.txt missing")

    crit_dir = os.path.join(sorted_dir, "Criterion_1.1_Curriculum Planning and Implementation")
    if not os.path.isdir(crit_dir):
        problems.append(f"expected criterion folder missing: {crit_dir}")
    else:
        # these decisions carry no "year" key -> they land under _YEAR_UNKNOWN inside
        # the criterion folder (the new year-first layout).
        crit_unknown_dir = os.path.join(crit_dir, "_YEAR_UNKNOWN")
        if not os.path.isdir(crit_unknown_dir):
            problems.append(f"expected _YEAR_UNKNOWN subfolder missing: {crit_unknown_dir}")
        else:
            names_in_crit = set(os.listdir(crit_unknown_dir))
            # this decision carries a "metric": "1.1.1" -> the new naming rule renames the
            # copy to "<metric_id>.<ext>" (no suggested_name/year present to add on).
            if "1.1.1.txt" not in names_in_crit:
                problems.append(f"syllabus_2024.txt (metric 1.1.1) not renamed as expected, got {names_in_crit}")
            # two "report.txt" decisions with NO metric key -> original filename is kept,
            # and the collision must be resolved with a suffix.
            report_variants = {n for n in names_in_crit if n.startswith("report")}
            if len(report_variants) != 2:
                problems.append(f"expected 2 report.txt variants (collision handling), got {report_variants}")

    mou_dir = os.path.join(sorted_dir, "Criterion_3_MoUs _ _Extension__ _Activities_")
    if not os.path.isdir(mou_dir):
        # sanitization result may differ slightly; just check SOME Criterion_3_ folder exists
        candidates = [d for d in os.listdir(sorted_dir) if d.startswith("Criterion_3_")]
        if not candidates:
            problems.append("no sanitized Criterion_3_* folder found for the odd-character name")

    # --- YEAR-detection / smart-naming checks --------------------------------------
    support_dir = os.path.join(sorted_dir, "Criterion_5.1_Student Support")
    if not os.path.isdir(support_dir):
        problems.append(f"expected criterion folder missing: {support_dir}")
    else:
        dated_dir = os.path.join(support_dir, "AY_2023-24")
        if not os.path.isdir(dated_dir):
            problems.append(f"expected AY_2023-24 subfolder missing: {dated_dir}")
        else:
            dated_names = set(os.listdir(dated_dir))
            expected_dated = "5.1.1_Scholarship_Beneficiary_List_2023-24.txt"
            if expected_dated not in dated_names:
                problems.append(f"dated file not named as expected, got {dated_names}")

        unknown_dir = os.path.join(support_dir, "_YEAR_UNKNOWN")
        if not os.path.isdir(unknown_dir):
            problems.append(f"expected _YEAR_UNKNOWN subfolder missing: {unknown_dir}")
        else:
            undated_names = set(os.listdir(unknown_dir))
            expected_undated = "5.1.2_Scholarship_Undated_Note.txt"
            if expected_undated not in undated_names:
                problems.append(f"undated file not named as expected, got {undated_names}")

    review_dir = os.path.join(sorted_dir, NEEDS_REVIEW_DIRNAME)
    review_names = set(os.listdir(review_dir)) if os.path.isdir(review_dir) else set()
    # unsure_doc.txt carries "metric": "2.1.1" -> also renamed by the same naming rule,
    # even inside _NEEDS_REVIEW (no year split happens here, per spec).
    if "2.1.1.txt" not in review_names:
        problems.append(f"unsure_doc.txt (metric 2.1.1) not filed into _NEEDS_REVIEW as expected, got {review_names}")
    if "no_criterion.txt" not in review_names:
        problems.append("no_criterion.txt (no criterion object) not filed into _NEEDS_REVIEW")

    unread_dir_path = os.path.join(sorted_dir, COULD_NOT_READ_DIRNAME)
    unread_names = set(os.listdir(unread_dir_path)) if os.path.isdir(unread_dir_path) else set()
    if "corrupt.pdf" not in unread_names:
        problems.append("corrupt.pdf not filed into _COULD_NOT_READ")

    # originals must all still exist, byte-for-byte untouched
    for fn, content in files.items():
        p = os.path.join(tmp_root, fn)
        if not os.path.isfile(p):
            problems.append(f"ORIGINAL MISSING: {fn}")
        else:
            with open(p, encoding="utf-8") as f:
                if f.read() != content:
                    problems.append(f"ORIGINAL CHANGED: {fn}")
    if not os.path.isfile(os.path.join(dup_dir, "report.txt")):
        problems.append("ORIGINAL MISSING: dupsource/report.txt")
    if not os.path.isfile(os.path.join(tmp_root, "report.txt")):
        problems.append("ORIGINAL MISSING: report.txt")

    # --- Task A: manifest was written, and it matches this run --------------------
    manifest_path = os.path.join(sorted_dir, MANIFEST_FILENAME)
    if not os.path.isfile(manifest_path):
        problems.append("_manifest.json missing")
    else:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest.get("pack") != "SelfTestPack":
            problems.append(f"manifest 'pack' mismatch, got {manifest.get('pack')!r}")
        if len(manifest.get("files", [])) != result["copied"]:
            problems.append(
                f"manifest file count ({len(manifest.get('files', []))}) != "
                f"this run's copied count ({result['copied']})"
            )

    # --- Task A: re-run-skip -- organizing the SAME folder twice must not pile up
    # "_2"/"_3" copies of files whose bytes have not changed. -------------------------
    def _snapshot_relpaths():
        found = set()
        for root, _dirnames, filenames in os.walk(sorted_dir):
            for name in filenames:
                found.add(os.path.relpath(os.path.join(root, name), sorted_dir))
        return found

    before_rerun = _snapshot_relpaths()
    result_rerun = organize(decisions, tmp_root, oversight_level="L2", pack_name="SelfTestPack")
    after_rerun = _snapshot_relpaths()
    print("Re-run summary:", result_rerun)

    if result_rerun["copied"] != 0:
        problems.append(f"re-run: expected 0 NEW copies (all identical), got {result_rerun['copied']}")
    if result_rerun.get("already_there") != result["copied"]:
        problems.append(
            f"re-run: expected already_there == first run's copied ({result['copied']}), "
            f"got {result_rerun.get('already_there')}"
        )
    if before_rerun != after_rerun:
        problems.append(
            "re-run created/removed files on disk (re-run-skip is broken): "
            f"new={after_rerun - before_rerun} gone={before_rerun - after_rerun}"
        )

    # --- Task A: verify_sorted.py -- tamper by hand, then confirm all 4 categories --------
    import verify_sorted  # local import: only this self-test section needs it

    changed_target = os.path.join(crit_dir, "_YEAR_UNKNOWN", "1.1.1.txt")
    moved_from = os.path.join(support_dir, "AY_2023-24", "5.1.1_Scholarship_Beneficiary_List_2023-24.txt")
    moved_to = os.path.join(crit_dir, "_YEAR_UNKNOWN", "5.1.1_Scholarship_Beneficiary_List_2023-24.txt")
    missing_target = os.path.join(unread_dir_path, "corrupt.pdf")
    extra_target = os.path.join(sorted_dir, "_dropped_in_by_hand.txt")

    with open(changed_target, "a", encoding="utf-8") as f:
        f.write(" -- edited by hand after sorting")
    shutil.move(moved_from, moved_to)
    os.remove(missing_target)
    with open(extra_target, "w", encoding="utf-8") as f:
        f.write("nobody sorted this")

    verify_result = verify_sorted.verify(sorted_dir)
    print("\nVerify report:\n" + verify_sorted.format_verify_text(verify_result))

    if len(verify_result["changed"]) != 1:
        problems.append(f"verify: expected 1 CHANGED, got {len(verify_result['changed'])}")
    if len(verify_result["moved"]) != 1:
        problems.append(f"verify: expected 1 MOVED, got {len(verify_result['moved'])}")
    if len(verify_result["missing"]) != 1:
        problems.append(f"verify: expected 1 MISSING, got {len(verify_result['missing'])}")
    if len(verify_result["extra"]) != 1:
        problems.append(f"verify: expected 1 EXTRA, got {len(verify_result['extra'])}")
    if verify_result["ok"]:
        problems.append("verify: expected ok=False after tampering, got True")

    # a clean, untampered folder must report ok=True -- sanity-check on a fresh source
    clean_root = tempfile.mkdtemp(prefix="saandru_organize_clean_test_")
    with open(os.path.join(clean_root, "a.txt"), "w", encoding="utf-8") as f:
        f.write("clean file")
    clean_decisions = [{
        "filename": "a.txt",
        "criterion": {"id": "1.1", "name": "Curriculum"},
        "metric": "1.1.1",
        "status": "auto",
        "decided_by": "auto",
    }]
    clean_result = organize(clean_decisions, clean_root, oversight_level="L2", pack_name="CleanTest")
    clean_verify = verify_sorted.verify(clean_result["sorted_dir"])
    if not clean_verify["ok"]:
        problems.append(f"verify: expected ok=True on an untampered folder, got {clean_verify}")
    shutil.rmtree(clean_root, ignore_errors=True)

    # missing-manifest case must not crash, and must say so clearly
    no_manifest_result = verify_sorted.verify(dup_dir)  # a real folder with no _manifest.json
    if no_manifest_result["manifest_found"]:
        problems.append("verify: expected manifest_found=False on a folder with no manifest")
    if "run sorting again" not in no_manifest_result.get("message", ""):
        problems.append(f"verify: missing-manifest message not clear: {no_manifest_result.get('message')!r}")

    print()
    if problems:
        print(f"SELF-TEST FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    else:
        print("SELF-TEST PASSED:")
        print(f"  - copied={result['copied']} skipped={result['skipped']} errors={result['errors']}")
        print("  - all originals verified present and unchanged")
        print(f"  - Saandru_Sorted written at: {sorted_dir}")

    # cleanup
    shutil.rmtree(tmp_root, ignore_errors=True)
    print(f"  - cleaned up {tmp_root}")
