"""
Saandru -- Accreditation Evidence Sorter
Single-file Streamlit UI for non-technical college office staff.

Everything runs locally. Nothing is uploaded anywhere.

Author: Chitranjan Jegadeesan (https://chitranjanjegadeesan.in/)
"""
import os
import io
import sys
import glob
import time
import datetime as dt

import yaml
import streamlit as st
from openpyxl import Workbook

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from pack import load_metrics          # noqa: E402
from pipeline import embed_metrics, classify  # noqa: E402
from ollama_client import CHAT_MODEL, CHAT_MODEL_REASON  # noqa: E402
from ingest import read_document       # noqa: E402
from discover import discover_files    # noqa: E402
from organize import organize          # noqa: E402
from verify_sorted import verify, format_verify_text  # noqa: E402
from enrich import extract_academic_year, suggest_name  # noqa: E402
from gap_report import build_gap_report, format_gap_report_text, format_summary_card_text, _shorten  # noqa: E402
import corrections                     # noqa: E402  -- Feature A: learn from human corrections
from duplicates import find_duplicates, file_sha256  # noqa: E402  -- Feature B: duplicate finder
from report_pdf import render_gap_report_pdf_bytes, render_gap_report_html_str  # noqa: E402 -- Task 1: PDF/HTML reports.
# Issue 1 fix: app.py only ever uses the in-memory bytes/string renderers above -- never
# the disk-writing write_gap_report_pdf()/write_gap_report_html() (those stay run.py-only,
# see src/report_pdf.py). A Streamlit rerun must not create/overwrite a report file.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CRITERIA_DIR = os.path.join(BASE_DIR, "criteria")

# Friendly labels for known criteria packs. Accreditation facts this UI must get right:
#   - NAAC accredits the WHOLE INSTITUTION (any college, including engineering colleges).
#   - NBA accredits ONE PROGRAMME (e.g. one engineering degree), not the whole college.
#   - NAAC has separate manuals for AFFILIATED/CONSTITUENT colleges vs AUTONOMOUS colleges.
# So an engineering college usually needs NAAC (for the institution) AND NBA (per programme).
KNOWN_PACK_LABELS = {
    "naac_affiliated_raf2021.yaml": "NAAC — Affiliated/Constituent college (whole institution)",
    "naac_autonomous_raf.yaml": "NAAC — Autonomous college (whole institution)",
    "nba_ug_engg_tier2_gapc_v4.yaml": "NBA — Engineering programme (Tier-II)",
}


def _discover_packs():
    """Scan criteria/*.yaml and build {friendly_label: path}, reading each file's
    institution_type/framework fields for the fallback label so a new pack dropped
    into criteria/ shows up automatically without code changes."""
    choices = {}
    for path in sorted(glob.glob(os.path.join(CRITERIA_DIR, "*.yaml"))):
        fname = os.path.basename(path)
        label = KNOWN_PACK_LABELS.get(fname)
        if label is None:
            try:
                with open(path, encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}
                label = meta.get("institution_type") or meta.get("framework") or fname
            except Exception:
                label = fname
        choices[label] = path
    return choices


PACK_CHOICES = _discover_packs()


# --------------------------------------------------------------------------
# Page setup + light styling
# --------------------------------------------------------------------------
st.set_page_config(page_title="Saandru (சான்று) — Accreditation Evidence Sorter", page_icon="📄", layout="wide")

st.markdown(
    """
    <style>
    .big-step-title {font-size: 1.4rem; font-weight: 700; margin-top: 0.6rem; margin-bottom: 0.2rem;}
    .subtitle {font-size: 1.05rem; color: #555; margin-bottom: 1.2rem;}
    .bucket-green {background-color: #e6f4ea; border-left: 6px solid #2e7d32; padding: 0.8rem 1rem; border-radius: 6px;}
    .bucket-amber {background-color: #fff8e1; border-left: 6px solid #f9a825; padding: 0.8rem 1rem; border-radius: 6px;}
    .bucket-red {background-color: #fdecea; border-left: 6px solid #c62828; padding: 0.8rem 1rem; border-radius: 6px;}
    .review-card {background-color: #fffdf5; border: 1px solid #f0d98c; border-radius: 8px; padding: 1.2rem; margin-bottom: 1rem;}
    div.stButton > button {font-size: 1.05rem; padding: 0.5rem 1.2rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("Saandru (சான்று) — Accreditation Evidence Sorter")
st.markdown(
    '<div class="subtitle">Your documents stay on this computer. Nothing goes to the internet.</div>',
    unsafe_allow_html=True,
)

with st.expander("ℹ️ What does this tool do? (read this first)", expanded=False):
    st.write("1. You point it at a folder of your college's documents (certificates, reports, notices — anything).")
    st.write("2. It reads each file and guesses which accreditation point (NAAC or NBA) it is evidence for.")
    st.write("3. It sorts the confident guesses into neat folders for you, and asks you to check the unsure ones.")
    st.write("4. At the end you get a tidy folder, an Excel list of every file, and a report of what is still missing.")
    st.markdown("**Your files never leave this computer. No internet is used.**")


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
def _init_state():
    defaults = {
        "results": [],          # list of dicts, one per processed document
        "audit_log": [],        # list of dicts: file, suggestion, confidence, human_action, timestamp
        "run_done": False,
        "metrics": None,
        "pack_name": None,
        "review_index": 0,      # which review card is currently shown
        # Issue 1 fix: cached in-memory report bytes + the decisions-signature they were
        # built from, so download_button gets stable `data=` across unrelated reruns.
        "_report_sig": None,
        "_report_cache": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


_init_state()


def _now():
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(file, suggestion, confidence, human_action):
    st.session_state.audit_log.append({
        "file": file,
        "suggestion": suggestion,
        "confidence": confidence,
        "human_action": human_action,
        "timestamp": _now(),
    })


def _friendly_error(user_message, exc):
    """One line the office staff can actually understand, plus the raw Python
    error tucked away in a collapsed expander for whoever helps them later.
    Never shows a raw stack trace directly on screen."""
    st.error(user_message)
    with st.expander("Technical details (for the person helping you)"):
        st.exception(exc)


def _format_time_left(seconds_left):
    """Turn a raw seconds estimate into a plain-English phrase for someone who
    has never seen a progress bar with a time estimate before."""
    if seconds_left is None or seconds_left <= 0:
        return "almost done"
    minutes_left = round(seconds_left / 60)
    if minutes_left < 1:
        return "less than a minute left"
    if minutes_left == 1:
        return "about 1 minute left"
    return f"about {minutes_left} minutes left"


def _progress_message(index_1based, total, elapsed_seconds):
    """'Reading file 3 of 120 -- about 8 minutes left'. Estimate = files still
    left times the rolling average seconds-per-file seen SO FAR this run --
    no new session-state keys, just the loop counters already in hand."""
    avg_seconds_per_file = elapsed_seconds / index_1based if index_1based else 0
    files_left = total - index_1based
    eta_seconds = avg_seconds_per_file * files_left
    return f"Reading file {index_1based} of {total} -- {_format_time_left(eta_seconds)}"


def _decisions_signature(results):
    """A hashable snapshot of exactly what the downloadable reports depend on.
    Streamlit reruns this WHOLE script on every button click -- Accept, Skip,
    Organise, Check my folder, anything -- so building the report bytes
    unconditionally on every rerun used to hand each download_button a brand
    new `data` object (with a freshly-stamped "Generated: <time>" line inside
    it) even when the user clicked something that changed nothing about the
    decisions. Streamlit treats a changed `data=` as a new file and re-serves
    the download -- that is the "every click re-downloads the PDF" bug a
    faculty tester hit. Caching the built bytes against this signature (see the
    Coverage & Gaps section below) means the bytes only regenerate when a
    decision genuinely changes (an Accept/Change/Save), not on every rerun."""
    return tuple(
        (r.get("file"), (r.get("chosen_metric") or {}).get("id"), r.get("status"), r.get("decided_by"))
        for r in results
    )


def _build_excel_bytes(results):
    """Evidence Index workbook as an in-memory .xlsx -- returns a BytesIO, ready
    for st.download_button's `data=`. Pulled out to module level (it used to be
    a closure defined right before its one call site) so the report-caching
    block below can call it too."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Evidence Index"
    ws.append(["File", "Criterion", "Metric", "Year", "Confidence", "Decided by",
               "Evidence quote", "Suggested name"])
    for r in results:
        if r.get("unreadable"):
            ws.append([r["file"], "-", "-", "-", "-", "-", r.get("reason", ""), "-"])
            continue
        c = r.get("chosen_metric")
        ws.append([
            r["file"],
            f"{c['criterion']} - {c['criterion_name']}" if c else "-",
            c["id"] if c else "NONE",
            r.get("year") or "-",
            r.get("confidence", 0),
            r.get("decided_by", "pending"),
            r.get("evidence", ""),
            r.get("suggested_name", ""),
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# --------------------------------------------------------------------------
# STEP 1 -- folder + pack
# --------------------------------------------------------------------------
st.markdown('<div class="big-step-title">Step 1 📁 — Show me where your documents are</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Paste the folder path below. This is the folder that has your certificates, reports, notices, etc.</div>', unsafe_allow_html=True)
folder_path = st.text_input(
    "Folder path",
    value=st.session_state.get("folder_path", ""),
    placeholder=r"Example: D:\My College\Accreditation Documents",
    help="Tip: open the folder in File Explorer, click the address bar, copy the path (Ctrl+C), and paste it here (Ctrl+V).",
)
st.session_state["folder_path"] = folder_path

if folder_path and not os.path.isdir(folder_path):
    st.warning(
        "This folder was not found. Check for spelling mistakes, or copy the path "
        "again from File Explorer (click the address bar, press Ctrl+C, then paste here with Ctrl+V)."
    )

if not PACK_CHOICES:
    st.error(
        "No accreditation checklists were found on this computer. "
        f"Please ask a technical person to add a .yaml file to the '{CRITERIA_DIR}' folder."
    )
    st.stop()

pack_choice_label = st.radio(
    "Which accreditation type is this for?",
    list(PACK_CHOICES.keys()),
    index=0,
    help="Choose the accreditation body this batch of documents is for.",
)
st.caption(
    "NAAC = the whole college. NBA = one engineering programme. "
    "Engineering colleges usually need both."
)
pack_path = PACK_CHOICES[pack_choice_label]

st.divider()

# --------------------------------------------------------------------------
# STEP 2 -- oversight level
# --------------------------------------------------------------------------
st.markdown('<div class="big-step-title">Step 2 🎚️ — How much should the assistant do on its own?</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">Choose how much you want to check yourself, and how much the assistant can decide alone.</div>', unsafe_allow_html=True)

# NOTE: only the VISIBLE label text changes here for non-technical staff.
# The underlying keys "L1"/"L2"/"L3" and everything that reads oversight_level
# elsewhere in this file (bucketing, organize(), the corrections memory) are
# untouched -- do not rename these dict keys.
LEVEL_OPTIONS = {
    "L1": "Safest — I check every file myself",
    "L2": "Balanced — it files the sure ones, I check the rest (recommended)",
    "L3": "Fastest — it files everything, I just get the report",
}
level_label_to_key = {v: k for k, v in LEVEL_OPTIONS.items()}

level_choice_label = st.radio(
    "Choose one",
    list(LEVEL_OPTIONS.values()),
    index=1,  # L2 default
)
oversight_level = level_label_to_key[level_choice_label]
st.session_state["oversight_level"] = oversight_level

st.divider()

# --------------------------------------------------------------------------
# STEP 3 -- run
# --------------------------------------------------------------------------
st.markdown('<div class="big-step-title">Step 3 ▶️ — Start sorting</div>', unsafe_allow_html=True)
st.markdown('<div class="subtitle">The assistant will read every file and suggest where it belongs. This can take a few minutes.</div>', unsafe_allow_html=True)
st.caption(f"Reading model on this PC: {CHAT_MODEL} ({CHAT_MODEL_REASON})")

start_col, _ = st.columns([1, 3])
with start_col:
    start_clicked = st.button("▶️  Start sorting", type="primary", use_container_width=True, key="btn_start_sorting")

if start_clicked:
    if not folder_path or not os.path.isdir(folder_path):
        st.error(
            "This folder was not found. Check for spelling mistakes, or copy the path "
            "again from File Explorer."
        )
    else:
        # discover_files() walks subfolders and never filters by extension --
        # every file (including unsupported/unreadable ones) shows up so it can
        # get a proper bracketed-marker reason in the "Could not read" tab
        # instead of silently disappearing.
        files = discover_files(folder_path)
        if not files:
            st.warning(
                "No files were found in that folder. Saandru looks for these formats: "
                "txt, docx, doc, pdf, csv, xlsx, pptx, and photo/scan formats (jpg, png). "
                "Please check that your documents are inside this folder (subfolders are fine — "
                "Saandru looks inside them too)."
            )
        else:
            # reset state for a fresh run
            st.session_state.results = []
            st.session_state.audit_log = []
            st.session_state.review_index = 0
            st.session_state.run_done = False

            progress_bar = st.progress(0)
            status_text = st.empty()
            run_start_time = time.time()

            with st.spinner("Loading the accreditation checklist..."):
                metrics, pack_name = load_metrics(pack_path)
                metric_vecs = embed_metrics(metrics, pack_name)
            st.session_state.metrics = metrics
            st.session_state.pack_name = pack_name

            total = len(files)
            for i, fn in enumerate(files, start=1):
                status_text.text(_progress_message(i, total, time.time() - run_start_time))
                full_path = os.path.join(folder_path, fn)
                try:
                    text = read_document(full_path)
                except Exception as e:
                    # A single bad/locked file must never stop the whole batch --
                    # treat it like any other unreadable file and keep going.
                    text = f"[UNREADABLE: {e}]"

                record = {
                    "file": fn,
                    "path": full_path,
                    "unreadable": False,
                    "reason": "",
                }

                if text.startswith("[UNREADABLE:") or text.startswith("[UNSUPPORTED FORMAT:"):
                    record["unreadable"] = True
                    inner = text.strip("[]")
                    if inner.startswith("UNREADABLE:"):
                        reason = "The file could not be opened. Details: " + inner.split(":", 1)[1].strip()
                    else:
                        reason = "This file type is not supported yet."
                    record["reason"] = reason
                    record["status"] = "unreadable"
                    # sha256 still works on an unreadable file (it's a hash of raw bytes, no
                    # parsing needed) -- two unreadable copies of the same bad file should still
                    # show up as an exact duplicate. No doc_vec though: nothing was classified.
                    try:
                        record["sha256"] = file_sha256(full_path)
                    except Exception:
                        record["sha256"] = None
                    record["doc_vec"] = None
                    st.session_state.results.append(record)
                    _log(fn, "-", 0.0, "could not read")
                    progress_bar.progress(i / total)
                    continue

                try:
                    result = classify(text, metrics, metric_vecs, pack_name=pack_name)
                    chosen = result["chosen"]

                    year_info = extract_academic_year(text)
                    # Task 1: reuse the title piggybacked onto the adjudication call instead of a
                    # second LLM call; fall back to suggest_name() only when it's empty.
                    suggested_name = result.get("title") or suggest_name(
                        text,
                        criterion_name=(chosen["criterion_name"] if chosen else ""),
                        metric_id=(chosen["id"] if chosen else ""),
                    )
                except Exception as e:
                    # Same rule as above: one document's classifier error must not stop
                    # the whole batch -- park it in "Could not be opened" and move on.
                    record["unreadable"] = True
                    record["reason"] = f"The assistant could not read this file properly. Details: {e}"
                    record["status"] = "unreadable"
                    try:
                        record["sha256"] = file_sha256(full_path)
                    except Exception:
                        record["sha256"] = None
                    record["doc_vec"] = None
                    st.session_state.results.append(record)
                    _log(fn, "-", 0.0, "could not read")
                    progress_bar.progress(i / total)
                    continue

                record.update({
                    "confidence": result["confidence"],
                    "engine_status": result["status"],   # "auto" or "review" from the model
                    # metric-vs-criterion commit granularity: the gap report needs it to
                    # count strong vs tentative evidence honestly (None = engine unsure).
                    "commit_level": result.get("commit_level"),
                    "evidence": result["evidence"],
                    "candidates": result["candidates"],
                    "doc_preview": text[:400],
                    "chosen_metric": chosen,  # dict or None
                    "year": year_info["year"],
                    "year_confidence": year_info["confidence"],
                    "suggested_name": suggested_name,
                    # Feature A: this doc's embedding, kept so corrections.record() can be called
                    # later if a human corrects/confirms it in the review tab below.
                    "doc_vec": result.get("doc_vec"),
                    # True when corrections memory recognised this doc outright (>=0.95 similarity
                    # to a past human correction) -- surfaced as a small tag in the results table.
                    "learned": result.get("learned", False),
                    # Feature B: file hash for exact-duplicate detection across this run.
                    "sha256": file_sha256(full_path),
                })

                # decide bucket based on oversight level + engine status
                if oversight_level == "L1":
                    record["status"] = "review"
                elif oversight_level == "L2":
                    record["status"] = "review" if result["status"] == "review" else "auto"
                else:  # L3
                    record["status"] = "auto"

                record["decided_by"] = "auto" if record["status"] == "auto" else "pending"

                st.session_state.results.append(record)

                suggestion_txt = (
                    f"{chosen['id']} ({chosen['criterion_name']})" if chosen else "no confident match"
                )
                if record["status"] == "auto":
                    _log(fn, suggestion_txt, result["confidence"], "auto-filed")

                progress_bar.progress(i / total)

            status_text.text(f"Done. Looked at {total} file(s).")
            st.session_state.run_done = True
            st.success(f"Finished reading {total} document(s). Scroll down to see the results.")


st.divider()

# --------------------------------------------------------------------------
# RESULTS
# --------------------------------------------------------------------------
if st.session_state.run_done:
    results = st.session_state.results
    metrics = st.session_state.metrics or []

    auto_docs = [r for r in results if not r.get("unreadable") and r["status"] == "auto"]
    review_docs = [r for r in results if not r.get("unreadable") and r["status"] == "review"]
    unreadable_docs = [r for r in results if r.get("unreadable")]

    tab_auto, tab_review, tab_bad = st.tabs([
        f"✅ Filed automatically ({len(auto_docs)})",
        f"🟡 Waiting for your check ({len(review_docs)})",
        f"🔴 Could not be opened ({len(unreadable_docs)})",
    ])

    # ---- Auto bucket ----
    with tab_auto:
        st.markdown('<div class="bucket-green">These were filed automatically ✅ because the assistant was confident.</div>', unsafe_allow_html=True)
        if not auto_docs:
            st.info("No documents were filed automatically yet.")
        else:
            table_rows = []
            for r in auto_docs:
                c = r.get("chosen_metric")
                table_rows.append({
                    "File": r["file"],
                    "Criterion": f"{c['criterion']} - {c['criterion_name']}" if c else "-",
                    "Metric": c["id"] if c else "-",
                    "Year": r.get("year") or "-",
                    "Confidence": f"{r.get('confidence', 0):.0%}",
                    "Evidence quote": r.get("evidence", ""),
                    # Feature A: shows when corrections memory recognised this exact document
                    # from an earlier human correction, instead of the usual vote.
                    "Notes": "🧠 learned from your earlier correction" if r.get("learned") else "",
                })
            st.dataframe(table_rows, use_container_width=True, hide_index=True)

    # ---- Review bucket: one-at-a-time card ----
    with tab_review:
        st.markdown('<div class="bucket-amber">🟡 The assistant is not fully sure about these. Please check each one.</div>', unsafe_allow_html=True)

        pending = [r for r in review_docs if r.get("decided_by") != "human"]
        decided = [r for r in review_docs if r.get("decided_by") == "human"]

        if not review_docs:
            st.info("Nothing needs your review. 🎉")
        elif not pending:
            st.success("You have reviewed all of them. 🎉")
        else:
            # clamp index
            if st.session_state.review_index >= len(pending):
                st.session_state.review_index = 0
            idx = st.session_state.review_index
            r = pending[idx]
            c = r.get("chosen_metric")

            st.caption(f"Reviewing {idx + 1} of {len(pending)} unsure files")
            st.caption("Not sure? Read the document preview below, then press Accept, or pick the right shelf from the list.")

            with st.container():
                st.markdown('<div class="review-card">', unsafe_allow_html=True)
                st.subheader(r["file"])
                st.text_area("First part of the document", r.get("doc_preview", ""), height=160, disabled=True, key=f"preview_{r['file']}_{idx}")

                if c:
                    st.write(
                        f"We think this belongs to **Criterion {c['criterion']} ({c['criterion_name']})**, "
                        f"metric **{c['id']}**, because: \"{r.get('evidence', '')}\""
                    )
                    st.caption(f"Confidence: {r.get('confidence', 0):.0%}")
                else:
                    st.write("We could not find a confident match for this document.")

                detected_year = r.get("year")
                if detected_year:
                    st.caption(f"Detected year: **{detected_year}** "
                               f"({r.get('year_confidence', 'low')} confidence)")
                else:
                    st.caption("Detected year: none found")

                # Editable document name -- prefilled with the model's suggestion so the
                # human can correct it before filing. This IS the intended human-oversight
                # point: the assistant suggests, the person confirms or fixes it.
                edited_name = st.text_input(
                    "Document name (edit if needed, this is used when filing)",
                    value=r.get("suggested_name", ""),
                    key=f"name_{r['file']}_{idx}",
                )
                r["suggested_name"] = edited_name

                st.caption(
                    "Your corrections teach the tool -- similar documents will be suggested "
                    "this metric automatically next time."
                )

                col_accept, col_skip = st.columns([1, 1])
                with col_accept:
                    accept_clicked = st.button("✅ Accept", key=f"accept_{r['file']}_{idx}", use_container_width=True)
                with col_skip:
                    skip_clicked = st.button("⏭️ Skip for now", key=f"skip_{r['file']}_{idx}", use_container_width=True)

                st.markdown("**Choose a different metric (optional)**")
                metric_options = ["-- keep suggestion above --"] + [
                    f"{m['id']} | Criterion {m['criterion']} - {m['criterion_name']} | {m['ki_name']}"
                    for m in metrics
                ]
                chosen_alt = st.selectbox(
                    "All metrics",
                    metric_options,
                    key=f"altselect_{r['file']}_{idx}",
                    label_visibility="collapsed",
                )
                save_choice_clicked = st.button("💾 Save choice", key=f"save_{r['file']}_{idx}")

                st.markdown("</div>", unsafe_allow_html=True)

            if accept_clicked:
                r["decided_by"] = "human"
                r["status"] = "auto"
                # a human looked at the doc and confirmed the metric -- that's the
                # strongest evidence there is; upgrade whatever the engine's level was.
                r["commit_level"] = "metric"
                suggestion_txt = f"{c['id']} ({c['criterion_name']})" if c else "no confident match"
                _log(r["file"], suggestion_txt, r.get("confidence", 0), "accepted")
                # Feature A: remember this doc's embedding + the confirmed metric so a future
                # similar document gets suggested (or auto-filed) the same way. Only meaningful
                # when there WAS a metric to confirm -- "no confident match" teaches nothing.
                if c:
                    corrections.record(
                        st.session_state.pack_name, c["id"], r.get("doc_vec"), r["file"],
                        note="accepted",
                    )
                st.rerun()

            if skip_clicked:
                st.session_state.review_index = (idx + 1) % max(len(pending), 1)
                st.rerun()

            if save_choice_clicked:
                if chosen_alt == "-- keep suggestion above --":
                    st.warning("Please pick a metric from the list first, or use Accept instead.")
                else:
                    new_id = chosen_alt.split("|")[0].strip()
                    new_metric = next((m for m in metrics if m["id"] == new_id), None)
                    if new_metric:
                        r["chosen_metric"] = new_metric
                        r["decided_by"] = "human"
                        r["status"] = "auto"
                        r["confidence"] = 1.0
                        # human hand-picked the metric -- strongest evidence, same as Accept.
                        r["commit_level"] = "metric"
                        _log(r["file"], f"{new_metric['id']} ({new_metric['criterion_name']})", 1.0, "changed by human")
                        # Feature A: the human picked a DIFFERENT metric than the engine
                        # suggested -- this is the most valuable kind of correction to remember.
                        corrections.record(
                            st.session_state.pack_name, new_metric["id"], r.get("doc_vec"),
                            r["file"], note="changed by human",
                        )
                    st.rerun()

            if decided:
                with st.expander(f"Already reviewed ({len(decided)})"):
                    for d in decided:
                        dc = d.get("chosen_metric")
                        st.write(f"- **{d['file']}** -> {dc['id'] if dc else 'NONE'} ({dc['criterion_name'] if dc else ''})")

    # ---- Unreadable bucket ----
    with tab_bad:
        st.markdown('<div class="bucket-red">🔴 These files could not be opened or understood.</div>', unsafe_allow_html=True)
        if not unreadable_docs:
            st.info("Every file could be read. 🎉")
        else:
            for r in unreadable_docs:
                st.write(f"**{r['file']}** — {r['reason']}")

    st.divider()

    # ---- Coverage & Gaps (deterministic, no LLM -- pure counting over the decisions
    # above, including any human Accept/Change corrections made in the review tab).
    # Uses `results` directly (not a copy) so a correction made a moment ago in the
    # 🟡 tab is already reflected here -- that is the whole point of showing this
    # AFTER the review step instead of right after the run finishes.
    st.markdown('<div class="big-step-title">Coverage & Gaps 📊 — what evidence do we have?</div>', unsafe_allow_html=True)
    st.caption("Counts your decisions above (Filed automatically ✅ + anything you confirmed), including any corrections you just made.")

    gap_decisions = [
        {
            "filename": r["file"],
            "status": r.get("status"),
            "chosen": r.get("chosen_metric"),
            "unreadable": r.get("unreadable", False),
            # thread the granularity through so tentative (criterion-only / L3
            # blanket-trust) evidence is never displayed as strong in the UI.
            "commit_level": r.get("commit_level"),
            # Issue 3: needed for the "documents on file" / "DOCUMENTS FILED" listings.
            "year": r.get("year"),
        }
        for r in results
    ]
    gap_report = build_gap_report(gap_decisions, metrics)
    gap_overall = gap_report["overall"]

    def _card_pct(n):
        return f"{round(100 * n / gap_overall['docs_scanned'])}%" if gap_overall["docs_scanned"] else "0%"

    tile1, tile2, tile3, tile4 = st.columns(4)
    tile1.metric("Files looked at", gap_overall["docs_scanned"])
    tile2.metric("Sorted automatically", gap_overall["committed"], _card_pct(gap_overall["committed"]))
    tile3.metric("Needs your review", gap_overall["in_review"], _card_pct(gap_overall["in_review"]))
    tile4.metric("Could not read", gap_overall["unreadable"], _card_pct(gap_overall["unreadable"]))

    st.markdown("**Coverage by criterion** — how much of each criterion has strong evidence.")
    for crit in gap_report["criteria"]:
        cov_col, num_col = st.columns([4, 1])
        with cov_col:
            st.write(f"Criterion {crit['id']} — {crit['name']}")
            st.progress(crit["coverage_pct"] / 100.0)
        with num_col:
            st.write(f"{crit['metrics_strong']}/{crit['total_metrics']} ({crit['coverage_pct']:.0f}%)")

        missing_rows = [row for ki in crit["kis"] for row in ki["metrics"]
                         if row["evidence_count"] == 0 and row["tentative_count"] == 0]
        tentative_rows = [row for ki in crit["kis"] for row in ki["metrics"]
                           if row["evidence_count"] == 0 and row["tentative_count"] > 0]
        if missing_rows or tentative_rows:
            with st.expander(
                f"See gaps for Criterion {crit['id']} "
                f"({len(missing_rows)} missing, {len(tentative_rows)} tentative)"
            ):
                if missing_rows:
                    st.write("**MISSING — no evidence found yet:**")
                    for row in missing_rows:
                        st.write(f"- {row['id']}: {_shorten(row['text'])}")
                if tentative_rows:
                    st.write("**TENTATIVE — needs a human to confirm:**")
                    for row in tentative_rows:
                        st.write(f"- {row['id']}: {_shorten(row['text'])}")

    # ---- Build (or reuse) all downloadable report bytes ONCE per result-set ----
    # Issue 1 fix: this used to rebuild the .txt/PDF/HTML/Excel payloads -- and write
    # PDF/HTML files under output/_app_report_tmp -- on EVERY Streamlit rerun (every
    # single button click reruns this whole script). Each rebuild embedded a fresh
    # "Generated: <timestamp>" line, so every download_button got a brand new `data`
    # object each time and Streamlit re-served the download, even for a click that
    # changed nothing (Organise, Check my folder, an unrelated widget). Fix: build the
    # bytes/string ONCE, cache them in session_state keyed by a signature of the actual
    # decisions (see _decisions_signature() above), and only rebuild when that signature
    # changes (i.e. the user actually Accepted/Changed/Saved a review). Nothing is
    # written to disk here -- run.py's console/batch path still writes the real
    # gap_report.pdf/.txt/.html files, unchanged.
    report_sig = (st.session_state.pack_name, _decisions_signature(results))
    if st.session_state.get("_report_sig") != report_sig:
        try:
            report_txt = (
                format_summary_card_text(gap_report, st.session_state.pack_name)
                + "\n\n" + format_gap_report_text(gap_report, st.session_state.pack_name)
            )
            # render_gap_report_pdf_bytes() never raises -- it returns None only when
            # reportlab itself is missing, in which case the HTML string (always
            # produced) is offered instead so office staff always get SOMETHING to
            # download and print.
            pdf_bytes = render_gap_report_pdf_bytes(gap_report, st.session_state.pack_name)
            html_str = render_gap_report_html_str(gap_report, st.session_state.pack_name)
            st.session_state["_report_cache"] = {
                "txt": report_txt.encode("utf-8"),
                "pdf": pdf_bytes,
                "html": html_str.encode("utf-8"),
                "excel": _build_excel_bytes(results).getvalue(),
            }
            st.session_state["_report_sig"] = report_sig
        except Exception as e:
            _friendly_error("The report files could not be prepared right now. "
                             "Try again, or check the technical details below.", e)
            # leave "_report_sig" unset so the next rerun retries the build; give the
            # download buttons below SOMETHING empty to point at meanwhile rather than
            # a None that would crash the subscripting below.
            if not st.session_state.get("_report_cache"):
                st.session_state["_report_cache"] = {"txt": b"", "pdf": None, "html": b"", "excel": b""}
    _reports = st.session_state["_report_cache"]

    st.download_button(
        "⬇️  Download gap report (.txt)",
        data=_reports["txt"],
        file_name="gap_report.txt",
        mime="text/plain",
        key="dl_txt",
    )

    # ---- PDF report (Task 1, in-memory -- Issue 1 fix): offer whichever of PDF/HTML
    # actually turned into a real PDF. `_reports["pdf"]` is None only when reportlab is
    # not installed on this machine; the HTML string (always produced) is offered
    # instead so office staff always get SOMETHING to download and print.
    if _reports["pdf"]:
        st.download_button(
            "⬇️  Download PDF report",
            data=_reports["pdf"],
            file_name="gap_report.pdf",
            mime="application/pdf",
            key="dl_pdf",
        )
    else:
        st.download_button(
            "⬇️  Download report (open in browser)",
            data=_reports["html"],
            file_name="gap_report.html",
            mime="text/html",
            key="dl_html",
        )
        st.caption("Open this file and press Ctrl+P to save as PDF.")

    # ---- Duplicate finder (Feature B): deterministic, embeddings + sha256 only -- no LLM. ----
    dup_items = [
        {"filename": r["file"], "sha256": r.get("sha256"), "doc_vec": r.get("doc_vec")}
        for r in results
    ]
    dup_groups = find_duplicates(dup_items)
    if dup_groups:
        with st.expander(f"⚠️ Possible duplicates found ({len(dup_groups)} group(s))", expanded=False):
            st.warning("These files look like copies of each other -- keep one, remove the rest.")
            for i, g in enumerate(dup_groups, start=1):
                kind_label = "identical file" if g["kind"] == "exact" else "looks like the same document"
                st.write(f"**Group {i}** ({kind_label}):")
                for f in g["files"]:
                    st.write(f"- {f}")

    st.divider()

    # ---- Download + audit log ----
    all_decided = all(
        r.get("decided_by") in ("auto", "human") for r in results if not r.get("unreadable")
    )

    if not all_decided:
        st.info("Finish reviewing the 🟡 unsure files above, then you can download the Excel index.")

    dl_col, _ = st.columns([1, 3])
    with dl_col:
        st.download_button(
            "⬇️  Download Excel index",
            data=_reports["excel"],
            file_name="evidence_index.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="dl_excel",
        )

    with st.expander("📜 Audit log (every decision made)"):
        if not st.session_state.audit_log:
            st.write("No decisions logged yet.")
        else:
            st.dataframe(st.session_state.audit_log, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Organise into folders ----
    st.markdown('<div class="big-step-title">Step 4 📁 — Organise files into folders</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">This makes a tidy copy of your files, sorted by criterion. Your originals are never touched.</div>', unsafe_allow_html=True)
    st.caption("Copies only — your original files are not moved or changed.")

    # At L1/L2 the human must finish deciding every 🟡 file first (all_decided covers
    # this). At L3 everything was auto-filed the moment classification finished, so
    # all_decided is already True right after the run and the button is available
    # immediately -- no extra human step is required at L3.
    organize_disabled = not all_decided
    if organize_disabled:
        st.info("Finish reviewing the 🟡 unsure files above before organising into folders.")

    org_col, _ = st.columns([1, 3])
    with org_col:
        organize_clicked = st.button(
            "📁 Organise files into folders",
            type="primary",
            use_container_width=True,
            disabled=organize_disabled,
            key="btn_organize",
        )

    if organize_clicked:
        decisions = []
        for r in results:
            c = r.get("chosen_metric")
            decisions.append({
                "filename": r["file"],
                "path": r.get("path"),
                "criterion": (
                    {"id": c["criterion"], "name": c["criterion_name"]} if c else None
                ),
                "metric": c["id"] if c else None,
                "status": r.get("status"),
                "decided_by": r.get("decided_by"),
                "unreadable": r.get("unreadable", False),
                "reason": r.get("reason", ""),
                "year": r.get("year"),
                "suggested_name": r.get("suggested_name", ""),
            })

        try:
            with st.spinner("Copying files into Saandru_Sorted..."):
                summary = organize(decisions, folder_path, oversight_level, pack_name=st.session_state.pack_name)
        except Exception as e:
            summary = None
            _friendly_error(
                "Something went wrong while copying your files into folders. "
                "Your original files were not touched. Please try again.", e)

        if summary:
            st.success(
                f"Copied {summary['copied']} file(s) into folders "
                f"({summary.get('already_there', 0)} already there, unchanged). "
                f"{len(summary['errors'])} problem(s)."
            )
            st.write(f"**Folders were created here:** `{summary['sorted_dir']}`")
            st.caption("These are COPIES. Your original files in the source folder are untouched.")

            if summary["errors"]:
                with st.expander(f"⚠️ {len(summary['errors'])} file(s) had a problem"):
                    for err in summary["errors"]:
                        st.write(f"- {err}")

            st.session_state.last_sorted_dir = summary["sorted_dir"]

            # ---- End-of-flow guidance: plain-words summary of what the office
            # staff member now has, and where to find each thing. ----
            st.markdown("#### You are done ✅ — what you have now")
            st.write(f"1. **A sorted folder** with copies of every file, organised by criterion: `{summary['sorted_dir']}`")
            st.write("2. **An Excel index** listing every file and where it went — use the 'Download Excel index' button above.")
            st.write("3. **A gap report (PDF or report file)** showing what evidence is still missing — use the report download button above.")

    # ---- Tamper check ----
    # Only shows once a Saandru_Sorted folder exists for this source folder (either just
    # organized above, or from an earlier run) -- checks it against its own _manifest.json.
    default_sorted_dir = os.path.join(folder_path, "Saandru_Sorted") if folder_path else None
    check_target = st.session_state.get("last_sorted_dir") or default_sorted_dir
    if check_target and os.path.isdir(check_target):
        st.caption("This checks that no one has quietly changed, moved, or deleted a filed document by hand.")
        if st.button("🛡 Check my folder", use_container_width=False, key="btn_verify"):
            try:
                with st.spinner("Comparing the folder against its record..."):
                    verify_result = verify(check_target)
                if not verify_result.get("manifest_found"):
                    st.warning(verify_result.get("message", "Could not check this folder."))
                elif verify_result["ok"]:
                    st.success("Folder matches the record ✔ -- nothing was changed, moved, or deleted by hand.")
                else:
                    total_issues = (
                        len(verify_result["changed"]) + len(verify_result["moved"])
                        + len(verify_result["missing"]) + len(verify_result["extra"])
                    )
                    st.warning(f"{total_issues} issue(s) found -- see below.")
                    st.text(format_verify_text(verify_result))
            except Exception as e:
                _friendly_error(
                    "Something went wrong while checking this folder. Your files are safe -- "
                    "this check just could not run right now.", e)

else:
    st.caption("Fill in Step 1 and Step 2 above, then press ▶️ Start sorting.")

# ---- signature footer (shown on every screen state) ----
st.divider()
st.caption("Saandru (சான்று) — built by **[Chitranjan Jegadeesan](https://chitranjanjegadeesan.in/)** · runs fully on your computer")
