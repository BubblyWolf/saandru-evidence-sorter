"""Corrections memory -- Feature A.

When a human corrects or confirms a metric in the review UI (app.py's Accept / Save choice
buttons), remember the DOCUMENT'S EMBEDDING alongside the metric that was right. When a future
document arrives whose embedding is close to one already corrected, pipeline.classify() uses this
memory (see LEARNED_AUTO_THRESHOLD / LEARNED_HINT_THRESHOLD there) instead of -- or in addition to
-- the usual shortlist+vote. Embeddings and plain code ONLY: this module never calls the chat
model, and doesn't even call the embedder itself (callers pass the vector in).

Storage: one JSON file (output/_corrections.json), a flat list of entries across all packs
(lookup() filters by pack_name at read time). One flat list keeps the on-disk format trivial to
open and read by hand, and the caps below happen per-pack even though storage is not partitioned.

Never-crash rule (matches doc_cache.py / ingest.py): a missing or corrupt file means "no memory
yet", not an error -- a human clicking Accept must never be able to crash the review screen.
"""
import datetime as _dt
import json
import math
import os

STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "_corrections.json")
MAX_PER_PACK = 500  # FIFO cap -- oldest correction for that pack drops first once exceeded


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


def _load():
    if not os.path.exists(STORE_PATH):
        return []
    try:
        with open(STORE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        # entries must be dicts with a usable vector -- silently drop anything else instead of
        # raising later inside lookup()'s cosine math (belt-and-braces against hand-edited files).
        return [e for e in data if isinstance(e, dict) and isinstance(e.get("vec"), list)]
    except Exception:
        # corrupt file (partial write, bad encoding, hand edit gone wrong, etc) -- start empty
        # rather than crash. The next successful record() call will rewrite it cleanly.
        return []


def _save(entries):
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    try:
        with open(STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(entries, f)
    except Exception:
        pass  # a failed write must never break a review click


def record(pack_name, metric_id, doc_vec, filename, note=""):
    """Append one correction. doc_vec is rounded to 5 decimals before saving -- plenty of
    precision for cosine similarity, and keeps the JSON file small over a long project.
    No-ops quietly if doc_vec is missing (nothing useful to remember)."""
    if not doc_vec:
        return
    entries = _load()
    entries.append({
        "pack_name": pack_name,
        "metric_id": metric_id,
        "vec": [round(float(x), 5) for x in doc_vec],
        "filename": filename,
        "note": note,
        "ts": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

    # FIFO cap PER PACK: only this pack's oldest entries are dropped once it exceeds the cap --
    # a heavily-used NAAC pack must not crowd out a smaller NBA pack's memory.
    same_pack = [i for i, e in enumerate(entries) if e.get("pack_name") == pack_name]
    if len(same_pack) > MAX_PER_PACK:
        drop = set(same_pack[: len(same_pack) - MAX_PER_PACK])
        entries = [e for i, e in enumerate(entries) if i not in drop]

    _save(entries)


def lookup(pack_name, doc_vec):
    """Return (best_entry, cosine) for the most similar remembered correction in this pack, or
    (None, 0.0) if there is no memory yet (or nothing on file for this pack)."""
    if not doc_vec:
        return None, 0.0
    entries = [e for e in _load() if e.get("pack_name") == pack_name]
    if not entries:
        return None, 0.0
    best_entry, best_cos = None, 0.0
    for e in entries:
        vec = e.get("vec")
        if not vec or len(vec) != len(doc_vec):
            continue  # mismatched embedding dimension -- a different embed model, skip rather than crash
        c = _cos(doc_vec, vec)
        if c > best_cos:
            best_cos, best_entry = c, e
    return best_entry, best_cos


def learned_override(pack_name, doc_vec, metrics):
    """Cheap, LLM-free twin of classify()'s learned-auto fast path (see pipeline.py's
    LEARNED_AUTO_THRESHOLD block) -- for use on a doc_cache HIT, where doc_vec is already known
    and re-embedding/re-voting the document would throw away the whole point of caching.

    Returns (learned_metric_dict, cos) when lookup() finds a past human correction with
    cos >= LEARNED_AUTO_THRESHOLD AND that metric id still exists in the CURRENT `metrics` list
    (a pack can be edited/re-versioned between runs, so the id a human corrected to may no
    longer be in it); otherwise (None, cos_or_0.0) so the caller keeps the cached answer as-is.

    Import of LEARNED_AUTO_THRESHOLD is done lazily inside the function body (not at module
    load) to avoid a circular import: pipeline.py already imports this module at load time.

    Never raises -- same defensive contract as lookup()/record() above: a corrupt memory file
    or an unexpected shape must degrade to "no override", not crash a cache-hit render.
    """
    try:
        from pipeline import LEARNED_AUTO_THRESHOLD
        entry, cos = lookup(pack_name, doc_vec)
    except Exception:
        return None, 0.0
    if not entry or cos < LEARNED_AUTO_THRESHOLD:
        return None, cos
    learned_metric = next((m for m in metrics if m["id"] == entry.get("metric_id")), None)
    if not learned_metric:
        # the corrected-to metric id no longer exists in this pack version -- nothing to apply
        return None, cos
    return learned_metric, cos


if __name__ == "__main__":
    print("corrections self-test: empty lookup ->", lookup("no-such-pack", [1.0, 0.0, 0.0]))
