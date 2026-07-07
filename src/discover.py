"""Recursive file discovery for a source folder.

Real college folders are messy: nested subfolders (Criterion 3/MoUs/...), stray
OS junk files (Thumbs.db, desktop.ini, ~$temp lock files), and Praman's own
output folder sitting inside the source folder from a previous run. This module
is the SINGLE place that decides "is this a real candidate document" so run.py,
app.py, and any future entry point all see the same messy-real-world folder the
same way.

Golden rule (same as ingest.py): never silently skip a real document. We only
filter out things that are provably OS/tool junk, never by file extension --
unknown extensions must still flow through so ingest.read_document() can mark
them "[UNSUPPORTED FORMAT: ...]" and land in the human-review bucket.
"""
import os

from ingest import SUPPORTED_EXTS  # re-exported for callers that want it from here

# Exact (case-insensitive) junk filenames to always skip.
_JUNK_NAMES = {"thumbs.db", "desktop.ini", "_ground_truth.json", "_expected.json"}

# Junk filename suffixes to always skip.
_JUNK_SUFFIXES = (".tmp", ".lnk", ".ini", ".db")

# Praman's own output folder -- if the tool is re-run on a folder it already
# organized, its own copies must never be re-ingested as "new" source documents.
_OWN_OUTPUT_DIRNAME = "Praman_Sorted"


def _is_junk_name(name):
    """True if `name` (a bare filename, no path) is OS/tool junk we always skip."""
    if name.startswith("~$") or name.startswith("."):
        return True
    lower = name.lower()
    if lower in _JUNK_NAMES:
        return True
    if lower.endswith(_JUNK_SUFFIXES):
        return True
    return False


def discover_files(folder):
    """Recursively walk `folder` and return a sorted list of file paths RELATIVE
    to `folder` (e.g. "Criterion 3/MoUs/mou_2023.pdf"), using OS-native separators
    so os.path.join(folder, relpath) always resolves correctly downstream.

    Skips: dotfiles/lock files (~$...), known OS junk (Thumbs.db, desktop.ini),
    Praman's own ground-truth/expected fixtures, .tmp/.lnk/.ini/.db files, and
    anything nested inside a "Praman_Sorted" folder (the tool's own output from
    a prior run). Never filters by extension -- that decision belongs to ingest.py
    so unsupported types still get a clear marker instead of vanishing silently.
    """
    if not folder or not os.path.isdir(folder):
        return []

    results = []
    for root, dirnames, filenames in os.walk(folder):
        # Prune Praman_Sorted (and any hidden "." dirs) BEFORE os.walk descends
        # into them -- cheaper than filtering afterwards, and guarantees we never
        # re-ingest our own prior output.
        dirnames[:] = [
            d for d in dirnames
            if d != _OWN_OUTPUT_DIRNAME and not d.startswith(".")
        ]

        for name in filenames:
            if _is_junk_name(name):
                continue
            full_path = os.path.join(root, name)
            rel_path = os.path.relpath(full_path, folder)
            results.append(rel_path)

    return sorted(results)
