# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Per-document result cache so re-running the same folder is near-instant.

Cache key = sha256(file BYTES + pack hash + model name). Hashing the raw bytes (not the
extracted text) means an edited file re-classifies automatically, while an unchanged file
on disk always hits -- exactly the same guarantee _pack_hash() gives for the metric pack.

Storage is one JSON file (output/_doc_cache.json). Corrupt/missing file -> start empty,
never crash (same never-crash rule as the rest of ingest/pipeline).
"""
import hashlib
import json
import os

CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "_doc_cache.json")
MAX_ENTRIES = 5000


def _load():
    if not os.path.exists(CACHE_PATH):
        return {}
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        # corrupt file (partial write, bad encoding, etc) -- start empty rather than crash
        return {}


def _save(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass  # a failed cache write must never break a classification run


def make_key(file_path, pack_hash, model_name):
    """sha256 of (file bytes + pack hash + model name). Read in binary so an edited file
    (even if the extracted text happens to come out the same) always gets a new key."""
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            h.update(f.read())
    except Exception:
        # unreadable file -- fall back to the path string so we still get a stable (if
        # useless) key instead of crashing; read_document() will hit the same error anyway
        # and produce its own [UNREADABLE:...] marker.
        h.update(file_path.encode("utf-8", errors="replace"))
    h.update(b"\x00")
    h.update(str(pack_hash).encode("utf-8"))
    h.update(b"\x00")
    h.update(str(model_name).encode("utf-8"))
    h.update(b"\x00")
    # classifier-logic version: a smarter pipeline must not serve answers cached by an
    # older, dumber one (file/pack/model alone can't detect a code change).
    from pipeline import PIPELINE_VERSION
    h.update(PIPELINE_VERSION.encode("utf-8"))
    return h.hexdigest()


def get(key):
    cache = _load()
    return cache.get(key)


def put(key, value):
    cache = _load()
    cache[key] = value
    if len(cache) > MAX_ENTRIES:
        # insertion order is preserved in Python 3.7+ dicts -- drop the oldest entries
        # (simple FIFO eviction is fine here, no need for real LRU bookkeeping)
        overflow = len(cache) - MAX_ENTRIES
        for k in list(cache.keys())[:overflow]:
            del cache[k]
    _save(cache)


if __name__ == "__main__":
    print("doc_cache self-test:", "empty on missing file ->", get("nonexistent-key"))
