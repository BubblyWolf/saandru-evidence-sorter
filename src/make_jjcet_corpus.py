# -*- coding: utf-8 -*-
"""Build ground-truth manifest for a real Tamil Nadu engineering college's DVV
per-metric evidence PDFs (folder name kept as "jjcet_dvv" -- see benchmarks/baseline.json).
Filename = metric id = the answer key (real, current-format, TN engineering college)."""
import os, json, sys
sys.path.insert(0, r"D:\praman\src")
from ingest import read_document

FOLDER = r"D:\praman\samples\jjcet_dvv"
manifest = []
print(f"{'file':10} {'chars':>7} {'per-pg?':>8}  readable?")
print("-"*50)
for fn in sorted(os.listdir(FOLDER)):
    if not fn.endswith(".pdf"): continue
    metric = fn[:-4]                      # "5.1.1"
    crit = metric.split(".")[0]
    txt = read_document(os.path.join(FOLDER, fn))
    marker = txt.startswith("[")
    n = len(txt)
    tag = "MARKER:"+txt[:28] if marker else ("TEXT" if n>200 else "thin/scanned?")
    print(f"{fn:10} {n:>7}          {tag}")
    manifest.append({"file": fn, "true_criterion": crit, "true_ki": ".".join(metric.split(".")[:2]),
                     "true_metric": metric})
json.dump(manifest, open(os.path.join(FOLDER,"_ground_truth.json"),"w",encoding="utf-8"), indent=2)
print(f"\nmanifest: {len(manifest)} files labeled to metric level")
