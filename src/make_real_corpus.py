# -*- coding: utf-8 -*-
# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Slice REAL evidence chunks from a real college SSR PDF -> labeled test docs.
The SSR section number (e.g. 3.2.1) gives the ground-truth CRITERION (first digit).
We strip the leading number so the classifier can't cheat; it must judge by content.
NOTE: SSR is old narrative format; metric ids won't match RAF pack, but CRITERION (1-7) does.
"""
import pdfplumber, re, os, json, random, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Pass the SSR PDF path as the first argument; defaults to samples/real/ssr.pdf.
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(_ROOT, "samples", "real", "ssr.pdf")
OUT = os.path.join(_ROOT, "samples", "real_chunks")
os.makedirs(OUT, exist_ok=True)

full = []
with pdfplumber.open(SRC) as pdf:
    for p in pdf.pages:
        full.append(p.extract_text() or "")
text = "\n".join(full)

# split into metric-response blocks: "X.Y.Z <heading> ... " until next such marker
pat = re.compile(r"(?m)^\s*([1-7]\.\d{1,2}\.\d{1,2})\s+([A-Z].{10,})")
marks = list(pat.finditer(text))
blocks = []
for i, m in enumerate(marks):
    start = m.end()
    end = marks[i+1].start() if i+1 < len(marks) else len(text)
    body = text[start:end].strip()
    body = re.sub(r"\s+", " ", body)
    crit = m.group(1).split(".")[0]
    if 350 <= len(body) <= 2600 and "……" not in body:
        blocks.append((m.group(1), crit, body[:2200]))

# pick ~2 per criterion, spread across 1-7, deterministic
by_crit = {}
for mid, crit, body in blocks:
    by_crit.setdefault(crit, []).append((mid, body))
random.seed(7)
manifest, n = [], 0
for crit in sorted(by_crit):
    chosen = by_crit[crit][:60]
    random.shuffle(chosen)
    for mid, body in chosen[:2]:
        n += 1
        fn = f"real_{crit}_{mid.replace('.','-')}.txt"
        with open(os.path.join(OUT, fn), "w", encoding="utf-8") as f:
            f.write(body)   # number stripped; pure content
        manifest.append({"file": fn, "true_criterion": crit, "true_ki": "?",
                         "true_metric": "?", "ssr_ref": mid})
json.dump(manifest, open(os.path.join(OUT, "_ground_truth.json"), "w", encoding="utf-8"), indent=2)
avg = sum(len(open(os.path.join(OUT, d["file"]), encoding="utf-8").read()) for d in manifest)//max(len(manifest),1)
print(f"wrote {n} REAL evidence chunks from a real college SSR to {OUT}")
print(f"  criteria covered: {sorted(by_crit)}  | avg length: {avg} chars (vs mock ~250)")
