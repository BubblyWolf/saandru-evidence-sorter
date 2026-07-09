# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Saandru -- the Gap Report: "what evidence is MISSING" for a criteria pack.

100% deterministic. This module never calls the LLM -- it only counts and sorts
decisions the classifier (pipeline.classify, via run.py or app.py) already made.

A "decision" here is the same per-file dict run.py/app.py already build while
looping over documents. Recognised keys (all optional except filename):
    - filename / file    : the document's name
    - status              : "auto" / "review" / "unreadable"
    - unreadable          : bool (also honoured; run.py callers may not set "status"
                             to "unreadable" the way app.py does)
    - commit_level        : "metric" / "criterion" / None. "metric" means the model
                             (or a human) landed on the exact metric with confidence --
                             that is STRONG evidence. "criterion" means only the broad
                             area was confirmed and the exact metric is a best guess --
                             that is TENTATIVE, never strong. When this key is absent
                             (app.py's simpler oversight-level bucketing does not track
                             it) an "auto" decision with a chosen metric is treated as
                             a metric-level (strong) commit, since app.py's L2/L3 auto
                             bucket already means "confident enough to file automatically".
    - chosen / chosen_metric : the metric dict (id/criterion/criterion_name/ki/ki_name/text)
                             the document was matched to, or None/absent.

Nothing here decides folders or touches disk -- it is pure counting so it can be unit
tested without Ollama and reused by both run.py (console + Excel) and app.py (Streamlit).
"""
import re


def _shorten(text, n=110):
    """Trim `text` to at most `n` characters WITHOUT cutting a word in half, and only
    append an ellipsis when something was actually removed. Also collapses runs of
    whitespace first -- some metric texts carry a stray internal space from the source
    PDF/OCR extraction (e.g. "Numbe r", "regio n"), and squashing that first stops a
    broken word from tripping up the word-boundary check below. A text that already
    fits within `n` chars is returned untouched -- it must never gain a trailing
    ellipsis just because it happened to end near the limit."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip() + "…"


def _get_chosen(decision):
    """run.py calls this key "chosen"; app.py calls it "chosen_metric". Accept either."""
    return decision.get("chosen") or decision.get("chosen_metric")


def _get_filename(decision):
    """run.py/app.py both use "filename" for the decisions they build, but keep the
    same "file" fallback the rest of this module already tolerates elsewhere, and never
    blow up a report just because one decision dict is missing it."""
    return decision.get("filename") or decision.get("file") or "(unknown file)"


def _is_unreadable(decision):
    if decision.get("unreadable"):
        return True
    return (decision.get("status") or "").lower() == "unreadable"


def _is_review(decision):
    return (decision.get("status") or "").lower() == "review"


def _is_committed(decision):
    return (decision.get("status") or "").lower() == "auto"


def build_gap_report(decisions, metrics):
    """Return a dict describing, per criterion/KI/metric, what evidence exists and what
    is missing -- built purely from `decisions` (already-computed classification results)
    and `metrics` (the pack's own list of what SHOULD be covered, from pack.load_metrics).
    """
    decisions = decisions or []

    # ---- scaffold the full criterion/KI/metric tree from the PACK, not from decisions ----
    # WHY: a gap report's whole point is to show metrics with ZERO evidence. If we built the
    # tree from decisions we would never see the metrics nobody has a document for yet.
    criteria = {}   # cid -> {"id","name","kis": {kid -> {"id","name","metrics": {mid -> row}}}}
    for m in metrics:
        crit = criteria.setdefault(m["criterion"], {
            "id": m["criterion"], "name": m["criterion_name"], "kis": {}, "_order": len(criteria),
        })
        ki = crit["kis"].setdefault(m["ki"], {
            "id": m["ki"], "name": m["ki_name"], "metrics": {}, "_order": len(crit["kis"]),
        })
        ki["metrics"][m["id"]] = {
            "id": m["id"], "text": m["text"], "evidence_count": 0, "tentative_count": 0,
            # Issue 3 (faculty quick-reference): WHICH file(s) are filed against this
            # metric, not just how many. Each entry is {"filename", "year"}.
            "evidence_files": [], "tentative_files": [],
            "_order": len(ki["metrics"]),
        }

    # quick lookup: metric id -> its row dict, so decisions (which only carry a metric id
    # via the "chosen" dict) can be tallied against the tree in one pass.
    metric_rows = {}
    for crit in criteria.values():
        for ki in crit["kis"].values():
            for row in ki["metrics"].values():
                metric_rows[row["id"]] = row

    # ---- overall counters (the honesty numbers: review docs count nowhere as evidence) ----
    overall = {
        "docs_scanned": len(decisions), "committed": 0, "in_review": 0,
        "unreadable": 0, "criterion_only_commits": 0, "metric_commits": 0,
        # Issue 3: flat filename-first index across the WHOLE pack, every committed
        # decision -- {"filename","criterion","criterion_name","metric_id","year","strength"}.
        "documents_on_file": [],
    }

    for d in decisions:
        if _is_unreadable(d):
            overall["unreadable"] += 1
            continue
        if _is_review(d):
            overall["in_review"] += 1
            continue  # pending a human -- NOT counted as evidence anywhere, by design
        if not _is_committed(d):
            continue  # unrecognised/blank status -- ignore rather than guess

        overall["committed"] += 1
        chosen = _get_chosen(d)
        if not chosen:
            continue  # committed but no metric at all (shouldn't normally happen)

        row = metric_rows.get(chosen.get("id"))
        if row is None:
            continue  # metric id not in this pack (e.g. report run against a different pack)

        filename = _get_filename(d)
        year = d.get("year")

        if "commit_level" in d:
            commit_level = d.get("commit_level")
            strong = commit_level == "metric"
        else:
            # legacy decisions that never carried the field -> keep old behavior (strong)
            strong = True

        if strong:
            # engine converged on the exact metric, or a human confirmed/changed it
            # (the review handlers stamp "metric" on Accept/Save) -> strong evidence.
            row["evidence_count"] += 1
            row["evidence_files"].append({"filename": filename, "year": year})
            overall["metric_commits"] += 1
            strength = "strong"
        else:
            # "criterion" (right area, metric was a best guess) OR None-with-auto
            # (L3 blanket-trust filed an engine-unsure doc) -> tentative, never strong.
            row["tentative_count"] += 1
            row["tentative_files"].append({"filename": filename, "year": year})
            overall["criterion_only_commits"] += 1
            strength = "tentative"

        overall["documents_on_file"].append({
            "filename": filename,
            "criterion": chosen.get("criterion"),
            "criterion_name": chosen.get("criterion_name"),
            "metric_id": chosen.get("id"),
            "year": year,
            "strength": strength,
        })

    # ---- per-criterion rollup ----
    criterion_list = []
    for crit in sorted(criteria.values(), key=lambda c: c["_order"]):
        ki_list = []
        total_metrics = strong = tentative_only = missing = 0
        for ki in sorted(crit["kis"].values(), key=lambda k: k["_order"]):
            metric_list = sorted(ki["metrics"].values(), key=lambda r: r["_order"])
            for row in metric_list:
                total_metrics += 1
                if row["evidence_count"] > 0:
                    strong += 1
                elif row["tentative_count"] > 0:
                    tentative_only += 1
                else:
                    missing += 1
            ki_list.append({"id": ki["id"], "name": ki["name"], "metrics": metric_list})

        coverage_pct = round(100.0 * strong / total_metrics, 1) if total_metrics else 0.0
        criterion_list.append({
            "id": crit["id"], "name": crit["name"], "kis": ki_list,
            "total_metrics": total_metrics, "metrics_strong": strong,
            "metrics_tentative_only": tentative_only, "metrics_missing": missing,
            "coverage_pct": coverage_pct,
        })

    return {"criteria": criterion_list, "overall": overall}


def _coverage_bar(pct, width=10):
    """[######----] style bar for a plain-text report."""
    filled = round(width * pct / 100.0)
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def format_gap_report_text(report, pack_name):
    """Plain-English printable text block -- the demo artifact for a non-technical
    IQAC coordinator (IQAC = the college office that manages accreditation).
    No jargon, short sentences, simple words."""
    lines = []
    lines.append("=" * 78)
    lines.append(f"SAANDRU GAP REPORT -- {pack_name}")
    lines.append("What evidence do we have, and what is still missing?")
    lines.append("=" * 78)

    ov = report["overall"]
    lines.append("")
    lines.append(f"Documents looked at: {ov['docs_scanned']}")
    lines.append(f"  - Sorted automatically (strong match):  {ov['committed']}")
    lines.append(f"  - Still waiting for a human to check:   {ov['in_review']}  "
                  f"(these are NOT counted as evidence below -- they are pending)")
    lines.append(f"  - Could not be read at all:             {ov['unreadable']}")
    lines.append("")

    for crit in report["criteria"]:
        bar = _coverage_bar(crit["coverage_pct"])
        lines.append("-" * 78)
        lines.append(f"Criterion {crit['id']}: {crit['name']}")
        lines.append(f"  Coverage: {bar} {crit['coverage_pct']:.0f}%  "
                      f"({crit['metrics_strong']} of {crit['total_metrics']} points have strong evidence)")

        missing_rows = []
        tentative_rows = []
        for ki in crit["kis"]:
            for row in ki["metrics"]:
                if row["evidence_count"] == 0 and row["tentative_count"] == 0:
                    missing_rows.append(row)
                elif row["evidence_count"] == 0 and row["tentative_count"] > 0:
                    tentative_rows.append(row)

        # Issue 3: what IS on file, filename-first, before we tell them what's missing --
        # a faculty member reading this wants to see proof of their own filing first.
        present_strong = [(row["id"], f) for ki in crit["kis"] for row in ki["metrics"]
                           for f in row["evidence_files"]]
        present_tentative = [(row["id"], f) for ki in crit["kis"] for row in ki["metrics"]
                              for f in row["tentative_files"]]
        if present_strong or present_tentative:
            lines.append("  PRESENT -- documents on file:")
            for mid, f in present_strong:
                year_str = f" ({f['year']})" if f.get("year") else ""
                lines.append(f"    - {mid}: {_shorten(f['filename'])}{year_str}")
            for mid, f in present_tentative:
                year_str = f" ({f['year']})" if f.get("year") else ""
                lines.append(f"    - {mid}: {_shorten(f['filename'])}{year_str} (needs confirmation)")

        if missing_rows:
            lines.append("  MISSING -- no evidence found:")
            for row in missing_rows:
                lines.append(f"    - {row['id']}: {_shorten(row['text'])}")
        if tentative_rows:
            lines.append("  TENTATIVE -- needs human confirmation:")
            for row in tentative_rows:
                lines.append(f"    - {row['id']}: {_shorten(row['text'])} "
                              f"({row['tentative_count']} document(s) probably match this)")
        if not missing_rows and not tentative_rows:
            lines.append("  Every point in this criterion has at least one strong document. Good.")

    # ---- WHAT TO DO NEXT: top 10 missing metrics, weakest criteria first ----
    lines.append("-" * 78)
    lines.append("WHAT TO DO NEXT")
    lines.append("The 10 most useful documents to go collect first:")
    ranked_criteria = sorted(report["criteria"], key=lambda c: c["coverage_pct"])
    todo = []
    for crit in ranked_criteria:
        for ki in crit["kis"]:
            for row in sorted(ki["metrics"], key=lambda r: r["id"]):
                if row["evidence_count"] == 0 and row["tentative_count"] == 0:
                    todo.append(row)
    if not todo:
        lines.append("  Nothing missing -- every metric already has at least one document.")
    else:
        for row in todo[:10]:
            lines.append(f"  Start collecting: {row['id']} -- {_shorten(row['text'])}")

    # ---- DOCUMENTS FILED (quick reference): Issue 3 -- flat, filename-first index of
    # every filed document across the WHOLE pack, so a faculty member who knows the
    # filename can scan straight to it instead of hunting criterion by criterion. ----
    lines.append("-" * 78)
    lines.append("DOCUMENTS FILED (quick reference)")
    docs_on_file = sorted(
        report["overall"].get("documents_on_file", []),
        key=lambda d: (str(d.get("criterion")), str(d.get("metric_id"))),
    )
    if not docs_on_file:
        lines.append("  Nothing has been filed yet.")
    else:
        for d in docs_on_file:
            year_str = f" ({d['year']})" if d.get("year") else ""
            note = "" if d.get("strength") == "strong" else " (needs confirmation)"
            lines.append(
                f"  {_shorten(d['filename'])}  ->  Criterion {d.get('criterion')} / "
                f"{d.get('metric_id')}{year_str}{note}"
            )

    lines.append("=" * 78)
    return "\n".join(lines)


def format_summary_card_text(report, pack_name, college_name=""):
    """A compact one-page card: totals + a per-criterion coverage table + one-line verdict."""
    ov = report["overall"]
    docs = ov["docs_scanned"]

    def _pct(n):
        return round(100.0 * n / docs, 1) if docs else 0.0

    lines = []
    header = f"SAANDRU SUMMARY CARD -- {pack_name}"
    if college_name:
        header += f" -- {college_name}"
    lines.append("=" * 60)
    lines.append(header)
    lines.append("=" * 60)
    lines.append(f"Total files looked at:      {docs}")
    lines.append(f"Sorted automatically:       {ov['committed']} ({_pct(ov['committed'])}%)")
    lines.append(f"Needs a human to check:     {ov['in_review']} ({_pct(ov['in_review'])}%)")
    lines.append(f"Could not be read:          {ov['unreadable']} ({_pct(ov['unreadable'])}%)")
    lines.append("")
    lines.append(f"{'Criterion':<28}{'Strong/Total':>14}{'Coverage':>12}")
    lines.append("-" * 60)
    for crit in report["criteria"]:
        label = f"{crit['id']} {crit['name']}"[:27]
        frac = f"{crit['metrics_strong']}/{crit['total_metrics']}"
        lines.append(f"{label:<28}{frac:>14}{crit['coverage_pct']:>11.0f}%")

    if report["criteria"]:
        strongest = max(report["criteria"], key=lambda c: c["coverage_pct"])
        weakest = min(report["criteria"], key=lambda c: c["coverage_pct"])
        lines.append("")
        lines.append(f"Strongest area: C{strongest['id']} {strongest['name']} "
                      f"({strongest['coverage_pct']:.0f}%). "
                      f"Biggest gap: C{weakest['id']} {weakest['name']} "
                      f"({weakest['coverage_pct']:.0f}%).")
    lines.append("=" * 60)
    return "\n".join(lines)
