"""Praman runner: point at a folder of documents -> sort each to a NAAC/NBA metric -> Excel index.
Usage: python src/run.py <folder> [pack_yaml]
If the folder has _ground_truth.json (mock set), prints accuracy too.
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(__file__))
from pack import load_metrics
from ingest import read_document
from pipeline import embed_metrics, classify
from openpyxl import Workbook

FOLDER = sys.argv[1] if len(sys.argv) > 1 else r"D:\praman\samples\mock"
PACK = sys.argv[2] if len(sys.argv) > 2 else r"D:\praman\criteria\naac_affiliated_raf2021.yaml"


def main():
    metrics, pack_name = load_metrics(PACK)
    print(f"Pack: {pack_name} ({len(metrics)} metrics)")
    metric_vecs = embed_metrics(metrics, pack_name)

    files = [f for f in sorted(os.listdir(FOLDER))
             if os.path.splitext(f)[1].lower() in (".txt", ".docx", ".pdf")]
    gt = {}
    gt_path = os.path.join(FOLDER, "_ground_truth.json")
    if os.path.exists(gt_path):
        gt = {d["file"]: d for d in json.load(open(gt_path, encoding="utf-8"))}

    wb = Workbook(); ws = wb.active; ws.title = "Evidence Index"
    ws.append(["File", "Criterion", "KI", "Metric", "Metric text", "Confidence",
               "Status", "Evidence quote", "Top-3 candidates"])

    rows, correct_crit, correct_metric, scored = [], 0, 0, 0
    auto_count, auto_correct_crit, review_count = 0, 0, 0
    print(f"\nClassifying {len(files)} documents...\n" + "-" * 78)
    for fn in files:
        t0 = time.time()
        text = read_document(os.path.join(FOLDER, fn))
        r = classify(text, metrics, metric_vecs, filename=fn)
        c = r["chosen"]
        crit = c["criterion"] if c else "-"
        mid = c["id"] if c else "NONE"
        ws.append([fn, crit, c["ki"] if c else "-", mid, c["text"][:90] if c else "-",
                   r["confidence"], r["status"], r["evidence"][:120],
                   ", ".join(r["candidates"])])
        dt = time.time() - t0
        if r["status"] == "auto":
            auto_count += 1
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
                if mid == tm: correct_metric += 1; flag += "M"
                flag = f"[crit={'OK' if crit==tc else 'X'} metric={'OK' if mid==tm else 'X'}]"
            else:
                flag = "[ambiguous -> " + ("review OK" if r["status"] == "review" else "MISSED") + "]"
        print(f"{fn[:34]:34} -> C{crit} {mid:8} conf={r['confidence']:.2f} {r['status']:6} {dt:4.1f}s {flag}")

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
    committed_pct = (100 * auto_correct_crit // auto_count) if auto_count else 0
    print(f"COMMITTED (status=auto): {auto_count}/{len(files)} docs, "
          f"{auto_correct_crit}/{auto_count if auto_count else 1} correct on criterion "
          f"({committed_pct}%)  |  ABSTAINED (status=review): {review_count}/{len(files)}")


if __name__ == "__main__":
    main()
