"""Offline self-test for gap_report.py -- no Ollama needed.

Builds a small synthetic set of "decisions" (the shape run.py/app.py produce) against
the REAL autonomous NAAC pack (loaded the normal way via pack.load_metrics), and checks
the counting logic: strong vs tentative evidence, coverage math, missing lists, and that
review/unreadable docs are excluded from evidence everywhere.

Run: python src/test_gap_report.py   (from the D:\\praman project root)
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(__file__))
from pack import load_metrics  # noqa: E402
from gap_report import build_gap_report, format_gap_report_text, format_summary_card_text  # noqa: E402

PACK_PATH = os.path.join(os.path.dirname(__file__), "..", "criteria", "naac_autonomous_raf.yaml")


def _metric(metrics, mid):
    row = next(m for m in metrics if m["id"] == mid)
    return row


def main():
    problems = []

    metrics, pack_name = load_metrics(PACK_PATH)
    print(f"Loaded pack: {pack_name} ({len(metrics)} metrics)")

    m_511 = _metric(metrics, "5.1.1")
    m_331 = _metric(metrics, "3.3.1")

    # 3 strong (metric-level) commits on 5.1.1
    decisions = [
        {"filename": "scholarship_list_1.txt", "status": "auto", "commit_level": "metric", "chosen": m_511},
        {"filename": "scholarship_list_2.txt", "status": "auto", "commit_level": "metric", "chosen": m_511},
        {"filename": "scholarship_list_3.txt", "status": "auto", "commit_level": "metric", "chosen": m_511},
        # 1 tentative (criterion-only) commit that best-guessed 3.3.1
        {"filename": "research_note.txt", "status": "auto", "commit_level": "criterion", "chosen": m_331},
        # 2 in review -- must NOT count as evidence anywhere
        {"filename": "unsure_1.txt", "status": "review", "commit_level": None, "chosen": m_511},
        {"filename": "unsure_2.txt", "status": "review", "commit_level": None, "chosen": m_331},
        # 1 unreadable -- must NOT count as evidence anywhere
        {"filename": "corrupt.pdf", "status": "unreadable", "unreadable": True, "commit_level": None, "chosen": None},
    ]

    report = build_gap_report(decisions, metrics)
    ov = report["overall"]

    # ---- overall counters ----
    if ov["docs_scanned"] != 7:
        problems.append(f"docs_scanned: expected 7, got {ov['docs_scanned']}")
    if ov["committed"] != 4:
        problems.append(f"committed: expected 4 (3 strong + 1 tentative), got {ov['committed']}")
    if ov["in_review"] != 2:
        problems.append(f"in_review: expected 2, got {ov['in_review']}")
    if ov["unreadable"] != 1:
        problems.append(f"unreadable: expected 1, got {ov['unreadable']}")
    if ov["metric_commits"] != 3:
        problems.append(f"metric_commits: expected 3, got {ov['metric_commits']}")
    if ov["criterion_only_commits"] != 1:
        problems.append(f"criterion_only_commits: expected 1, got {ov['criterion_only_commits']}")

    # ---- per-metric counts ----
    crit5 = next(c for c in report["criteria"] if c["id"] == "5")
    row_511 = next(r for ki in crit5["kis"] for r in ki["metrics"] if r["id"] == "5.1.1")
    if row_511["evidence_count"] != 3:
        problems.append(f"5.1.1 evidence_count: expected 3, got {row_511['evidence_count']}")
    if row_511["tentative_count"] != 0:
        problems.append(f"5.1.1 tentative_count: expected 0, got {row_511['tentative_count']}")
    # the 2 "review" docs that guessed 5.1.1/3.3.1 must NOT have bumped either counter
    total_evidence_on_511 = row_511["evidence_count"]
    if total_evidence_on_511 != 3:
        problems.append("a 'review' status decision leaked into evidence_count for 5.1.1")

    crit3 = next(c for c in report["criteria"] if c["id"] == "3")
    row_331 = next(r for ki in crit3["kis"] for r in ki["metrics"] if r["id"] == "3.3.1")
    if row_331["evidence_count"] != 0:
        problems.append(f"3.3.1 evidence_count: expected 0 (only tentative), got {row_331['evidence_count']}")
    if row_331["tentative_count"] != 1:
        problems.append(f"3.3.1 tentative_count: expected 1, got {row_331['tentative_count']}")

    # ---- coverage math: criterion 5 has exactly one metric with strong evidence (5.1.1);
    # every other metric in criterion 5 got zero decisions and must show as MISSING ----
    strong_in_5 = crit5["metrics_strong"]
    if strong_in_5 != 1:
        problems.append(f"criterion 5 metrics_strong: expected 1, got {strong_in_5}")
    expected_cov = round(100.0 * 1 / crit5["total_metrics"], 1)
    if crit5["coverage_pct"] != expected_cov:
        problems.append(f"criterion 5 coverage_pct: expected {expected_cov}, got {crit5['coverage_pct']}")

    # criterion 3 must show 3.3.1 as tentative-only, not missing, not strong
    if crit3["metrics_strong"] != 0:
        problems.append(f"criterion 3 metrics_strong: expected 0, got {crit3['metrics_strong']}")
    if crit3["metrics_tentative_only"] < 1:
        problems.append("criterion 3 should have at least one tentative-only metric (3.3.1)")

    # ---- a metric nobody touched at all must land in MISSING, never in strong/tentative ----
    untouched = next(
        (r for ki in crit5["kis"] for r in ki["metrics"] if r["id"] != "5.1.1"), None
    )
    if untouched is None:
        problems.append("expected at least one other metric in criterion 5 to sanity-check MISSING")
    elif untouched["evidence_count"] != 0 or untouched["tentative_count"] != 0:
        problems.append(f"untouched metric {untouched['id']} should be MISSING (0/0), "
                         f"got evidence={untouched['evidence_count']} tentative={untouched['tentative_count']}")

    # ---- every metric in the whole report must be accounted for exactly once ----
    for crit in report["criteria"]:
        total = crit["metrics_strong"] + crit["metrics_tentative_only"] + crit["metrics_missing"]
        if total != crit["total_metrics"]:
            problems.append(
                f"criterion {crit['id']}: strong+tentative+missing ({total}) != total_metrics "
                f"({crit['total_metrics']})"
            )

    # ---- text formatters must run without exceptions and mention the key facts ----
    gap_text = format_gap_report_text(report, pack_name)
    summary_text = format_summary_card_text(report, pack_name, college_name="Test College")

    if "5.1.1" not in gap_text and "3.3.1" not in gap_text:
        problems.append("gap report text doesn't mention any of the tested metric ids")
    if "MISSING" not in gap_text:
        problems.append("gap report text missing the 'MISSING' section header")
    if "TENTATIVE" not in gap_text:
        problems.append("gap report text missing the 'TENTATIVE' section header")
    if "WHAT TO DO NEXT" not in gap_text:
        problems.append("gap report text missing the 'WHAT TO DO NEXT' block")
    if "Test College" not in summary_text:
        problems.append("summary card text doesn't include the college name")
    if "Strongest area" not in summary_text:
        problems.append("summary card text missing the one-line verdict")

    # ---- empty-decisions edge case must not crash and must show everything as missing ----
    empty_report = build_gap_report([], metrics)
    if empty_report["overall"]["docs_scanned"] != 0:
        problems.append("empty decisions: docs_scanned should be 0")
    if any(c["metrics_strong"] != 0 for c in empty_report["criteria"]):
        problems.append("empty decisions: no criterion should have any strong evidence")
    format_gap_report_text(empty_report, pack_name)  # must not raise
    format_summary_card_text(empty_report, pack_name)  # must not raise

    print("\n" + "=" * 60)
    if problems:
        print(f"{len(problems)} PROBLEM(S) FOUND:")
        for p in problems:
            print(" -", p)
        sys.exit(1)
    else:
        print("gap_report.py self-test: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
