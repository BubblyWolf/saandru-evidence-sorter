"""Frozen accuracy scoreboard. Run this BEFORE and AFTER any tuning change to the
classifier (pipeline.py) or a criteria pack -- it tells you honestly whether the
change made things better or worse. Never tune blind.

Usage:
    python src/benchmark.py                 # run + print + save as new "latest"
    python src/benchmark.py --save-baseline # also promote this run to the baseline
                                             # (do this once you're happy with a result)

Each benchmark set = a labeled sample folder + the criteria pack it should be
scored against. Ground truth files list the exact filenames to score, so we
score exactly those documents (not "every file in the folder").
"""
import os
import sys
import json
import time

# Windows consoles default to cp1252 -- Tamil filenames or unicode text in markers
# must never crash a run. Replace unprintable chars instead of raising.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from pack import load_metrics
from ingest import read_document
from pipeline import embed_metrics, classify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "output")           # disposable, gitignored
BENCH_DIR = os.path.join(ROOT, "benchmarks")     # tracked in git -- the frozen scoreboard

BENCHMARK_SETS = [
    {"name": "mock", "folder": "samples/mock",
     "pack": "criteria/naac_affiliated_raf2021.yaml"},
    {"name": "real_chunks", "folder": "samples/real_chunks",
     "pack": "criteria/naac_affiliated_raf2021.yaml"},
    {"name": "jjcet_dvv", "folder": "samples/jjcet_dvv",
     "pack": "criteria/naac_autonomous_raf.yaml"},
]


def _score_set(name, folder, pack_path):
    folder = os.path.join(ROOT, folder)
    pack_path = os.path.join(ROOT, pack_path)
    gt_path = os.path.join(folder, "_ground_truth.json")
    if not os.path.exists(gt_path):
        return {"name": name, "error": f"no _ground_truth.json in {folder}"}
    gt = {d["file"]: d for d in json.load(open(gt_path, encoding="utf-8"))}

    metrics, pack_name = load_metrics(pack_path)
    metric_vecs = embed_metrics(metrics, pack_name, log=lambda *a: None)

    labeled = 0          # docs with a known true_criterion (excludes "?" ambiguous docs)
    metric_labeled = 0   # subset of labeled docs that also have a known true_metric
    correct_crit = 0
    correct_metric = 0
    auto_count = 0
    auto_correct_crit = 0
    review_count = 0
    metric_commit_count = 0
    metric_commit_correct = 0
    crit_commit_count = 0
    crit_commit_correct = 0
    total_time = 0.0
    per_doc = []

    for fn, row in gt.items():
        path = os.path.join(folder, fn)
        if not os.path.exists(path):
            per_doc.append({"file": fn, "error": "file listed in ground truth but missing on disk"})
            continue
        t0 = time.time()
        text = read_document(path)
        # pack_name deliberately NOT passed: with it, classify() would consult the
        # corrections memory, and a machine with saved human corrections for these very
        # files would score a taught engine against the frozen baseline -- silent grade
        # inflation. The benchmark must always measure the UN-taught engine.
        r = classify(text, metrics, metric_vecs, filename=fn)
        dt = time.time() - t0
        total_time += dt

        c = r["chosen"]
        crit = c["criterion"] if c else "-"
        mid = c["id"] if c else "NONE"
        commit_level = r.get("commit_level")

        is_labeled = row["true_criterion"] != "?"
        crit_ok = is_labeled and crit == row["true_criterion"]
        has_metric_gt = is_labeled and row.get("true_metric", "?") != "?"
        metric_ok = has_metric_gt and mid == row["true_metric"]

        if is_labeled:
            labeled += 1
            if crit_ok:
                correct_crit += 1
            if has_metric_gt:
                metric_labeled += 1
                if metric_ok:
                    correct_metric += 1

        if r["status"] == "auto":
            auto_count += 1
            if is_labeled and crit_ok:
                auto_correct_crit += 1
            if commit_level == "metric":
                metric_commit_count += 1
                if is_labeled and crit_ok:
                    metric_commit_correct += 1
            elif commit_level == "criterion":
                crit_commit_count += 1
                if is_labeled and crit_ok:
                    crit_commit_correct += 1
        else:
            review_count += 1

        per_doc.append({
            "file": fn, "predicted_criterion": crit, "predicted_metric": mid,
            "true_criterion": row["true_criterion"], "true_metric": row.get("true_metric", "?"),
            "status": r["status"], "commit_level": commit_level,
            "confidence": r["confidence"], "seconds": round(dt, 1),
        })

    n = len(gt)
    return {
        "name": name, "pack": pack_name, "total_docs": n, "labeled_docs": labeled,
        "metric_labeled_docs": metric_labeled,
        "criterion_accuracy": round(100 * correct_crit / labeled, 1) if labeled else None,
        "metric_accuracy": round(100 * correct_metric / metric_labeled, 1) if metric_labeled else None,
        "committed": auto_count, "abstained": review_count,
        "commit_rate_pct": round(100 * auto_count / n, 1) if n else None,
        "committed_correct_on_criterion_pct":
            round(100 * auto_correct_crit / auto_count, 1) if auto_count else None,
        "metric_level_commits": metric_commit_count,
        "metric_level_commit_correct": metric_commit_correct,
        "criterion_only_commits": crit_commit_count,
        "criterion_only_commit_correct": crit_commit_correct,
        "avg_seconds_per_doc": round(total_time / n, 1) if n else None,
        "per_doc": per_doc,
    }


def _print_table(results):
    print("\n" + "=" * 96)
    print(f"{'Set':14}{'Docs':>6}{'Crit-acc':>10}{'Metric-acc':>12}{'Committed':>11}"
          f"{'Commit-ok%':>12}{'Abstain':>9}{'s/doc':>8}")
    print("-" * 96)
    for r in results:
        if r.get("error"):
            print(f"{r['name']:14}  ERROR: {r['error']}")
            continue
        print(f"{r['name']:14}{r['total_docs']:>6}"
              f"{(str(r['criterion_accuracy']) + '%') if r['criterion_accuracy'] is not None else '-':>10}"
              f"{(str(r['metric_accuracy']) + '%') if r['metric_accuracy'] is not None else '-':>12}"
              f"{r['committed']:>11}"
              f"{(str(r['committed_correct_on_criterion_pct']) + '%') if r['committed_correct_on_criterion_pct'] is not None else '-':>12}"
              f"{r['abstained']:>9}{r['avg_seconds_per_doc']:>7}s")
    print("=" * 96)


def _print_diff(baseline_results, new_results):
    base_by_name = {r["name"]: r for r in baseline_results if not r.get("error")}
    print("\n--- Compared to saved baseline ---")
    for r in new_results:
        if r.get("error") or r["name"] not in base_by_name:
            continue
        b = base_by_name[r["name"]]
        for field, label in [("criterion_accuracy", "criterion-acc"),
                              ("metric_accuracy", "metric-acc"),
                              ("avg_seconds_per_doc", "s/doc")]:
            old, new = b.get(field), r.get(field)
            if old is None or new is None:
                continue
            delta = round(new - old, 1)
            arrow = "no change" if delta == 0 else ("UP" if delta > 0 else "DOWN")
            if field == "avg_seconds_per_doc" and delta != 0:
                # for speed, higher is WORSE -- don't let it read like an improvement
                arrow = "SLOWER" if delta > 0 else "FASTER"
            print(f"  {r['name']:14} {label:15} {old} -> {new}  ({arrow}{'' if delta==0 else f' {abs(delta)}'})")


def main():
    save_baseline = "--save-baseline" in sys.argv
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(BENCH_DIR, exist_ok=True)

    results = [_score_set(s["name"], s["folder"], s["pack"]) for s in BENCHMARK_SETS]
    _print_table(results)

    baseline_path = os.path.join(BENCH_DIR, "baseline.json")
    latest_path = os.path.join(OUT_DIR, "benchmark_latest.json")

    if os.path.exists(baseline_path):
        baseline = json.load(open(baseline_path, encoding="utf-8"))
        _print_diff(baseline.get("results", []), results)
    else:
        print("\n(No saved baseline yet. Run with --save-baseline once you're happy with this result.)")

    payload = {"results": results}
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nSaved: {latest_path}")

    if save_baseline:
        with open(baseline_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Saved: {baseline_path}  <- new baseline, future runs compare against this")


if __name__ == "__main__":
    main()
