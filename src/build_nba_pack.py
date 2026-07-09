# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Parse the NBA SAR Format (UG Engineering, Tier-II, GAPC V4.0, Jan 2025) into a criteria YAML pack.
Source: reference/NBA_SAR_UG_TierII_2025_Format.pdf, extracted to text.
"""
import re, sys

SRC, OUT = sys.argv[1], sys.argv[2]
text = open(SRC, encoding="utf-8").read()

lines = []
for ln in text.splitlines():
    if re.match(r"^===== PAGE \d+ =====$", ln): continue
    if ln.strip() == "NATIONAL BOARD OF ACCREDITATION": continue
    if re.match(r"^\d{1,3}$", ln.strip()): continue
    lines.append(ln.rstrip())

crit_re = re.compile(r"^Criterion\s+(\d{1,2})\s*:\s*(.+?)\s*\((\d{2,4})\)\s*$")
item_re = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,2})\.?\s+(.+?)\s*\((\d{1,3})\)\s*$")
table_re = re.compile(r"^Table No\.", re.I)

criteria = {}     # cid -> {"name":..., "points":..., "subs": {sid: {...}}}
current_item = None
in_guidance = False

for ln in lines:
    s = ln.strip()
    cm = crit_re.match(s)
    if cm:
        cid, name, pts = cm.group(1), cm.group(2).strip(), int(cm.group(3))
        criteria[cid] = {"name": name, "points": pts, "items": {}}
        current_item = None
        continue
    im = item_re.match(s)
    if im and criteria:
        iid, name, pts = im.group(1), im.group(2).strip(), int(im.group(3))
        cid = iid.split(".")[0]
        if cid in criteria:
            criteria[cid]["items"][iid] = {"name": name, "points": pts, "guidance": ""}
            current_item = (cid, iid)
            in_guidance = False
        continue
    if table_re.match(s):
        current_item = None
        continue
    if current_item and s:
        cid, iid = current_item
        g = criteria[cid]["items"][iid]["guidance"]
        if s.startswith("(") or in_guidance:
            in_guidance = not s.endswith(")")
            if len(g) < 380:
                criteria[cid]["items"][iid]["guidance"] = (g + " " + s.strip("()")).strip()
        else:
            current_item = None

def esc(x):
    return re.sub(r"\s+", " ", x).strip().replace('"', "'")

out = []
out.append("# NBA criteria pack - UG Engineering Programs, TIER-II, SAR Format GAPC V4.0 (January 2025)")
out.append("# Source: official SAR format (reference/NBA_SAR_UG_TierII_2025_Format.pdf). Auto-parsed; guidance trimmed.")
out.append("pack: nba_ug_engg_tier2_gapc_v4")
out.append("framework: NBA Graduate Attributes and Professional Competencies V4.0 (Tier-II)")
out.append("institution_type: UG Engineering Programs (Tier-II institutions)")
out.append("total_points: 1000")
out.append("criteria:")
n_items = 0
for cid in sorted(criteria, key=int):
    c = criteria[cid]
    out.append(f'  - id: "{cid}"')
    out.append(f'    name: "{esc(c["name"])}"')
    out.append(f'    points: {c["points"]}')
    out.append(f"    items:")
    for iid in sorted(c["items"], key=lambda k: [int(x) for x in k.split(".")]):
        it = c["items"][iid]
        n_items += 1
        out.append(f'      - id: "{iid}"')
        out.append(f'        name: "{esc(it["name"])[:200]}"')
        out.append(f'        points: {it["points"]}')
        if it["guidance"]:
            out.append(f'        guidance: "{esc(it["guidance"])[:380]}"')
        out.append(f"        evidence_hints: []   # filled in Phase 0.5")
open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"criteria: {len(criteria)} | items: {n_items}")
for cid in sorted(criteria, key=int):
    print(f'  C{cid} {criteria[cid]["name"]} ({criteria[cid]["points"]}) -> {len(criteria[cid]["items"])} items')
print("wrote", OUT)
