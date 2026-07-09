# -*- coding: utf-8 -*-
# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Build a ground-truth manifest for a real college's DVV per-metric evidence PDFs.
Folder name is "real_dvv" -- see benchmarks/baseline.json.
Filename = metric id = the answer key (real, current-format evidence)."""
import os, json, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
from ingest import read_document

FOLDER = os.path.join(_ROOT, "samples", "real_dvv")
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
