"""The classifier: embedding shortlist (top-3) -> small-LLM adjudication with self-consistency.
Code does the structure; the 3B model only picks among 3 and quotes evidence. That's why a small model is enough.
"""
import json, math, os
from ollama_client import embed, generate_json

CACHE = os.path.join(os.path.dirname(__file__), "..", "output", "_metric_vectors.json")
REVIEW_THRESHOLD = 0.55   # below this confidence -> human review queue


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


def embed_metrics(metrics, pack_name, log=print):
    """Embed every metric once; cache to disk so re-runs are instant."""
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    key = pack_name
    if key in cache and len(cache[key]) == len(metrics):
        return cache[key]
    log(f"  embedding {len(metrics)} metrics (one-time)...")
    vecs = [embed(m["search_text"]) for m in metrics]
    cache[key] = vecs
    json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    return vecs


def shortlist(doc_text, metrics, metric_vecs, k=3):
    dv = embed(doc_text[:1500])
    scored = sorted(
        ((_cos(dv, mv), i) for i, mv in enumerate(metric_vecs)),
        reverse=True,
    )[:k]
    return [(metrics[i], round(s, 3)) for s, i in scored]


PROMPT = """You match a college document to ONE accreditation metric, or NONE if it truly fits none.

DOCUMENT:
{doc}

CANDIDATE METRICS:
{cands}

Pick the single best candidate. Reply ONLY as JSON:
{{"choice": "A" or "B" or "C" or "NONE", "confidence": a number 0.0 to 1.0, "evidence": "one exact sentence copied from the document that justifies it"}}"""


def _adjudicate(doc_text, cands, temperature):
    letters = ["A", "B", "C"][:len(cands)]
    cand_block = "\n".join(f"{L}) [{m['id']}] {m['text'][:160]}" for L, (m, _) in zip(letters, cands))
    prompt = PROMPT.format(doc=doc_text[:900], cands=cand_block)
    out = generate_json(prompt, temperature=temperature)
    return out


def classify(doc_text, metrics, metric_vecs):
    cands = shortlist(doc_text, metrics, metric_vecs, k=3)
    letters = ["A", "B", "C"][:len(cands)]
    # self-consistency: two runs at low temperature
    runs = [_adjudicate(doc_text, cands, 0.3), _adjudicate(doc_text, cands, 0.3)]
    choices, confs, evidence = [], [], ""
    for r in runs:
        ch = str(r.get("choice", "NONE")).strip().upper()[:4].rstrip(")")
        choices.append(ch)
        try:
            confs.append(float(r.get("confidence", 0)))
        except (TypeError, ValueError):
            confs.append(0.0)
        if not evidence and r.get("evidence"):
            evidence = str(r["evidence"])[:300]

    agree = choices[0] == choices[1]
    pick = choices[0]
    model_conf = sum(confs) / len(confs) if confs else 0.0
    final_conf = model_conf if agree else model_conf * 0.5   # disagreement halves confidence

    if pick in letters and agree:
        m, sim = cands[letters.index(pick)]
        chosen = m
    elif pick in letters:  # picked something but runs disagreed
        m, sim = cands[letters.index(pick)]
        chosen = m
    else:  # NONE or garbage
        chosen, sim = None, cands[0][1]

    status = "auto" if (chosen and final_conf >= REVIEW_THRESHOLD and agree) else "review"
    return {
        "chosen": chosen,
        "confidence": round(final_conf, 2),
        "agree": agree,
        "status": status,
        "evidence": evidence,
        "top_sim": sim,
        "candidates": [m["id"] for m, _ in cands],
    }
