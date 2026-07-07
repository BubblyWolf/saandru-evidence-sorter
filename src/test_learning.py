"""Offline self-test for Feature A (corrections.py + pipeline.classify()'s learned paths) and
Feature B (duplicates.py). No Ollama needed -- embed()/generate_json() are monkeypatched.

Run: python src/test_learning.py   (from the D:\\praman project root)
"""
import os
import sys
import json

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))

import corrections            # noqa: E402
import pipeline                # noqa: E402
from duplicates import find_duplicates  # noqa: E402
import ollama_client           # noqa: E402

problems = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not cond else ""))
    if not cond:
        problems.append(name)


# ---------------------------------------------------------------------------
# Feature A: corrections.py round-trip
# ---------------------------------------------------------------------------
def test_corrections_roundtrip():
    print("\n== corrections.py: record/lookup round trip ==")
    corrections.STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "_test_corrections.json")
    if os.path.exists(corrections.STORE_PATH):
        os.remove(corrections.STORE_PATH)

    entry, cos = corrections.lookup("packA", [1.0, 0.0, 0.0])
    check("empty memory -> (None, 0.0)", entry is None and cos == 0.0)

    vec = [0.123456789, 0.5, -0.25]
    corrections.record("packA", "5.1.1", vec, "scholarship_list.pdf", note="accepted")
    entries = corrections._load()
    check("record() wrote exactly one entry", len(entries) == 1, str(entries))
    check("vec rounded to 5 decimals", entries[0]["vec"] == [0.12346, 0.5, -0.25], str(entries[0]["vec"]))

    best, cos = corrections.lookup("packA", vec)
    check("lookup() finds the exact same vector at cosine ~1.0", best is not None and cos > 0.9999, str(cos))
    check("lookup() returns the right metric_id", best["metric_id"] == "5.1.1")

    best2, cos2 = corrections.lookup("packB", vec)
    check("lookup() is scoped per pack_name (packB sees nothing)", best2 is None and cos2 == 0.0)

    unrelated, cos3 = corrections.lookup("packA", [0.0, 0.0, -1.0])
    check("an unrelated vector still returns SOME best match (low cosine)", unrelated is not None)
    check("...but with low similarity", cos3 < 0.5, str(cos3))


def test_corrections_fifo_cap():
    print("\n== corrections.py: FIFO cap per pack ==")
    corrections.STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "_test_corrections_fifo.json")
    if os.path.exists(corrections.STORE_PATH):
        os.remove(corrections.STORE_PATH)

    for i in range(corrections.MAX_PER_PACK + 10):
        corrections.record("packA", f"m{i}", [float(i), 0.0], f"file{i}.pdf")
    entries = [e for e in corrections._load() if e["pack_name"] == "packA"]
    check(f"pack capped at MAX_PER_PACK ({corrections.MAX_PER_PACK})",
          len(entries) == corrections.MAX_PER_PACK, str(len(entries)))
    check("oldest entries were dropped (file0.pdf gone)",
          all(e["filename"] != "file0.pdf" for e in entries))
    check("newest entry survived (last one written)",
          entries[-1]["filename"] == f"file{corrections.MAX_PER_PACK + 9}.pdf")

    # a different pack must not be affected by packA's cap
    corrections.record("packB", "x", [1.0, 0.0], "other.pdf")
    entries_b = [e for e in corrections._load() if e["pack_name"] == "packB"]
    check("a different pack keeps its own entries independently", len(entries_b) == 1)


def test_corrections_corrupt_file():
    print("\n== corrections.py: corrupt/missing file never crashes ==")
    corrections.STORE_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "_test_corrections_corrupt.json")
    os.makedirs(os.path.dirname(corrections.STORE_PATH), exist_ok=True)
    with open(corrections.STORE_PATH, "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ]][[")

    try:
        entry, cos = corrections.lookup("packA", [1.0, 0.0])
        crashed = False
    except Exception as e:
        crashed = True
        entry, cos = None, 0.0
        print(f"    (unexpected exception: {e})")
    check("lookup() on a corrupt file does not raise", not crashed)
    check("corrupt file behaves like empty memory", entry is None and cos == 0.0)

    try:
        corrections.record("packA", "1.1.1", [1.0, 0.0], "new.pdf")
        crashed2 = False
    except Exception as e:
        crashed2 = True
        print(f"    (unexpected exception: {e})")
    check("record() after a corrupt file does not raise", not crashed2)

    best, cos = corrections.lookup("packA", [1.0, 0.0])
    check("the corrupt file self-heals after the next record()", best is not None and best["metric_id"] == "1.1.1")

    # missing file entirely
    os.remove(corrections.STORE_PATH)
    entry, cos = corrections.lookup("packA", [1.0, 0.0])
    check("missing file also behaves like empty memory", entry is None and cos == 0.0)


# ---------------------------------------------------------------------------
# pipeline.classify(): learned fast-return (>=0.95) and learned hint (0.88-0.95)
# ---------------------------------------------------------------------------
def _make_metrics():
    # 6 synthetic metrics with hand-picked 4-dim embeddings so shortlist ranking is fully
    # deterministic and does not depend on any real embedder.
    metrics = [
        {"id": "1.1.1", "criterion": "1", "criterion_name": "Curriculum", "ki": "1.1",
         "text": "curriculum design metric one", "search_text": "x"},
        {"id": "1.2.1", "criterion": "1", "criterion_name": "Curriculum", "ki": "1.2",
         "text": "curriculum enrichment metric two", "search_text": "x"},
        {"id": "2.1.1", "criterion": "2", "criterion_name": "Teaching", "ki": "2.1",
         "text": "teaching learning metric three", "search_text": "x"},
        {"id": "3.1.1", "criterion": "3", "criterion_name": "Research", "ki": "3.1",
         "text": "research grants metric four", "search_text": "x"},
        {"id": "4.1.1", "criterion": "4", "criterion_name": "Infrastructure", "ki": "4.1",
         "text": "infrastructure metric five", "search_text": "x"},
        {"id": "5.1.1", "criterion": "5", "criterion_name": "Student Support", "ki": "5.1",
         "text": "scholarship metric six -- THE LEARNED ONE", "search_text": "x"},
    ]
    # against the fixed doc vector [1,0,0,0] used throughout these tests, the raw cosines come
    # out strictly descending 1.0 > 0.994 > 0.707 > 0.394 > 0.110 > 0.0 -- no ties, so which one
    # gets excluded from a top-5-of-6 shortlist is deterministic (5.1.1, the weakest).
    metric_vecs = [
        [1.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.5, 0.5, 0.0, 0.0],
        [0.3, 0.7, 0.0, 0.0],
        [0.1, 0.9, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],   # 5.1.1 "THE LEARNED ONE" -- deliberately the weakest match
    ]
    return metrics, metric_vecs


class _CallCounter:
    def __init__(self, return_value):
        self.calls = 0
        self.return_value = return_value

    def __call__(self, *a, **kw):
        self.calls += 1
        return self.return_value


def test_classify_learned_fast_path():
    print("\n== pipeline.classify(): learned fast-return path (cosine >= 0.95) ==")
    metrics, metric_vecs = _make_metrics()
    doc_vec_fixed = [1.0, 0.0, 0.0, 0.0]

    real_embed = pipeline.embed
    real_generate_json = pipeline.generate_json
    real_lookup = corrections.lookup
    gen_counter = _CallCounter({"choice": "A", "confidence": 0.9, "evidence": "ev", "title": "t"})
    try:
        pipeline.embed = lambda text, *a, **kw: doc_vec_fixed
        pipeline.generate_json = gen_counter
        corrections.lookup = lambda pack_name, dv: (
            ({"metric_id": "5.1.1", "pack_name": pack_name}, 0.97) if pack_name == "testpack" else (None, 0.0)
        )

        result = pipeline.classify("some scholarship list document text", metrics, metric_vecs,
                                    filename="doc.pdf", pack_name="testpack")

        check("learned=True", result.get("learned") is True)
        check("chosen metric is the learned one (5.1.1)", result["chosen"]["id"] == "5.1.1", str(result["chosen"]))
        check("commit_level == metric", result["commit_level"] == "metric")
        check("status == auto", result["status"] == "auto")
        check("confidence == 0.99", result["confidence"] == 0.99)
        check("doc_vec is present and rounded", result.get("doc_vec") == [1.0, 0.0, 0.0, 0.0])
        check("NO chat-model (generate_json) calls were made", gen_counter.calls == 0, str(gen_counter.calls))

        # sanity: without pack_name, the same doc must NOT fast-return via corrections (falls
        # through to the normal vote instead) -- proves the fast path is opt-in via pack_name.
        gen_counter.calls = 0
        result2 = pipeline.classify("some scholarship list document text", metrics, metric_vecs,
                                     filename="doc.pdf", pack_name=None)
        check("without pack_name, learned=False (normal vote runs)", result2.get("learned") is False)
        check("...and generate_json WAS called this time", gen_counter.calls > 0)
    finally:
        pipeline.embed = real_embed
        pipeline.generate_json = real_generate_json
        corrections.lookup = real_lookup


def test_classify_learned_hint():
    print("\n== pipeline.classify(): learned hint path (0.88 <= cosine < 0.95) ==")
    metrics, metric_vecs = _make_metrics()
    doc_vec_fixed = [1.0, 0.0, 0.0, 0.0]

    real_embed = pipeline.embed
    real_generate_json = pipeline.generate_json
    real_lookup = corrections.lookup
    gen_counter = _CallCounter({"choice": "A", "confidence": 0.9, "evidence": "ev", "title": "t"})
    try:
        pipeline.embed = lambda text, *a, **kw: doc_vec_fixed
        pipeline.generate_json = gen_counter
        corrections.lookup = lambda pack_name, dv: ({"metric_id": "5.1.1", "pack_name": pack_name}, 0.90)

        # first confirm 5.1.1 is NOT naturally in the top-5 (its raw cosine to [1,0,0,0] is 0,
        # the weakest of all 6 candidates) so injection is actually being exercised.
        plain_cands = pipeline.shortlist("txt", metrics, metric_vecs, dv=doc_vec_fixed, k=5)
        check("setup: 5.1.1 is NOT in the natural top-5", "5.1.1" not in [m["id"] for m, _ in plain_cands])

        injected_cands = pipeline.shortlist("txt", metrics, metric_vecs, dv=doc_vec_fixed, k=5,
                                             hint_metric_id="5.1.1")
        check("shortlist() injects the hint metric when missing",
              "5.1.1" in [m["id"] for m, _ in injected_cands])
        hint_sim = next(s for m, s in injected_cands if m["id"] == "5.1.1")
        check("injected candidate got the +0.05 boost (score > raw cosine of 0.0)", hint_sim >= 0.05, str(hint_sim))

        result = pipeline.classify("some document text", metrics, metric_vecs,
                                    filename="doc.pdf", pack_name="testpack")
        check("learned=False (hint does not auto-apply)", result.get("learned") is False)
        check("learned_hint=True", result.get("learned_hint") is True)
        check("5.1.1 made it into the final candidates seen by the vote",
              "5.1.1" in result["candidates"], str(result["candidates"]))
        check("generate_json WAS still called (hint doesn't skip the vote)", gen_counter.calls > 0)
    finally:
        pipeline.embed = real_embed
        pipeline.generate_json = real_generate_json
        corrections.lookup = real_lookup


def test_classify_no_false_learned_hit():
    print("\n== pipeline.classify(): a different, unrelated doc must NOT get a false learned hit ==")
    metrics, metric_vecs = _make_metrics()

    real_embed = pipeline.embed
    real_generate_json = pipeline.generate_json
    real_lookup = corrections.lookup
    gen_counter = _CallCounter({"choice": "A", "confidence": 0.9, "evidence": "ev", "title": "t"})
    try:
        pipeline.embed = lambda text, *a, **kw: [0.0, 1.0, 0.0, 0.0]  # far from the remembered vec
        pipeline.generate_json = gen_counter
        corrections.lookup = lambda pack_name, dv: (
            {"metric_id": "5.1.1", "pack_name": pack_name}, 0.05  # cosine deliberately low
        )
        result = pipeline.classify("an unrelated document", metrics, metric_vecs,
                                    filename="other.pdf", pack_name="testpack")
        check("far-away doc: learned=False", result.get("learned") is False)
        check("far-away doc: learned_hint=False", result.get("learned_hint") is False)
    finally:
        pipeline.embed = real_embed
        pipeline.generate_json = real_generate_json
        corrections.lookup = real_lookup


# ---------------------------------------------------------------------------
# Feature B: duplicates.py
# ---------------------------------------------------------------------------
def test_find_duplicates():
    print("\n== duplicates.py: find_duplicates() ==")

    # exact: A and B share sha256, C is unrelated
    items = [
        {"filename": "A.pdf", "sha256": "hash1", "doc_vec": [1.0, 0.0]},
        {"filename": "B.pdf", "sha256": "hash1", "doc_vec": [1.0, 0.0]},
        {"filename": "C.pdf", "sha256": "hash2", "doc_vec": [0.0, 1.0]},
    ]
    groups = find_duplicates(items)
    check("exact duplicate group found", len(groups) == 1 and groups[0]["kind"] == "exact",
          str(groups))
    check("exact group has exactly A and B", set(groups[0]["files"]) == {"A.pdf", "B.pdf"})
    check("C is not in any group (no false positive)",
          all("C.pdf" not in g["files"] for g in groups))

    # near: D and E have different hashes but near-identical embeddings
    items2 = [
        {"filename": "D.pdf", "sha256": "hashD", "doc_vec": [1.0, 0.0]},
        {"filename": "E.pdf", "sha256": "hashE", "doc_vec": [0.995, 0.0998]},  # cos ~0.995
        {"filename": "F.pdf", "sha256": "hashF", "doc_vec": [0.0, 1.0]},       # orthogonal, cos 0
    ]
    groups2 = find_duplicates(items2)
    check("near-duplicate group found", len(groups2) == 1 and groups2[0]["kind"] == "near", str(groups2))
    check("near group has exactly D and E", set(groups2[0]["files"]) == {"D.pdf", "E.pdf"})
    check("F (orthogonal vector) has no false-positive match", all("F.pdf" not in g["files"] for g in groups2))

    # overlap merge: G-H exact, H-I near (but not G-I directly) -> one merged group, still "exact"
    items3 = [
        {"filename": "G.pdf", "sha256": "hashG", "doc_vec": [1.0, 0.0]},
        {"filename": "H.pdf", "sha256": "hashG", "doc_vec": [1.0, 0.0]},
        {"filename": "I.pdf", "sha256": "hashI", "doc_vec": [0.997, 0.0774]},  # near H, not exact
    ]
    groups3 = find_duplicates(items3)
    check("overlapping exact+near groups merge into one", len(groups3) == 1, str(groups3))
    check("merged group contains all three files", set(groups3[0]["files"]) == {"G.pdf", "H.pdf", "I.pdf"})
    check("merged group stays tagged 'exact' (because G-H matched by hash)", groups3[0]["kind"] == "exact")

    # markers/unreadables (doc_vec=None) never produce a false near-match with each other
    items4 = [
        {"filename": "bad1.pdf", "sha256": "hashBad1", "doc_vec": None},
        {"filename": "bad2.pdf", "sha256": "hashBad2", "doc_vec": None},
    ]
    groups4 = find_duplicates(items4)
    check("two unreadable files (doc_vec=None) never near-match each other", groups4 == [], str(groups4))

    # single-file list: no group possible
    check("a single item never forms a group", find_duplicates([items[0]]) == [])


# ---------------------------------------------------------------------------
# Task B: hardware auto-tiering (ollama_client._choose_chat_model)
# ---------------------------------------------------------------------------
def test_chat_model_tiering():
    print("\n== ollama_client._choose_chat_model(): hardware auto-tiering ==")
    HIGH, LOW = ollama_client._HIGH_RAM_MODEL, ollama_client._LOW_RAM_MODEL

    real_env = os.environ.pop("PRAMAN_MODEL", None)
    try:
        # high-RAM machine, both models installed -> default 3b, unchanged.
        model, reason = ollama_client._choose_chat_model(ram_gb=16, installed=[HIGH, LOW])
        check("high-RAM -> default 3b model", model == HIGH, model)
        check("high-RAM reason mentions RAM", "RAM" in reason, reason)

        # low-RAM machine, both installed -> prefers the 1.5b tier.
        model, reason = ollama_client._choose_chat_model(ram_gb=8, installed=[HIGH, LOW])
        check("low-RAM -> prefers 1.5b model", model == LOW, model)
        check("low-RAM reason mentions low-RAM tier", "low-RAM" in reason, reason)

        # low-RAM machine, but 1.5b was never pulled -> falls back to the installed 3b
        # rather than requesting a model Ollama doesn't have.
        model, reason = ollama_client._choose_chat_model(ram_gb=8, installed=[HIGH])
        check("low-RAM + 1.5b not installed -> falls back to installed 3b", model == HIGH, model)
        check("fallback reason says so", "fallback" in reason and HIGH in reason, reason)

        # high-RAM machine, but 3b was never pulled and only 1.5b is -> falls back the
        # other direction too (reconciliation is symmetric).
        model, reason = ollama_client._choose_chat_model(ram_gb=16, installed=[LOW])
        check("high-RAM + 3b not installed -> falls back to installed 1.5b", model == LOW, model)

        # neither tier installed / Ollama unreachable -> keep the RAM-preferred name as-is
        # (no crash, no guessing a third model).
        model, reason = ollama_client._choose_chat_model(ram_gb=16, installed=[])
        check("nothing installed -> keeps RAM-preferred name (no crash)", model == HIGH, model)

        # env override wins over everything, even a low-RAM machine with nothing installed.
        os.environ["PRAMAN_MODEL"] = "custom-model:latest"
        model, reason = ollama_client._choose_chat_model(ram_gb=4, installed=[])
        check("env override wins over RAM tier", model == "custom-model:latest", model)
        check("env override reason says so", reason == "env override", reason)
    finally:
        os.environ.pop("PRAMAN_MODEL", None)
        if real_env is not None:
            os.environ["PRAMAN_MODEL"] = real_env

    # live check: on THIS machine (16GB RAM, qwen2.5:3b-instruct installed), the module-level
    # CHAT_MODEL computed at import time must land on the unchanged default -- the doc cache
    # keys include CHAT_MODEL, so an unintended change here would invalidate every cache entry.
    print(f"  live CHAT_MODEL on this machine = {ollama_client.CHAT_MODEL!r} "
          f"({ollama_client.CHAT_MODEL_REASON})")
    check("live CHAT_MODEL on this machine == 'qwen2.5:3b-instruct'",
          ollama_client.CHAT_MODEL == "qwen2.5:3b-instruct", ollama_client.CHAT_MODEL)


def main():
    test_corrections_roundtrip()
    test_corrections_fifo_cap()
    test_corrections_corrupt_file()
    test_classify_learned_fast_path()
    test_classify_learned_hint()
    test_classify_no_false_learned_hit()
    test_find_duplicates()
    test_chat_model_tiering()

    # clean up test artifacts
    for p in ["_test_corrections.json", "_test_corrections_fifo.json", "_test_corrections_corrupt.json"]:
        full = os.path.join(os.path.dirname(__file__), "..", "output", p)
        if os.path.exists(full):
            os.remove(full)

    print("\n" + "-" * 60)
    if problems:
        print(f"{len(problems)} CHECK(S) FAILED:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    else:
        print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
