"""Praman runner: point at a folder of documents -> sort each to a NAAC/NBA metric -> Excel index.
Usage: python src/run.py <folder> [pack_yaml]
If the folder has _ground_truth.json (mock set), prints accuracy too.
"""
import os, sys, json, time

# Windows consoles default to cp1252 -- Tamil filenames or unicode text in markers
# must never crash a run. Replace unprintable chars instead of raising.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(__file__))
from pack import load_metrics
from ingest import read_document
from discover import discover_files
from pipeline import embed_metrics, classify, _pack_hash
from enrich import extract_academic_year, suggest_name
from ollama_client import CHAT_MODEL
import doc_cache
from gap_report import build_gap_report, format_gap_report_text, format_summary_card_text
from duplicates import find_duplicates, file_sha256
from openpyxl import Workbook

FOLDER = sys.argv[1] if len(sys.argv) > 1 else r"D:\praman\samples\mock"
PACK = sys.argv[2] if len(sys.argv) > 2 else r"D:\praman\criteria\naac_affiliated_raf2021.yaml"


def main():
    metrics, pack_name = load_metrics(PACK)
    print(f"Pack: {pack_name} ({len(metrics)} metrics)")
    metric_vecs = embed_metrics(metrics, pack_name)

    # discover_files() walks subfolders too and never filters by extension --
    # unsupported/unreadable files still show up here so they land in the
    # human-review bucket via read_document()'s bracketed markers, instead of
    # silently vanishing the way a naive os.listdir(FOLDER) extension filter would.
    files = discover_files(FOLDER)
    gt = {}
    gt_path = os.path.join(FOLDER, "_ground_truth.json")
    if os.path.exists(gt_path):
        gt = {d["file"]: d for d in json.load(open(gt_path, encoding="utf-8"))}

    wb = Workbook(); ws = wb.active; ws.title = "Evidence Index"
    ws.append(["File", "Criterion", "KI", "Metric", "Metric text", "Confidence",
               "Status", "Evidence quote", "Top-3 candidates", "Year", "Suggested name"])

    rows, correct_crit, correct_metric, scored = [], 0, 0, 0
    auto_count, auto_correct_crit, review_count = 0, 0, 0
    metric_commit_count, metric_commit_correct = 0, 0
    crit_commit_count, crit_commit_correct = 0, 0
    decisions = []  # feeds gap_report.build_gap_report() once the loop is done
    dup_items = []  # feeds duplicates.find_duplicates() once the loop is done (Feature B)
    pack_key = _pack_hash(metrics)
    print(f"\nClassifying {len(files)} documents...\n" + "-" * 78)
    for fn in files:
        t0 = time.time()
        full_path = os.path.join(FOLDER, fn)

        # Task 4: cache key = sha256(file bytes + pack hash + model name) -- an unchanged file
        # against the same pack/model always hits, an edited file (even same extracted text)
        # always misses. Cache stores the classify result + year + title together.
        cache_key = doc_cache.make_key(full_path, pack_key, CHAT_MODEL)
        cached = doc_cache.get(cache_key)
        cached_tag = ""
        if cached:
            r = cached["result"]
            year = cached["year"]
            suggested_name = cached["title"]
            # older cache entries (written before the gap report existed) won't have this
            # key -- default to "readable" rather than guessing wrong in either direction.
            unreadable = cached.get("unreadable", False)
            cached_tag = " [cached]"
        else:
            text = read_document(full_path)
            # gap_report needs to know "could not read" separately from "low-confidence
            # match" -- read_document() marks that with one of its bracketed prefixes
            # (see ingest.py's _KNOWN_MARKER_PREFIXES); classify() still runs on the
            # marker text below so run.py's existing accuracy bookkeeping is unchanged.
            unreadable = text.startswith(
                ("[UNSUPPORTED FORMAT:", "[NEEDS OCR:", "[UNREADABLE:", "[EMPTY DOCUMENT:"))
            r = classify(text, metrics, metric_vecs, filename=fn, pack_name=pack_name)
            year_info = extract_academic_year(text)
            year = year_info["year"]
            # Task 1: prefer the title piggybacked on the adjudication call (free) -- only fall
            # back to enrich.suggest_name()'s own LLM call when the adjudication title is empty
            # (e.g. the doc abstained before any vote ran, or the model returned garbage).
            c0 = r["chosen"]
            suggested_name = r.get("title") or suggest_name(
                text, criterion_name=(c0["criterion_name"] if c0 else ""),
                metric_id=(c0["id"] if c0 else "NONE"))
            doc_cache.put(cache_key, {
                "result": r, "year": year, "title": suggested_name, "unreadable": unreadable,
            })

        decisions.append({
            "filename": fn, "status": r["status"], "commit_level": r.get("commit_level"),
            "chosen": r["chosen"], "unreadable": unreadable,
        })
        # Feature B (duplicate finder): sha256 always (cheap, works on any file including
        # unreadable ones), but doc_vec only for docs that were actually classified -- an
        # unreadable file's "text" is one of read_document()'s bracketed markers, and embedding
        # THAT would make every unreadable file look "near"-identical to every other one.
        dup_items.append({
            "filename": fn,
            "sha256": file_sha256(full_path),
            "doc_vec": None if unreadable else r.get("doc_vec"),
        })

        c = r["chosen"]
        crit = c["criterion"] if c else "-"
        mid = c["id"] if c else "NONE"

        ws.append([fn, crit, c["ki"] if c else "-", mid, c["text"][:90] if c else "-",
                   r["confidence"], r["status"], r["evidence"][:120],
                   ", ".join(r["candidates"]), year or "", suggested_name])
        dt = time.time() - t0
        commit_level = r.get("commit_level")
        if r["status"] == "auto":
            auto_count += 1
            if commit_level == "metric":
                metric_commit_count += 1
            elif commit_level == "criterion":
                crit_commit_count += 1
        else:
            review_count += 1
        flag = ""
        if fn in gt:
            scored += 1
            tc, tm = gt[fn]["true_criterion"], gt[fn]["true_metric"]
            if tc != "?":
                if crit == tc:
                    correct_crit += 1; flag += "C"
                    if r["status"] == "auto":
                        auto_correct_crit += 1
                        if commit_level == "metric":
                            metric_commit_correct += 1
                        elif commit_level == "criterion":
                            crit_commit_correct += 1
                if mid == tm: correct_metric += 1; flag += "M"
                flag = f"[crit={'OK' if crit==tc else 'X'} metric={'OK' if mid==tm else 'X'}]"
            else:
                flag = "[ambiguous -> " + ("review OK" if r["status"] == "review" else "MISSED") + "]"
        lvl_tag = {"metric": "", "criterion": "~crit"}.get(commit_level, "")
        year_tag = f" yr={year}" if year else ""
        print(f"{fn[:34]:34} -> C{crit} {mid:8} conf={r['confidence']:.2f} {r['status']:6}{lvl_tag:5} {dt:4.1f}s{cached_tag}{year_tag} {flag}")

    # ---- Gap report: deterministic, no LLM -- just counting what the loop above already
    # decided against what the pack says SHOULD exist. ----
    gap_report = build_gap_report(decisions, metrics)
    summary_text = format_summary_card_text(gap_report, pack_name)
    gap_text = format_gap_report_text(gap_report, pack_name)
    print("\n" + summary_text)
    print("\n" + gap_text)

    # ---- Duplicate finder (Feature B): deterministic, embeddings + sha256 only -- no LLM. ----
    dup_groups = find_duplicates(dup_items)
    if dup_groups:
        print("\nPOSSIBLE DUPLICATES")
        print("These files look like copies -- keep one, remove the rest:")
        for i, g in enumerate(dup_groups, start=1):
            print(f"  Group {i} ({g['kind']}):")
            for f in g["files"]:
                print(f"    - {f}")

    dup_ws = wb.create_sheet("Duplicates")
    dup_ws.append(["Group #", "Kind", "File"])
    for i, g in enumerate(dup_groups, start=1):
        for f in g["files"]:
            dup_ws.append([i, g["kind"], f])

    gap_ws = wb.create_sheet("Gap Report")
    gap_ws.append(["Criterion", "KI", "Metric", "Metric text", "Strong evidence",
                   "Tentative", "Status"])
    for crit in gap_report["criteria"]:
        for ki in crit["kis"]:
            for row in ki["metrics"]:
                if row["evidence_count"] > 0:
                    status = "OK"
                elif row["tentative_count"] > 0:
                    status = "TENTATIVE"
                else:
                    status = "MISSING"
                gap_ws.append([
                    f"{crit['id']} {crit['name']}", f"{ki['id']} {ki['name']}",
                    row["id"], row["text"][:90], row["evidence_count"], row["tentative_count"],
                    status,
                ])

    ov = gap_report["overall"]
    sum_ws = wb.create_sheet("Summary")
    sum_ws.append(["Metric", "Value"])
    sum_ws.append(["Pack", pack_name])
    sum_ws.append(["Docs scanned", ov["docs_scanned"]])
    sum_ws.append(["Sorted automatically", ov["committed"]])
    sum_ws.append(["Needs human review", ov["in_review"]])
    sum_ws.append(["Could not read", ov["unreadable"]])
    sum_ws.append(["Criterion-only commits (tentative)", ov["criterion_only_commits"]])
    sum_ws.append([])
    sum_ws.append(["Criterion", "Strong/Total", "Coverage %"])
    for crit in gap_report["criteria"]:
        sum_ws.append([f"{crit['id']} {crit['name']}",
                        f"{crit['metrics_strong']}/{crit['total_metrics']}", crit["coverage_pct"]])

    out = os.path.join(os.path.dirname(FOLDER), "evidence_index.xlsx")
    try:
        wb.save(out); print("-" * 78 + f"\nExcel written: {out}")
    except Exception as e:
        print("Excel save failed:", e)

    gap_txt_path = os.path.join(os.path.dirname(FOLDER), "gap_report.txt")
    try:
        with open(gap_txt_path, "w", encoding="utf-8") as f:
            f.write(summary_text + "\n\n" + gap_text)
        print(f"Gap report written: {gap_txt_path}")
    except Exception as e:
        print("Gap report save failed:", e)

    if scored:
        labeled = sum(1 for f in files if f in gt and gt[f]["true_criterion"] != "?")
        print(f"\nACCURACY on {labeled} labeled docs:  "
              f"criterion {correct_crit}/{labeled} ({100*correct_crit//max(labeled,1)}%)  |  "
              f"exact metric {correct_metric}/{labeled} ({100*correct_metric//max(labeled,1)}%)")

    # honest headline: of the documents the tool actually COMMITTED to (status=auto), how many
    # were right, versus how many it abstained on (status=review, i.e. handed to a human).
    # Split into metric-level commits (exact metric agreed by both runs) and criterion-only
    # commits (the new fallback: right area, exact metric was a best guess) -- these carry
    # different confidence and should not be silently merged into one number.
    print(f"COMMITTED (status=auto): {auto_count}/{len(files)} docs  |  "
          f"ABSTAINED (status=review): {review_count}/{len(files)}")
    print(f"COMMITTED metric: {metric_commit_count} | COMMITTED criterion-only: {crit_commit_count} "
          f"| ABSTAINED: {review_count}")
    if scored:
        # correctness lines only make sense when a ground-truth file graded something --
        # printing "0/10 correct" on an unlabeled folder reads like total failure when
        # in truth nothing was graded at all (torture-run lesson).
        committed_pct = (100 * auto_correct_crit // auto_count) if auto_count else 0
        print(f"committed-correct on criterion: {auto_correct_crit}/{auto_count if auto_count else 1} "
              f"({committed_pct}%) "
              f"(metric-level {metric_commit_correct}/{metric_commit_count if metric_commit_count else 1}, "
              f"criterion-only {crit_commit_correct}/{crit_commit_count if crit_commit_count else 1})")


if __name__ == "__main__":
    main()
