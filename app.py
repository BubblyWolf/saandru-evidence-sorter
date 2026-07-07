"""
Praman -- Accreditation Evidence Sorter
Single-file Streamlit UI for non-technical college office staff.

Everything runs locally. Nothing is uploaded anywhere.
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
from ingest import read_document       # noqa: E402
from discover import discover_files    # noqa: E402
from organize import organize          # noqa: E402
from enrich import extract_academic_year, suggest_name  # noqa: E402

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
st.set_page_config(page_title="Praman — Accreditation Evidence Sorter", page_icon="📄", layout="wide")

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

st.title("Praman — Accreditation Evidence Sorter")
st.markdown(
    '<div class="subtitle">Your documents stay on this computer. Nothing goes to the internet.</div>',
    unsafe_allow_html=True,
)


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


# --------------------------------------------------------------------------
# STEP 1 -- folder + pack
# --------------------------------------------------------------------------
st.markdown('<div class="big-step-title">Step 1 📁 — Where are your documents?</div>', unsafe_allow_html=True)
folder_path = st.text_input(
    "Folder path",
    value=st.session_state.get("folder_path", ""),
    placeholder=r"Example: D:\My College\Accreditation Documents",
    help="Tip: open the folder in File Explorer, click the address bar, copy the path (Ctrl+C), and paste it here (Ctrl+V).",
)
st.session_state["folder_path"] = folder_path

if not PACK_CHOICES:
    st.error(f"No criteria packs found in {CRITERIA_DIR}. Add a .yaml pack there first.")
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
st.markdown('<div class="big-step-title">Step 2 🎚️ — How much should the assistant do alone?</div>', unsafe_allow_html=True)

LEVEL_OPTIONS = {
    "L1": "I will check every file before it is filed",
    "L2": "File the confident ones, show me only the unsure ones",
    "L3": "File everything, just give me the summary and log",
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

start_col, _ = st.columns([1, 3])
with start_col:
    start_clicked = st.button("▶️  Start sorting", type="primary", use_container_width=True)

if start_clicked:
    if not folder_path or not os.path.isdir(folder_path):
        st.error("That folder path does not exist. Please check it and try again.")
    else:
        # discover_files() walks subfolders and never filters by extension --
        # every file (including unsupported/unreadable ones) shows up so it can
        # get a proper bracketed-marker reason in the "Could not read" tab
        # instead of silently disappearing.
        files = discover_files(folder_path)
        if not files:
            st.warning(
                "No files were found in that folder (this scans all common formats — "
                "txt/docx/doc/pdf/csv/xlsx/pptx/images — including subfolders)."
            )
        else:
            # reset state for a fresh run
            st.session_state.results = []
            st.session_state.audit_log = []
            st.session_state.review_index = 0
            st.session_state.run_done = False

            progress_bar = st.progress(0)
            status_text = st.empty()

            with st.spinner("Loading the criteria list..."):
                metrics, pack_name = load_metrics(pack_path)
                metric_vecs = embed_metrics(metrics, pack_name)
            st.session_state.metrics = metrics
            st.session_state.pack_name = pack_name

            total = len(files)
            for i, fn in enumerate(files, start=1):
                status_text.text(f"Reading file {i} of {total}: {fn}...")
                full_path = os.path.join(folder_path, fn)
                text = read_document(full_path)

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
                    st.session_state.results.append(record)
                    _log(fn, "-", 0.0, "could not read")
                    progress_bar.progress(i / total)
                    continue

                result = classify(text, metrics, metric_vecs)
                chosen = result["chosen"]

                year_info = extract_academic_year(text)
                # Task 1: reuse the title piggybacked onto the adjudication call instead of a
                # second LLM call; fall back to suggest_name() only when it's empty.
                suggested_name = result.get("title") or suggest_name(
                    text,
                    criterion_name=(chosen["criterion_name"] if chosen else ""),
                    metric_id=(chosen["id"] if chosen else ""),
                )

                record.update({
                    "confidence": result["confidence"],
                    "engine_status": result["status"],   # "auto" or "review" from the model
                    "evidence": result["evidence"],
                    "candidates": result["candidates"],
                    "doc_preview": text[:400],
                    "chosen_metric": chosen,  # dict or None
                    "year": year_info["year"],
                    "year_confidence": year_info["confidence"],
                    "suggested_name": suggested_name,
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

            status_text.text(f"Done. Processed {total} file(s).")
            st.session_state.run_done = True
            st.success(f"Finished sorting {total} document(s).")


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
        f"🟡 Please check these ({len(review_docs)})",
        f"❌ Could not read ({len(unreadable_docs)})",
    ])

    # ---- Auto bucket ----
    with tab_auto:
        st.markdown('<div class="bucket-green">These were filed automatically because the assistant was confident.</div>', unsafe_allow_html=True)
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
                })
            st.dataframe(table_rows, use_container_width=True, hide_index=True)

    # ---- Review bucket: one-at-a-time card ----
    with tab_review:
        st.markdown('<div class="bucket-amber">The assistant is not fully sure about these. Please check each one.</div>', unsafe_allow_html=True)

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
                suggestion_txt = f"{c['id']} ({c['criterion_name']})" if c else "no confident match"
                _log(r["file"], suggestion_txt, r.get("confidence", 0), "accepted")
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
                        _log(r["file"], f"{new_metric['id']} ({new_metric['criterion_name']})", 1.0, "changed by human")
                    st.rerun()

            if decided:
                with st.expander(f"Already reviewed ({len(decided)})"):
                    for d in decided:
                        dc = d.get("chosen_metric")
                        st.write(f"- **{d['file']}** -> {dc['id'] if dc else 'NONE'} ({dc['criterion_name'] if dc else ''})")

    # ---- Unreadable bucket ----
    with tab_bad:
        st.markdown('<div class="bucket-red">These files could not be opened or understood.</div>', unsafe_allow_html=True)
        if not unreadable_docs:
            st.info("Every file could be read. 🎉")
        else:
            for r in unreadable_docs:
                st.write(f"**{r['file']}** — {r['reason']}")

    st.divider()

    # ---- Download + audit log ----
    all_decided = all(
        r.get("decided_by") in ("auto", "human") for r in results if not r.get("unreadable")
    )

    if not all_decided:
        st.info("Finish reviewing the 🟡 unsure files above, then you can download the Excel index.")

    def _build_excel_bytes():
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

    dl_col, _ = st.columns([1, 3])
    with dl_col:
        excel_bytes = _build_excel_bytes()
        st.download_button(
            "⬇️  Download Excel index",
            data=excel_bytes,
            file_name="evidence_index.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    with st.expander("📜 Audit log (every decision made)"):
        if not st.session_state.audit_log:
            st.write("No decisions logged yet.")
        else:
            st.dataframe(st.session_state.audit_log, use_container_width=True, hide_index=True)

    st.divider()

    # ---- Organise into folders ----
    st.markdown('<div class="big-step-title">Step 4 📁 — Organise files into folders</div>', unsafe_allow_html=True)
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

        with st.spinner("Copying files into Praman_Sorted..."):
            summary = organize(decisions, folder_path, oversight_level)

        st.success(
            f"Copied {summary['copied']} file(s) into folders. "
            f"{len(summary['errors'])} problem(s)."
        )
        st.write(f"**Folders were created here:** `{summary['sorted_dir']}`")
        st.caption("These are COPIES. Your original files in the source folder are untouched.")

        if summary["errors"]:
            with st.expander(f"⚠️ {len(summary['errors'])} file(s) had a problem"):
                for err in summary["errors"]:
                    st.write(f"- {err}")

else:
    st.caption("Fill in Step 1 and Step 2 above, then press Start sorting.")
