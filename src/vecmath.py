# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Shared vector math. Cosine similarity was independently reimplemented in pipeline.py
(metric matching), corrections.py (remembered-correction matching), and duplicates.py
(near-duplicate detection) -- pulled into one place so the three call sites can't drift."""
import math


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)
