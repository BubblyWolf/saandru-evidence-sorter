# Saandru -- Copyright (C) 2026 Chitranjan Jegadeesan.
# Licensed under the GNU Affero General Public License v3.0 or later; see LICENSE.
"""Generate a small LABELED test corpus of realistic (fake) accreditation evidence documents.
Each doc maps to a known NAAC metric id -> lets us MEASURE the tool's accuracy.
No real college/student data (privacy-safe). Mix of .txt and .docx to test both readers.
"""
import os, json, sys
from docx import Document

OUT = sys.argv[1] if len(sys.argv) > 1 else r"D:\praman\samples\mock"
os.makedirs(OUT, exist_ok=True)

# (filename, true_criterion, true_ki, true_metric_hint, format, title, body)
DOCS = [
    ("feedback_analysis_2024.txt", "1", "1.4", "1.4.1", "txt",
     "Curriculum Feedback Analysis Report 2023-24",
     "The Internal Quality Assurance Cell collected structured feedback on the syllabus and its "
     "transaction from students, teachers, alumni and employers. A total of 812 responses were "
     "analysed. 78% rated the curriculum relevance as good or excellent. Action taken report on "
     "syllabus gaps was forwarded to the affiliating university's Board of Studies."),

    ("addon_certificate_python.docx", "1", "1.2", "1.2.2", "docx",
     "Add-on Certificate Course Completion - Python Programming",
     "This certifies completion of the 45-hour Add-on Certificate Course on Python Programming "
     "offered by the Department of Computer Science during the academic year 2023-24. "
     "Number of students enrolled: 60. Number completed: 57. Course coordinator: Head of Department."),

    ("experiential_project_report.txt", "2", "2.3", "2.3.1", "txt",
     "Report on Student-Centric Experiential Learning",
     "The Department adopted project-based and experiential learning methods. Final-year students "
     "completed industry mini-projects and field work. Participative learning, problem-solving "
     "assignments and internships were integrated into the teaching-learning process this year."),

    ("result_analysis_2024.docx", "2", "2.6", "2.6.3", "docx",
     "University Examination Result Analysis 2023-24",
     "Programme-wise pass percentage for the final year: B.Sc 91%, B.Com 88%, BCA 84%. "
     "The result analysis of the university examinations shows an overall improvement of 6% over "
     "the previous year. Student outcome attainment was reviewed by the examination committee."),

    ("mou_industry_research.txt", "3", "3.3", "3.3.1", "txt",
     "Memorandum of Understanding for Research Collaboration",
     "A Memorandum of Understanding (MoU) was signed between the college and Zentech Solutions "
     "Pvt Ltd for collaborative research, consultancy and student internships. The MoU is valid "
     "for three years and supports joint research innovation and extension activities."),

    ("nss_extension_camp.docx", "3", "3.4", "3.4.2", "docx",
     "NSS Special Camp - Village Outreach Report",
     "The National Service Scheme unit organised a seven-day special camp in Kovilpatti village. "
     "Extension activities included a health awareness rally, cleanliness drive and blood donation "
     "camp, promoting institutional social responsibility and community engagement."),

    ("infrastructure_lab_purchase.txt", "4", "4.1", "4.1.1", "txt",
     "New Computer Laboratory - Infrastructure Augmentation",
     "The management sanctioned the purchase of 40 new desktop computers and networking equipment "
     "for the new computer laboratory. This augments the physical and IT infrastructure and "
     "learning resources available for teaching, learning and practical work."),

    ("library_eresources.docx", "4", "4.2", "4.2.2", "docx",
     "Library e-Resources Subscription Report",
     "The central library subscribed to N-LIST INFLIBNET and DELNET providing access to e-journals "
     "and e-books. Total library resources include 24,500 titles. Annual expenditure on the "
     "purchase of books, journals and e-resources was documented for the year."),

    ("scholarship_disbursement.txt", "5", "5.1", "5.1.1", "txt",
     "Scholarship and Financial Support Disbursement List",
     "Student support: 214 students received government and institutional scholarships and freeships "
     "during 2023-24. Capability enhancement and financial assistance schemes, including SC/ST "
     "post-matric scholarships, were disbursed as per the enclosed beneficiary list."),

    ("placement_record_2024.docx", "5", "5.2", "5.2.1", "docx",
     "Campus Placement Record 2023-24",
     "Student progression: 138 final-year students were placed through campus recruitment. "
     "Recruiters included Infosys, TCS and Zoho. The average package was 4.2 LPA. Details of "
     "students placed, employers and offer letters are enclosed for the placement cell records."),

    ("iqac_meeting_minutes.txt", "6", "6.1", "6.1.1", "txt",
     "IQAC Meeting Minutes",
     "Minutes of the Internal Quality Assurance Cell meeting on institutional governance and "
     "leadership. The Principal reviewed strategic plan deployment, decentralisation and "
     "participative management. Perspective plan and e-governance in administration were discussed."),

    ("egovernance_erp.docx", "6", "6.2", "6.2.3", "docx",
     "e-Governance Implementation in Administration",
     "The college implemented an ERP system covering administration, admission, finance and "
     "examination. e-Governance areas include planning and development, student admission and "
     "support, and the accounts module, improving institutional governance and management."),

    ("green_audit_report.txt", "7", "7.1", "7.1.5", "txt",
     "Green Audit and Energy Conservation Report",
     "The green audit documented institutional environmental initiatives: solar panels, rainwater "
     "harvesting, waste management and a plastic-free campus. Energy conservation and environmental "
     "consciousness measures support institutional values and sustainability."),

    ("gender_sensitization.docx", "7", "7.1", "7.1.1", "docx",
     "Gender Sensitization Programme Report",
     "A gender sensitization and equity programme was conducted for students and staff. Activities "
     "addressed gender equity, safety and human values. The Women's Cell organised awareness "
     "sessions on institutional values and professional ethics."),

    # a deliberately AMBIGUOUS/weak doc -> should land in the low-confidence review queue
    ("misc_circular.txt", "?", "?", "?", "txt",
     "General Office Circular",
     "All heads of departments are informed that the office will remain closed on Friday on account "
     "of local holiday. Kindly inform students accordingly. Pending files may be submitted on Monday."),
]

manifest = []
for fn, crit, ki, metric, fmt, title, body in DOCS:
    path = os.path.join(OUT, fn)
    if fmt == "txt":
        with open(path, "w", encoding="utf-8") as f:
            f.write(title + "\n\n" + body + "\n")
    else:
        doc = Document()
        doc.add_heading(title, level=1)
        doc.add_paragraph(body)
        doc.save(path)
    manifest.append({"file": fn, "true_criterion": crit, "true_ki": ki, "true_metric": metric})

with open(os.path.join(OUT, "_ground_truth.json"), "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print(f"wrote {len(DOCS)} mock docs to {OUT}")
print(f"  formats: {sum(1 for d in DOCS if d[4]=='txt')} txt, {sum(1 for d in DOCS if d[4]=='docx')} docx")
print(f"  1 deliberately ambiguous (should go to review queue)")
