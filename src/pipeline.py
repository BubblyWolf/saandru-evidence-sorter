"""The classifier: embedding shortlist (top-5) -> small-LLM adjudication with self-consistency.
Code does the structure; the 3B model only picks among up to 5 and quotes evidence. That's why a
small model is enough.
"""
import hashlib
import json, math, os
from ollama_client import embed, generate_json

CACHE = os.path.join(os.path.dirname(__file__), "..", "output", "_metric_vectors.json")
REVIEW_THRESHOLD = 0.55   # below this confidence -> human review queue
SIM_FLOOR = 0.45          # below this top cosine similarity, don't even call the LLM
SHORTLIST_K = 5           # A-E
LONG_DOC_CHARS = 1500     # docs longer than this get classified twice (head + middle) and cross-checked

# keyword boost: a small list of rare/distinctive terms. If a candidate metric's text shares one
# of these with the document's opening (title/filename territory), nudge its similarity up a
# little before ranking. This helps short, jargon-y docs (MoUs, scholarship letters, green audit
# reports) beat generic-sounding neighbours that otherwise win on pure cosine similarity.
KEYWORD_BOOST_TERMS = [
    "mou", "placement", "scholarship", "green audit", "feedback", "e-governance",
    "egovernance", "alumni", "nss", "ncc", "extension", "iqac", "grievance",
    "internship", "research grant", "patent", "consultancy", "library", "hostel",
    "gender", "disabled", "divyang", "sports", "cultural", "wi-fi", "wifi",
    "energy audit", "rain water", "waste management", "e-resources", "phd",
]
KEYWORD_BOOST = 0.05


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb + 1e-9)


def _pack_hash(metrics):
    """Hash of every metric's search_text, concatenated in id order. Used as the cache key so a
    pack regeneration (e.g. re-parsing the manual with better boilerplate stripping) invalidates
    stale cached vectors automatically -- keying by pack NAME alone can't detect that the metric
    TEXTS underneath changed."""
    h = hashlib.sha256()
    for m in metrics:
        h.update(m["search_text"].encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def embed_metrics(metrics, pack_name, log=print):
    """Embed every metric once; cache to disk so re-runs are instant."""
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
    key = f"{pack_name}:{_pack_hash(metrics)}"
    if key in cache and len(cache[key]) == len(metrics):
        return cache[key]
    log(f"  embedding {len(metrics)} metrics (one-time)...")
    vecs = [embed(m["search_text"]) for m in metrics]
    cache[key] = vecs
    json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    return vecs


def _keyword_hits(text):
    low = text.lower()
    return {t for t in KEYWORD_BOOST_TERMS if t in low}


def shortlist(doc_text, metrics, metric_vecs, filename="", k=SHORTLIST_K):
    """Embed the doc, rank metrics by cosine similarity, apply a small keyword boost, return top-k."""
    dv = embed(doc_text[:1500])
    head_hits = _keyword_hits(doc_text[:120] + " " + filename)
    scored = []
    for i, mv in enumerate(metric_vecs):
        sim = _cos(dv, mv)
        if head_hits and _keyword_hits(metrics[i]["text"]) & head_hits:
            sim += KEYWORD_BOOST
        scored.append((sim, i))
    scored.sort(reverse=True)
    top = scored[:k]
    return [(metrics[i], round(s, 3)) for s, i in top]


PROMPT = """You match a college document to ONE accreditation metric, or NONE if it truly fits none.

DOCUMENT:
{doc}

CANDIDATE METRICS:
{cands}

Pick the single best candidate. Reply ONLY as JSON:
{{"choice": "A" or "B" or "C" or "D" or "E" or "NONE", "confidence": a number 0.0 to 1.0, "evidence": "one exact sentence copied from the document that justifies it"}}"""


def _adjudicate(doc_text, cands, temperature):
    letters = ["A", "B", "C", "D", "E"][:len(cands)]
    cand_block = "\n".join(f"{L}) [{m['id']}] {m['text'][:160]}" for L, (m, _) in zip(letters, cands))
    prompt = PROMPT.format(doc=doc_text[:900], cands=cand_block)
    out = generate_json(prompt, temperature=temperature)
    return out


def _normalize_sim(sim, cands):
    """Map a cosine similarity into 0..1 for blending with model confidence.
    Raw cosine similarities from nomic-embed-text cluster tightly (roughly 0.3-0.7 in practice)
    rather than spanning the full [-1, 1] range, so using the raw value would almost always read
    as a low "confidence" even for a clearly-best match. We min-max normalize sim WITHIN the
    candidate set (top pick vs the weakest of the shortlist) -- this rewards a candidate that
    stands out clearly from its neighbours (real signal) and does not reward a top pick that
    barely beats the pack (ambiguous, arguably should be less confident). Falls back to the raw
    value clipped to [0,1] if the candidate set is degenerate (all equal sim, e.g. k=1)."""
    sims = [s for _, s in cands]
    lo, hi = min(sims), max(sims)
    if hi - lo < 1e-6:
        return max(0.0, min(1.0, sim))
    return max(0.0, min(1.0, (sim - lo) / (hi - lo)))


def _single_run(doc_text, cands, letters, temperature):
    r = _adjudicate(doc_text, cands, temperature)
    ch = str(r.get("choice", "NONE")).strip().upper()[:4].rstrip(")")
    try:
        conf = float(r.get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    ev = str(r["evidence"])[:300] if r.get("evidence") else ""
    return ch, conf, ev


def _classify_window(doc_text, metrics, metric_vecs, filename):
    """Run the full shortlist -> 2-vote (+ tiebreaker on disagreement) adjudication for ONE text
    window. Returns the same shape as classify()'s per-window result, used both for short docs
    (single window) and for each half of a long doc's head/middle split."""
    cands = shortlist(doc_text, metrics, metric_vecs, filename=filename, k=SHORTLIST_K)
    top_sim = cands[0][1]

    if top_sim < SIM_FLOOR:
        # honest abstention: don't even spend LLM calls on a shortlist the embedder itself is
        # unsure about -- a low top similarity means the true metric probably isn't in the
        # candidate set at all, so no amount of LLM adjudication among these 5 can be trusted.
        # Below the floor there is no reliable criterion signal either, so commit_level stays
        # None -- never invent a metric (or even a criterion) when the embedder itself is lost.
        return {
            "chosen": None, "confidence": 0.0, "agree": False, "status": "review",
            "reason": "low_similarity", "evidence": "", "top_sim": top_sim,
            "candidates": [m["id"] for m, _ in cands],
            "commit_level": None, "metric_uncertain": False,
        }

    letters = ["A", "B", "C", "D", "E"][:len(cands)]
    choices, confs, evidence = [], [], ""
    for temperature in (0.3, 0.3):
        ch, conf, ev = _single_run(doc_text, cands, letters, temperature)
        choices.append(ch); confs.append(conf)
        if not evidence and ev:
            evidence = ev

    agree = choices[0] == choices[1]
    tiebreak_used = False
    if not agree:
        # one extra tiebreaker vote instead of just halving confidence: run a third time and
        # let majority decide. This recovers cases where the model flip-flopped on a coin-toss
        # between two close candidates, rather than punishing confidence for a single disagreement.
        ch3, conf3, ev3 = _single_run(doc_text, cands, letters, 0.3)
        choices.append(ch3); confs.append(conf3)
        tiebreak_used = True
        if not evidence and ev3:
            evidence = ev3
        counts = {}
        for c in choices:
            counts[c] = counts.get(c, 0) + 1
        best_count = max(counts.values())
        winners = [c for c, n in counts.items() if n == best_count]
        pick = winners[0] if len(winners) == 1 else choices[0]
        # agree stays False: not all three runs matched, even if 2-of-3 formed a majority
        agree = False
    else:
        pick = choices[0]

    model_conf = sum(confs) / len(confs) if confs else 0.0
    if pick in letters:
        m, sim = cands[letters.index(pick)]
        chosen = m
    else:  # NONE or garbage
        chosen, sim = None, top_sim

    norm_sim = _normalize_sim(sim, cands)
    # hybrid confidence: blend the model's self-reported confidence with how well-separated the
    # chosen candidate's embedding similarity is from the rest of the shortlist. The model alone
    # over-reports (0.9+ even for NONE, see baseline diagnosis); pure similarity alone ignores
    # what the LLM actually read in the document text. Averaging keeps either signal from
    # dominating -- a confident-sounding model pick on a barely-distinguishable embedding still
    # lands mid-scale, and vice versa.
    final_conf = 0.5 * model_conf + 0.5 * norm_sim
    if tiebreak_used and not agree:
        # majority vote already recovered from the disagreement; still shave a little because
        # not all three runs matched (keeps the old "disagreement costs something" property).
        final_conf *= 0.85

    # --- criterion-level fallback signal ---------------------------------------------------
    # The metric-level vote above is strict: both runs must land on the exact same metric id.
    # But real docs (JJCET set) showed the model repeatedly nailing the CRITERION (the general
    # area, e.g. "1.3 Curriculum Enrichment") while flip-flopping on which specific metric within
    # it applies -- that's a false abstention, not a real failure. So collect a second, looser
    # signal: the criterion of every run's chosen metric, plus the criterion of the top-2
    # shortlisted candidates (the embedder's own best guesses, independent of what the LLM said).
    # If one criterion clearly dominates that pool, we trust "the area is right" even when the
    # exact metric vote didn't converge.
    criterion_votes = []
    for c in choices:
        if c in letters:
            criterion_votes.append(cands[letters.index(c)][0]["criterion"])
    for cand_m, _ in cands[:2]:
        criterion_votes.append(cand_m["criterion"])

    criterion_confident = False
    dominant_criterion = None
    if criterion_votes:
        tally = {}
        for cv in criterion_votes:
            tally[cv] = tally.get(cv, 0) + 1
        dominant_criterion, dom_count = max(tally.items(), key=lambda kv: kv[1])
        # "clearly dominates" = majority of the pooled votes (runs' picks + top-2 shortlist),
        # e.g. 2 different metrics that both fall under the same criterion, or all runs +
        # top candidate agreeing on the area even if the metric itself was contested.
        if dom_count > len(criterion_votes) / 2:
            criterion_confident = True

    metric_uncertain = False
    commit_level = None
    if chosen and final_conf >= REVIEW_THRESHOLD and agree:
        # both runs converged on the same exact metric with solid confidence -- the strongest
        # case, commit at metric level exactly as before.
        commit_level = "metric"
        status = "auto"
    elif top_sim < SIM_FLOOR:
        # guarded again here for the long-doc path where a window's own top_sim could in theory
        # still be borderline; never auto-commit anything, metric or criterion, below the floor.
        commit_level = None
        chosen = None
        status = "review"
    elif criterion_confident:
        # right neighbourhood, exact metric unsure -- commit to the criterion and hand over the
        # single best-guess metric within it (highest-confidence candidate that belongs to the
        # dominant criterion) so the human only has to confirm a number, not start from scratch.
        in_crit = [(m, s) for m, s in cands if m["criterion"] == dominant_criterion]
        best_m, best_s = max(in_crit, key=lambda ms: ms[1]) if in_crit else (cands[0][0], cands[0][1])
        chosen = best_m
        sim = best_s
        commit_level = "criterion"
        metric_uncertain = True
        status = "auto"
    else:
        # no metric agreement and no dominant criterion either -- genuine abstention, but still
        # surface the model's best guess (chosen stays as computed above) so a human reviewer
        # has a starting point instead of a blank row.
        commit_level = None
        status = "review"

    return {
        "chosen": chosen,
        "confidence": round(final_conf, 2),
        "agree": agree,
        "status": status,
        "reason": "" if status == "auto" else ("disagreement" if not agree else "low_confidence"),
        "evidence": evidence,
        "top_sim": sim,
        "candidates": [m["id"] for m, _ in cands],
        "commit_level": commit_level,
        "metric_uncertain": metric_uncertain,
    }


def classify(doc_text, metrics, metric_vecs, filename=""):
    if len(doc_text) <= LONG_DOC_CHARS:
        return _classify_window(doc_text, metrics, metric_vecs, filename)

    # long doc: classify on the first 900 chars and on a middle 900-char slice. If the two
    # windows land on different metrics, the document likely covers more than one topic (or the
    # head is boilerplate/letterhead) -- take the higher-confidence window's answer but mark the
    # result "review" so a human double-checks instead of silently trusting one slice.
    mid_start = max(0, len(doc_text) // 2 - 450)
    head_result = _classify_window(doc_text[:900], metrics, metric_vecs, filename)
    mid_result = _classify_window(doc_text[mid_start:mid_start + 900], metrics, metric_vecs, filename)

    head_id = head_result["chosen"]["id"] if head_result["chosen"] else None
    mid_id = mid_result["chosen"]["id"] if mid_result["chosen"] else None

    if head_id == mid_id:
        return head_result  # both windows agree -- trust the normal status/confidence

    winner = head_result if head_result["confidence"] >= mid_result["confidence"] else mid_result
    result = dict(winner)
    result["status"] = "review"
    result["reason"] = "long_doc_window_disagreement"
    # the window that "won" may have auto-committed (metric or criterion level) on its own, but
    # the head/mid split disagreeing is exactly the kind of ambiguity a human should see -- don't
    # let a stale commit_level from the winning window imply this doc auto-committed overall.
    result["commit_level"] = None
    result["metric_uncertain"] = False
    return result
