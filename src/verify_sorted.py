# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Saandru -- tamper-evident check for a Saandru_Sorted folder.

organize.py writes "_manifest.json" while it copies (sha256 of every copied file, plus
where it came from). Faculty sometimes edit/move/delete files inside Saandru_Sorted by
hand afterwards -- this module compares the folder AS IT IS NOW against that manifest
and reports exactly what changed, in plain English.

Pure code, zero AI. Four kinds of discrepancy:
  - CHANGED : same relpath as the manifest, but the bytes on disk are different now.
  - MOVED   : a file with a manifest-recorded hash was found at a DIFFERENT relpath
              (renamed or dragged into another criterion folder).
  - MISSING : a manifest entry whose hash is not found anywhere in the folder anymore
              (deleted, or edited beyond recognition -- either way, gone).
  - EXTRA   : a file sitting on disk that the manifest never recorded (dropped in by
              hand). "_manifest.json" and "README.txt" are never flagged as extra --
              they are Saandru's own bookkeeping, not evidence.

Runnable standalone: `python src/verify_sorted.py <path-to-Saandru_Sorted>`
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from duplicates import file_sha256  # same sha256-file-bytes helper organize.py uses

MANIFEST_FILENAME = "_manifest.json"
_IGNORED_NAMES = {MANIFEST_FILENAME, "README.txt"}


def _walk_current_files(sorted_dir):
    """Return {relpath (forward slashes): sha256} for every real file under sorted_dir,
    skipping the manifest itself and README.txt (Saandru's own bookkeeping, not evidence)."""
    current = {}
    for root, _dirnames, filenames in os.walk(sorted_dir):
        for name in filenames:
            if name in _IGNORED_NAMES:
                continue
            full = os.path.join(root, name)
            relpath = os.path.relpath(full, sorted_dir).replace(os.sep, "/")
            current[relpath] = file_sha256(full)
    return current


def verify(sorted_dir):
    """Compare the real folder at sorted_dir against its _manifest.json.

    Returns a dict:
      {"ok": bool, "manifest_found": bool,
       "changed": [...], "moved": [...], "missing": [...], "extra": [...],
       "message": str}   # only set when manifest_found is False
    Each of changed/moved/missing/extra is a list of dicts describing one discrepancy.
    Never raises -- a missing/corrupt manifest is reported, not crashed on.
    """
    manifest_path = os.path.join(sorted_dir, MANIFEST_FILENAME)
    if not os.path.isdir(sorted_dir):
        return {
            "ok": False, "manifest_found": False,
            "changed": [], "moved": [], "missing": [], "extra": [],
            "message": f"Folder not found: {sorted_dir}",
        }
    if not os.path.isfile(manifest_path):
        return {
            "ok": False, "manifest_found": False,
            "changed": [], "moved": [], "missing": [], "extra": [],
            "message": "No _manifest.json found -- run sorting again to create a manifest.",
        }

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        return {
            "ok": False, "manifest_found": False,
            "changed": [], "moved": [], "missing": [], "extra": [],
            "message": f"_manifest.json is unreadable ({exc}) -- run sorting again to create a fresh manifest.",
        }

    manifest_entries = manifest.get("files") or []
    current = _walk_current_files(sorted_dir)
    # index current files by hash too, so MOVED/MISSING can be told apart -- a hash that
    # exists somewhere else in the folder is "moved"; a hash that's gone entirely is "missing".
    current_by_hash = {}
    for relpath, h in current.items():
        if h:
            current_by_hash.setdefault(h, []).append(relpath)

    changed, moved, missing = [], [], []
    seen_relpaths = set()  # relpaths accounted for by a manifest entry (matched or moved)

    for entry in manifest_entries:
        relpath = entry.get("relpath", "")
        expected_hash = entry.get("sha256", "")
        seen_relpaths.add(relpath)

        if relpath in current:
            actual_hash = current[relpath]
            if actual_hash and actual_hash == expected_hash:
                continue  # exactly as recorded -- nothing to report
            # a file IS still at the recorded path, but its bytes differ.
            changed.append({
                "relpath": relpath,
                "source": entry.get("source", ""),
                "metric": entry.get("metric", ""),
                "criterion": entry.get("criterion", ""),
            })
            continue

        # nothing at the recorded path anymore -- did it move, or is it gone?
        candidates = [r for r in current_by_hash.get(expected_hash, []) if r not in seen_relpaths]
        if candidates:
            now_at = candidates[0]
            seen_relpaths.add(now_at)
            moved.append({
                "was_at": relpath,
                "now_at": now_at,
                "source": entry.get("source", ""),
                "metric": entry.get("metric", ""),
                "criterion": entry.get("criterion", ""),
            })
        else:
            missing.append({
                "relpath": relpath,
                "source": entry.get("source", ""),
                "metric": entry.get("metric", ""),
                "criterion": entry.get("criterion", ""),
            })

    extra = [
        {"relpath": relpath}
        for relpath in current
        if relpath not in seen_relpaths
    ]
    extra.sort(key=lambda e: e["relpath"])
    changed.sort(key=lambda e: e["relpath"])
    moved.sort(key=lambda e: e["was_at"])
    missing.sort(key=lambda e: e["relpath"])

    ok = not (changed or moved or missing or extra)
    return {
        "ok": ok, "manifest_found": True,
        "changed": changed, "moved": moved, "missing": missing, "extra": extra,
        "message": "",
    }


def _criterion_hint(entry):
    """Small "belongs under Criterion_X" hint for the MOVED report line, built from the
    manifest's own recorded relpath (its folder segment already IS the criterion folder
    name) rather than re-deriving it -- cheaper and always in sync with organize.py."""
    was_at = entry.get("was_at", "")
    parts = was_at.split("/")
    return parts[0] if parts else ""


def format_verify_text(result):
    """Plain-English printable report from verify()'s return dict."""
    if not result.get("manifest_found"):
        return f"Could not check this folder: {result.get('message', 'unknown problem')}"

    lines = []
    if result["changed"]:
        lines.append(f"CHANGED after sorting ({len(result['changed'])}):")
        for e in result["changed"]:
            lines.append(f"  ✏ CHANGED: {e['relpath']}  (originally \"{e['source']}\")")
    if result["moved"]:
        lines.append(f"MOVED ({len(result['moved'])}):")
        for e in result["moved"]:
            lines.append(
                f"  \U0001F500 MOVED: \"{e['source']}\" is now at {e['now_at']} "
                f"-- belongs under {_criterion_hint(e)}"
            )
    if result["missing"]:
        lines.append(f"MISSING ({len(result['missing'])}):")
        for e in result["missing"]:
            lines.append(f"  \U0001F47B MISSING: {e['relpath']}  (originally \"{e['source']}\")")
    if result["extra"]:
        lines.append(f"EXTRA -- nobody sorted this ({len(result['extra'])}):")
        for e in result["extra"]:
            lines.append(f"  ➕ EXTRA (nobody sorted this): {e['relpath']}")

    total = len(result["changed"]) + len(result["moved"]) + len(result["missing"]) + len(result["extra"])
    if total == 0:
        verdict = "Folder matches the record ✔"
    else:
        verdict = f"{total} issue(s) found -- see above"

    if lines:
        return "\n".join(lines) + "\n\n" + verdict
    return verdict


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python src/verify_sorted.py <path-to-Saandru_Sorted>")
        raise SystemExit(1)
    target = sys.argv[1]
    result = verify(target)
    print(format_verify_text(result))
    if result.get("manifest_found") and not result["ok"]:
        raise SystemExit(1)
