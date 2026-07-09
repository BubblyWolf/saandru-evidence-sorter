# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Load a criteria YAML pack into a flat list of metrics the tool can match against."""
import yaml


def load_metrics(pack_path):
    """Return list of dicts: {id, criterion, criterion_name, ki, ki_name, text, search_text}."""
    with open(pack_path, encoding="utf-8") as f:
        pack = yaml.safe_load(f)

    metrics = []
    for crit in pack.get("criteria", []):
        cid, cname = str(crit["id"]), crit.get("name", "")
        # NAAC style: key_indicators -> metrics ; NBA style: items (flat)
        if "key_indicators" in crit:
            for ki in crit["key_indicators"]:
                kid, kname = str(ki["id"]), ki.get("name", "")
                for m in ki.get("metrics", []):
                    _add(metrics, m, cid, cname, kid, kname)
        else:
            for m in crit.get("items", []):
                mid = str(m["id"])
                kid = ".".join(mid.split(".")[:2])
                _add(metrics, m, cid, cname, kid, m.get("name", ""))
    return metrics, pack.get("pack", "unknown")


def _add(metrics, m, cid, cname, kid, kname):
    mid = str(m["id"])
    text = m.get("text") or m.get("name") or ""
    text = " ".join(text.split())[:300]
    metrics.append({
        "id": mid, "criterion": cid, "criterion_name": cname,
        "ki": kid, "ki_name": kname, "text": text,
        "search_text": f"{cname} > {kname}: {text}",
    })
