# Praman — local, private accreditation evidence sorter

**Praman** (प्रमाण, "evidence") is a free, open-source tool that helps small colleges organise their
accreditation evidence. Point it at a folder of documents; it reads each file, matches it to the right
accreditation criterion/metric, extracts key details, builds an evidence index (Excel), and flags
anything it is unsure about for a human to check.

## Why it is different
- **100% local & offline.** Runs on a normal office PC (8–16 GB RAM) with a small local model via
  [Ollama](https://ollama.com). No cloud, no API keys, no subscription. Your documents never leave your computer.
- **Zero integration.** No ERP, no portal, no data entry. It works on the messy folder you already have.
- **Calibrated oversight.** You choose how much the tool does alone:
  - **L1 — Review everything:** it proposes, you approve each file.
  - **L2 — Review the unsure:** it files confident matches, queues low-confidence ones for you.
  - **L3 — Autonomous:** it files everything and gives you a summary + full audit log.
- **Explainable.** Every classification cites the line in the document that justified it.
- **Auditable.** Every action is logged: what was decided, why, with what confidence, who approved.

## Status
Early development (Phase 0/1). Current contents:
- `criteria/naac_affiliated_raf2021.yaml` — NAAC criteria pack (Affiliated/Constituent UG & PG
  Colleges, RAF), auto-parsed from the official manual. Metric QnM/QlM types are heuristic and
  being hand-verified (`verify_type: true`).
- `src/build_naac_pack.py` — the pack builder (official manual PDF → YAML).
- NBA (Tier-II UG Engineering) pack: planned next.
- Criteria packs are swappable YAML — when NAAC's Binary/MBGL framework goes live, it becomes a new pack.

## Planned architecture (v1)
folder scan → text extraction (pdfplumber/docx/openpyxl/OCR) → embedding shortlist (top-3 candidate
metrics) → small LLM adjudication (constrained JSON, self-consistency) → confidence routing per
oversight level → evidence index (Excel) + gap report + audit log.

## How to run the app
Praman has a simple point-and-click screen for office staff (no command-line steps after this):

1. Make sure [Ollama](https://ollama.com) is installed and running on this PC.
2. Open a terminal in the `praman` folder and install the app's requirements once:
   ```
   pip install streamlit
   ```
3. Start the app:
   ```
   streamlit run app.py
   ```
4. Your web browser opens automatically (usually at `http://localhost:8501`). Follow the 3 steps on
   screen: pick your documents folder, choose NAAC or NBA, choose how much the assistant should do on
   its own, then press **Start sorting**.
5. When it is done, check the 🟡 "Please check these" tab if there is anything to review, then press
   **Download Excel index** to get your evidence index file.

Everything runs on this computer only — no document ever leaves it.

## License
MIT (planned). Official accreditation manuals are © their issuing bodies (NAAC/NBA) and are **not**
redistributed here — see `reference/SOURCES.md` for where to download them.
