"""The classifier: embedding shortlist (top-5) -> small-LLM adjudication with self-consistency.
Code does the structure; the 3B model only picks among up to 5 and quotes evidence. That's why a
small model is enough.
"""
import hashlib
import json, math, os, re
from ollama_client import embed, generate_json

# corrections memory (Feature A) is optional: a fresh checkout with no output/_corrections.json
# yet, or a corrupted one, must classify exactly like before -- so the import itself is
# defensive. corrections.py already turns "missing/corrupt file" into "empty memory"; this
# try/except only guards against the module itself failing to import.
try:
    import corrections
except Exception:
    corrections = None

# Bump this whenever anything that changes a document's RESULT changes: classify()'s
# logic (prompt, voting, thresholds, fast path) OR how ingest.py extracts a document's
# text (the doc_cache key is sha256(file BYTES)+pack+model+this version, so an
# extraction change on an UNCHANGED file would otherwise keep serving the stale result).
# v4: corrections-memory fast path/hint injection + classify() now always returns doc_vec.
# v6: _read_docx now also reads TABLE cells + headers/footers -- table-heavy college docs
# (MoU/committee/attendance lists) used to extract as near-empty, so their old cached
# results must not be served.
PIPELINE_VERSION = "6"

# corrections-memory thresholds (Feature A): a remembered document doesn't have to be
# byte-identical to fire -- nomic-embed-text similarity this high means "basically the same
# kind of document" in practice (same letterhead/template, minor date or name edits).
LEARNED_AUTO_THRESHOLD = 0.95    # near-identical to a corrected doc -> trust it outright
LEARNED_HINT_THRESHOLD = 0.88    # similar enough to nudge the vote, not enough to skip it
LEARNED_HINT_BOOST = 0.05        # same magnitude as KEYWORD_BOOST -- a nudge, not a veto

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


def shortlist(doc_text, metrics, metric_vecs, filename="", k=SHORTLIST_K, dv=None, hint_metric_id=None):
    """Embed the doc, rank metrics by cosine similarity, apply a small keyword boost, return top-k.

    dv: precomputed doc embedding. classify() computes embed(doc_text[:1500]) once itself (so it
    can also feed corrections.lookup()) and passes it in here to avoid embedding the same text
    twice for the common short-doc case. When dv is None (long-doc windows, or direct callers)
    this embeds exactly as before.

    hint_metric_id: Feature A's "learned hint" (0.88-0.95 similarity to a past human correction).
    Guarantees that metric is present in the returned shortlist -- injecting it in place of the
    weakest candidate if the embedder didn't already rank it top-k -- and gives it a small boost
    so the LLM vote actually sees and can favour it, without silently overriding the vote outright
    the way the >=0.95 fast path does.
    """
    if dv is None:
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
    result = [(metrics[i], round(s, 3)) for s, i in top]

    if hint_metric_id:
        ids_present = [m["id"] for m, _ in result]
        if hint_metric_id in ids_present:
            # already shortlisted on its own merit -- still give it the learned nudge so the
            # vote is more likely to land on it.
            result = [(m, round(s + LEARNED_HINT_BOOST, 3)) if m["id"] == hint_metric_id else (m, s)
                      for m, s in result]
        else:
            hint_idx = next((i for i, m in enumerate(metrics) if m["id"] == hint_metric_id), None)
            if hint_idx is not None:
                # replace the WEAKEST candidate (last, since `result` is sorted descending) --
                # never bump the current top pick out, so an already-confident non-learned match
                # is not displaced by a merely-similar past correction.
                hint_sim = round(_cos(dv, metric_vecs[hint_idx]) + LEARNED_HINT_BOOST, 3)
                result[-1] = (metrics[hint_idx], hint_sim)
    return result


PROMPT = """You match a college document to ONE accreditation metric, or NONE if it truly fits none.

DOCUMENT:
{doc}

CANDIDATE METRICS:
{cands}

Pick the single best candidate. Reply ONLY as JSON:
{{"choice": "A" or "B" or "C" or "D" or "E" or "NONE", "confidence": a number 0.0 to 1.0, "evidence": "one exact sentence copied from the document that justifies it", "title": "a 3-6 word plain-English title for what this document IS, e.g. Scholarship Beneficiary List"}}"""

# fast-path thresholds for Task 2 (_classify_window): a single vote can stand in for the
# usual 2-vote self-consistency check ONLY when every one of these holds -- deliberately
# strict, because skipping the second vote trades a little safety net for latency and we
# only want to spend that trade on docs that are genuinely easy.
FAST_PATH_MODEL_CONF = 0.85
# The #2 shortlist candidate must sit clearly BELOW #1 in raw cosine similarity for the
# fast path to fire. (An earlier version tested _normalize_sim of the chosen top candidate,
# but min-max normalization within the shortlist makes the top candidate exactly 1.0 by
# definition -- that condition could never fail. This raw-gap test is the real thing.)
FAST_PATH_MIN_GAP = 0.02


def _clean_title(raw):
    """Strip quotes/brackets, collapse whitespace, cap 60 chars, Title Case -- same shape as
    enrich.suggest_name's cleanup so callers can treat result["title"] identically to it."""
    if not raw:
        return ""
    s = str(raw).replace("\n", " ").replace("\r", " ")
    s = s.strip().strip('"\'').strip()
    s = re.sub(r'[\[\]{}"]', "", s)
    s = " ".join(s.split())
    if not s:
        return ""
    s = s[:60]
    # title-case each word but don't mangle words that are already all-caps acronyms (e.g. "MoU", "NAAC")
    words = []
    for w in s.split(" "):
        words.append(w if (w.isupper() and len(w) > 1) else w.capitalize())
    return " ".join(words)


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
    title = _clean_title(r.get("title", ""))
    return ch, conf, ev, title


def _classify_window(doc_text, metrics, metric_vecs, filename, dv=None, hint_metric_id=None):
    """Run the full shortlist -> 2-vote (+ tiebreaker on disagreement) adjudication for ONE text
    window. Returns the same shape as classify()'s per-window result, used both for short docs
    (single window) and for each half of a long doc's head/middle split.

    dv/hint_metric_id: passed straight through to shortlist() -- see its docstring. classify()
    supplies dv for the short-doc case (reusing its own doc-level embedding) and hint_metric_id
    whenever corrections memory found a 0.88-0.95 similar past correction."""
    cands = shortlist(doc_text, metrics, metric_vecs, filename=filename, k=SHORTLIST_K,
                       dv=dv, hint_metric_id=hint_metric_id)
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
            "commit_level": None, "metric_uncertain": False, "title": "", "fast_path": False,
            "learned_hint": bool(hint_metric_id),
        }

    letters = ["A", "B", "C", "D", "E"][:len(cands)]
    choices, confs, evidence = [], [], ""

    # --- vote 1 -------------------------------------------------------------------------
    ch1, conf1, ev1, title = _single_run(doc_text, cands, letters, 0.3)  # title only kept from vote 1
    choices.append(ch1); confs.append(conf1)
    if ev1:
        evidence = ev1

    # --- Task 2: confident-single-vote fast path -----------------------------------------
    # If vote 1 alone already looks unambiguous from TWO independent angles (the model says
    # so AND the embedder's own ranking agrees, with real separation from the rest of the
    # shortlist), skip the normal second self-consistency vote entirely. This is deliberately
    # conservative -- all four conditions must hold -- because the whole point of the second
    # vote is to catch the model flip-flopping; we only skip that safety check when the doc
    # is easy enough that flip-flopping is very unlikely.
    fast_path = False
    top_candidate_id = cands[0][0]["id"]
    chosen_is_top = (ch1 in letters) and (cands[letters.index(ch1)][0]["id"] == top_candidate_id)
    # raw similarity gap between #1 and #2: only skip the second vote when the embedder's
    # top pick is not in a near-tie with its runner-up (a tie is exactly when the model
    # flip-flops, i.e. when the self-consistency vote earns its keep).
    top2_gap = (cands[0][1] - cands[1][1]) if len(cands) > 1 else 1.0
    if (ch1 in letters and conf1 >= FAST_PATH_MODEL_CONF and chosen_is_top
            and top2_gap >= FAST_PATH_MIN_GAP):
        fast_path = True
        agree = True
        tiebreak_used = False
    else:
        # --- vote 2 (normal path) --------------------------------------------------------
        ch2, conf2, ev2, _title2 = _single_run(doc_text, cands, letters, 0.3)
        choices.append(ch2); confs.append(conf2)
        if not evidence and ev2:
            evidence = ev2

        agree = choices[0] == choices[1]
        tiebreak_used = False
    if not fast_path and not agree:
        # one extra tiebreaker vote instead of just halving confidence: run a third time and
        # let majority decide. This recovers cases where the model flip-flopped on a coin-toss
        # between two close candidates, rather than punishing confidence for a single disagreement.
        ch3, conf3, ev3, _title3 = _single_run(doc_text, cands, letters, 0.3)
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
    # But real docs (a real Tamil Nadu college's DVV set) showed the model repeatedly nailing the CRITERION (the general
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
        "title": title,
        "fast_path": fast_path,
        "learned_hint": bool(hint_metric_id),
    }


def _latin_ratio(text):
    """Of the alphabetic characters in `text`, what fraction are ASCII/Latin letters?
    Docs with fewer than 20 letters (pure number tables, markers) count as 1.0 --
    the gate below must only fire on genuinely non-Latin PROSE."""
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 20:
        return 1.0
    latin = sum(1 for ch in letters if ch.isascii())
    return latin / len(letters)


NON_LATIN_GATE = 0.50  # below this Latin-letter share, refuse to classify -- honest review


def classify(doc_text, metrics, metric_vecs, filename="", pack_name=None):
    """pack_name: enables Feature A (corrections memory). Optional and defaults to None so every
    existing caller keeps working unchanged; without it classify() behaves exactly as PIPELINE_VERSION
    3 did (minus the harmless extra doc_vec field every result now carries)."""
    # --- non-Latin-script gate (Tamil experiment, 07.07) -----------------------------------
    # Measured fact: nomic-embed-text cannot discriminate Tamil text -- 3/3 pure-Tamil test
    # docs were AUTO-COMMITTED at 0.81-0.97 confidence to the WRONG metric (they all cluster
    # in embedding space). Confidently-wrong is the worst failure mode this tool can have, so
    # for majority-non-Latin documents we refuse to classify at all: no embedding lookup (the
    # corrections memory would false-hit across different Tamil docs for the same reason), no
    # votes, no doc_vec stored (keeps unreliable vectors OUT of the learning memory). Even a
    # doc with an English heading + Tamil body goes to review: the model can only read the
    # heading and cannot verify the body says what the heading claims.
    if _latin_ratio(doc_text[:1500]) < NON_LATIN_GATE:
        return {
            "chosen": None, "confidence": 0.0, "agree": False, "status": "review",
            "reason": "non_english_text", "evidence": "", "top_sim": 0.0,
            "candidates": [], "commit_level": None, "metric_uncertain": False,
            "title": "", "fast_path": False, "learned": False, "learned_hint": False,
            "doc_vec": None,
        }
    # doc-level embedding, computed ONCE here (not per-window) so it can double as: (a) the vector
    # corrections.lookup() searches against, (b) the vector reused by shortlist() for the common
    # short-doc case below (dv=doc_vec), and (c) result["doc_vec"] for the caller to hand back to
    # corrections.record() later if a human corrects this doc. Long-doc windows below still embed
    # their own (different, shorter) text slices -- that's real classification signal, not waste.
    doc_vec = embed(doc_text[:1500])
    rounded_vec = [round(float(x), 5) for x in doc_vec]

    hint_metric_id = None
    if pack_name and corrections is not None:
        try:
            entry, cos = corrections.lookup(pack_name, doc_vec)
        except Exception:
            # a corrupted/unreadable memory file must never break classification -- treat it as
            # "no memory yet" (corrections.lookup() already does this internally; this is belt
            # and braces against any other surprise, e.g. a bad monkeypatch in a test).
            entry, cos = None, 0.0
        if entry:
            learned_metric = next((m for m in metrics if m["id"] == entry.get("metric_id")), None)
            if learned_metric and cos >= LEARNED_AUTO_THRESHOLD:
                # near-identical to a document a human already corrected/confirmed -- skip
                # shortlist + the vote entirely (saves the LLM calls too) and trust the memory.
                return {
                    "chosen": learned_metric, "confidence": 0.99, "agree": True, "status": "auto",
                    "reason": "", "evidence": "", "top_sim": round(cos, 3),
                    "candidates": [learned_metric["id"]], "commit_level": "metric",
                    "metric_uncertain": False, "title": "", "fast_path": False,
                    "learned": True, "learned_hint": False, "doc_vec": rounded_vec,
                }
            elif learned_metric and cos >= LEARNED_HINT_THRESHOLD:
                # similar but not identical -- don't auto-apply, just make sure the vote sees
                # this metric as an option (see shortlist()'s hint_metric_id handling).
                hint_metric_id = entry.get("metric_id")

    if len(doc_text) <= LONG_DOC_CHARS:
        result = _classify_window(doc_text, metrics, metric_vecs, filename,
                                   dv=doc_vec, hint_metric_id=hint_metric_id)
    else:
        # long doc: classify on the first 900 chars and on a middle 900-char slice. If the two
        # windows land on different metrics, the document likely covers more than one topic (or
        # the head is boilerplate/letterhead) -- take the higher-confidence window's answer but
        # mark the result "review" so a human double-checks instead of silently trusting one
        # slice. Each window gets its own embedding (dv=None -> shortlist() embeds that window's
        # own text) since head vs middle really can be about different things; only hint_metric_id
        # (the corrections nudge, based on the whole doc's opening) is shared between them.
        mid_start = max(0, len(doc_text) // 2 - 450)
        head_result = _classify_window(doc_text[:900], metrics, metric_vecs, filename,
                                        hint_metric_id=hint_metric_id)
        mid_result = _classify_window(doc_text[mid_start:mid_start + 900], metrics, metric_vecs,
                                       filename, hint_metric_id=hint_metric_id)

        head_id = head_result["chosen"]["id"] if head_result["chosen"] else None
        mid_id = mid_result["chosen"]["id"] if mid_result["chosen"] else None

        if head_id == mid_id:
            result = head_result  # both windows agree -- trust the normal status/confidence
        else:
            winner = head_result if head_result["confidence"] >= mid_result["confidence"] else mid_result
            result = dict(winner)
            result["status"] = "review"
            result["reason"] = "long_doc_window_disagreement"
            # the window that "won" may have auto-committed (metric or criterion level) on its
            # own, but the head/mid split disagreeing is exactly the kind of ambiguity a human
            # should see -- don't let a stale commit_level from the winning window imply this doc
            # auto-committed overall.
            result["commit_level"] = None
            result["metric_uncertain"] = False

    result = dict(result)
    result["doc_vec"] = rounded_vec
    result.setdefault("learned", False)
    result.setdefault("learned_hint", False)
    return result
