# Tamil documents — what Saandru actually does today (measured, not promised)

Real Tamil Nadu colleges keep some evidence in Tamil (scholarship lists, notices,
circulars). This is a measurement of Saandru's CURRENT behaviour on Tamil text, run
on 07 July 2026 against the `naac_autonomous_raf.yaml` pack, using the installed
`nomic-embed-text` embedding model. No code was changed for this experiment.

## 1. OCR (Tesseract) language support

- `tesseract --list-langs` (binary at `C:\Program Files\Tesseract-OCR\tesseract.exe`)
  reports only **`eng`** and `osd` installed. **`tam` (Tamil) is NOT installed.**
- Effect: a *scanned/photographed* Tamil document (image-only PDF, JPEG of a
  notice) currently goes through OCR with the English model, which will not
  produce usable Tamil text -- it will read as garbage or near-empty, and
  `ingest.py`'s own safeguards will likely mark it `[NEEDS OCR: ...]` or
  `[EMPTY DOCUMENT: ...]`, landing it safely in human review rather than being
  silently misfiled.
- **Official fix (not installed by this experiment):** download `tam.traineddata`
  from `https://github.com/tesseract-ocr/tessdata` and place it in Tesseract's
  `tessdata` folder (`C:\Program Files\Tesseract-OCR\tessdata\`). This only helps
  *scanned/image* Tamil documents -- it does nothing for the classification step
  below, which is a separate (embedding) problem.

## 2. Classification test: 3 pure-Tamil docs + 1 mixed doc

Four short evidence-like `.txt` files (already-digital text, so OCR is not a
factor here -- this isolates the embedding/classification step) were classified
against the autonomous pack (106 metrics), `pack_name=None` so no corrections
memory could help or hurt the result:

| File | Real topic | Expected metric | Saandru's result | Status | Confidence |
|---|---|---|---|---|---|
| `1_scholarship.txt` (pure Tamil) | scholarship disbursement list | 5.1.1 | **3.5.1 (WRONG)** | auto (committed) | 0.97 |
| `2_green_audit.txt` (pure Tamil) | green/energy/water audit | 7.1.x | **3.5.1 (WRONG)** | auto (committed) | 0.81 |
| `3_mou_note.txt` (pure Tamil) | industry MoU | 3.4.x | **3.7.2 (WRONG)** | auto (committed) | 0.97 |
| `4_mixed_scholarship.txt` (Tamil body + English heading `5.1.1 Scholarship disbursement 2023-24`) | scholarship disbursement list | 5.1.1 | **5.1.1 (CORRECT)** | auto (committed) | 0.97 |

## 3. Honest finding -- this is worse than "safely abstains"

The original assumption going into this test was "pure Tamil docs probably
abstain safely (land in `_NEEDS_REVIEW`)." **That assumption was wrong.** All
three pure-Tamil documents were classified with HIGH confidence (0.81-0.97) and
committed automatically (`status=auto`) -- but to the WRONG metric, and all three
landed near the same narrow cluster (3.5.x/3.7.x) regardless of their real
content. `nomic-embed-text` clearly cannot tell Tamil documents apart from each
other; it just returns *some* confident-looking nearest neighbour, and Saandru's
confidence gate does not know the embedding itself is meaningless here.

The one case that worked correctly is the realistic one for most real folders:
a document with an **English metric heading or filename** plus a Tamil body.
There, the English heading alone carried enough signal for a correct,
high-confidence match -- the Tamil body did not hurt.

## 4. What works today vs. what pure-Tamil support would need

**Works today (safe to rely on):**
- English-headed or English-filenamed documents with a Tamil body classify
  correctly, because the classifier reads the whole extracted text and English
  headings dominate the embedding.
- Purely non-Tamil documents are unaffected by any of this.

**Does NOT work today, and must not be claimed as working:**
- Pure-Tamil documents (no English heading/filename cue) get **miscommitted with
  false confidence**, not safely deferred to a human. This is a real risk for a
  college that files Tamil-only scholarship lists, notices, or minutes without an
  English heading.
- Scanned/photographed Tamil documents cannot be read at all yet (`tam.traineddata`
  missing).

**What real pure-Tamil support would need (not built, scope for later):**
1. `tam.traineddata` installed for Tesseract, so scanned Tamil documents produce
   real extracted text instead of garbage/empty.
2. A multilingual (or Tamil-capable) embedding model swapped in for
   `nomic-embed-text` for Tamil documents specifically -- `nomic-embed-text` is
   English-centric and this test shows it cannot discriminate Tamil content.
   Options to evaluate later: a multilingual Ollama-compatible embedding model,
   or translating extracted Tamil text to English before embedding (adds an LLM
   call per document, changes the "zero-AI-cost embedding" cost profile).
3. Re-running the accuracy benchmark (`benchmarks/baseline.json`) with genuine
   Tamil ground-truth documents once (1)+(2) exist, before claiming any Tamil
   accuracy number.

## Recommendation

Do not market or promise Tamil support yet. Short-term, safest mitigation that
needs NO new dependency: lower the auto-commit confidence gate specifically for
documents where the extracted text is mostly non-Latin-script (a cheap
character-range check), forcing them to `_NEEDS_REVIEW` instead of auto-filing on
an untrustworthy embedding. That is a scoping decision for a future task, not
implemented here (this task was measurement/report only, per instructions).


---

## UPDATE (same day): mitigation IMPLEMENTED — non-Latin-script gate

`pipeline.classify()` now measures the share of Latin letters in the text it is about
to classify. Below 50%, it refuses to classify at all: the document goes straight to
the human-review bucket with reason `non_english_text` — no embedding, no votes, no
vector stored in the corrections memory (Tamil vectors would false-hit each other).

This turns the dangerous failure ("confidently filed to the WRONG metric at 0.81–0.97")
into the honest one ("I cannot read this language reliably — please check it").
Even Tamil-body documents with an English heading go to review, deliberately: the model
can only read the heading and cannot verify the body says what the heading claims.

Verified: 3/3 pure-Tamil docs + 1 mixed doc → review with 0 model calls; English prose
and pure number-tables are untouched by the gate. Full Tamil support (tam OCR pack +
multilingual embedder) remains a V3 decision.
