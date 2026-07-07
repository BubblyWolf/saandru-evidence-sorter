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
from pipeline import embed_metrics, classify
from enrich import extract_academic_year, suggest_name
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
    print(f"\nClassifying {len(files)} documents...\n" + "-" * 78)
    for fn in files:
        t0 = time.time()
        text = read_document(os.path.join(FOLDER, fn))
        r = classify(text, metrics, metric_vecs, filename=fn)
        c = r["chosen"]
        crit = c["criterion"] if c else "-"
        mid = c["id"] if c else "NONE"

        year_info = extract_academic_year(text)
        year = year_info["year"]
        suggested_name = suggest_name(text, criterion_name=(c["criterion_name"] if c else ""),
                                       metric_id=mid)

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
        print(f"{fn[:34]:34} -> C{crit} {mid:8} conf={r['confidence']:.2f} {r['status']:6}{lvl_tag:5} {dt:4.1f}s{year_tag} {flag}")

    out = os.path.join(os.path.dirname(FOLDER), "evidence_index.xlsx")
    try:
        wb.save(out); print("-" * 78 + f"\nExcel written: {out}")
    except Exception as e:
        print("Excel save failed:", e)

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
