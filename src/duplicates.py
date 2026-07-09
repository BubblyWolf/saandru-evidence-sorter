# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Duplicate finder -- Feature B. Within ONE run, flag files that are copies of each other so
office staff can keep one and delete the rest instead of accidentally filing the same evidence
twice under two different metrics.

Two kinds of "same":
  - "exact": identical file bytes (same sha256), however the file got renamed/moved/nested.
  - "near": cosine >= NEAR_THRESHOLD between the CLASSIFY-time document embeddings (the doc_vec
    pipeline.classify() now returns) -- catches re-saves/re-scans/re-exports that changed a few
    bytes but are clearly the same document. Only docs that were actually classified (readable,
    non-marker) have a doc_vec, so unreadable/marker files never produce a false "near" match with
    each other purely because their marker text ("[UNREADABLE: ...]" etc) looks similar.

Embeddings + plain code ONLY -- no chat-model calls here.
"""
import hashlib

from vecmath import cosine_similarity as _cos

NEAR_THRESHOLD = 0.98


def file_sha256(path):
    """sha256 of a file's raw bytes. Same pattern as doc_cache.make_key()'s file hashing, pulled
    out standalone here since duplicate-detection needs the bare file hash, not doc_cache's
    combined (bytes + pack + model + pipeline-version) cache key."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read())
    except Exception:
        return None  # unreadable file -- exact-match detection just skips it, never crashes
    return h.hexdigest()




def find_duplicates(items):
    """items: list of {"filename": ..., "sha256": ... or None, "doc_vec": [...] or None}.

    Returns groups: [{"files": [...], "kind": "exact" | "near"}, ...]. Overlapping pairs are
    merged with union-find so one file never appears in two separate groups (e.g. A exact-matches
    B, B near-matches C -> one group {A, B, C}). A group is tagged "exact" if ANY pair inside it
    matched by sha256, even if other members only joined via a near (embedding) match.
    """
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    exact_pairs = set()
    for i in range(n):
        h1 = items[i].get("sha256")
        if not h1:
            continue
        for j in range(i + 1, n):
            h2 = items[j].get("sha256")
            if h2 and h1 == h2:
                union(i, j)
                exact_pairs.add((i, j))

    for i in range(n):
        v1 = items[i].get("doc_vec")
        if not v1:
            continue
        for j in range(i + 1, n):
            v2 = items[j].get("doc_vec")
            if not v2 or len(v1) != len(v2):
                continue
            if _cos(v1, v2) >= NEAR_THRESHOLD:
                union(i, j)

    groups_idx = {}
    for i in range(n):
        groups_idx.setdefault(find(i), []).append(i)

    result = []
    for idxs in groups_idx.values():
        if len(idxs) < 2:
            continue
        is_exact = any((min(a, b), max(a, b)) in exact_pairs
                        for a in idxs for b in idxs if a != b)
        result.append({
            "files": [items[i]["filename"] for i in idxs],
            "kind": "exact" if is_exact else "near",
        })
    return result
