#!/usr/bin/env python3
"""Generate a 10-K-scale fixture matching a real filing's structural profile.

Measured from NVDA's FY2025 10-K (nvda-20250126.htm) in a browser:

    2.08 MB HTML · 87 pages · 27 sections · ~1,130 paragraphs · 68 tables
    zero <p> tags — all div/span
    86 page breaks as `style="page-break-after:always"` on <hr>
    18,740 chars of inline-XBRL inside <div style="display:none"><ix:header>
    paragraph length: median 114, p90 763, one legitimate 3,751-char risk para

Reproducing that profile locally is what lets the Python parser be tested at
scale without depending on SEC being reachable — it blocks whole IP ranges.
"""
import os
import random

random.seed(20250126)
OUT = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures", "edgar",
                   "Archives", "edgar", "data", "1045810",
                   "000104581025000023", "nvda-20250126.htm")

SECTIONS = [
    ("Part I", 1), ("Item 1. Business", 2), ("Item 1A. Risk Factors", 2),
    ("Item 1B. Unresolved Staff Comments", 2), ("Item 1C. Cybersecurity", 2),
    ("Item 2. Properties", 2), ("Item 3. Legal Proceedings", 2),
    ("Item 4. Mine Safety Disclosures", 2), ("Part II", 1),
    ("Item 5. Market for Registrant's Common Equity", 2),
    ("Item 6. Reserved", 2),
    ("Item 7. Management's Discussion and Analysis", 2),
    ("Item 7A. Quantitative and Qualitative Disclosures About Market Risk", 2),
    ("Item 8. Financial Statements and Supplementary Data", 2),
    ("Item 9. Changes in and Disagreements with Accountants", 2),
    ("Item 9A. Controls and Procedures", 2), ("Part III", 1),
    ("Item 10. Directors, Executive Officers and Corporate Governance", 2),
    ("Item 11. Executive Compensation", 2),
    ("Item 12. Security Ownership of Certain Beneficial Owners", 2),
    ("Item 13. Certain Relationships and Related Transactions", 2),
    ("Item 14. Principal Accountant Fees and Services", 2), ("Part IV", 1),
    ("Item 15. Exhibits and Financial Statement Schedules", 2),
    ("Item 16. Form 10-K Summary", 2),
]

LEXICON = ("the Company data center revenue gross margin accelerated computing "
           "platform demand supply constraints export controls licensing "
           "customers concentration foundry capacity inventory obligations "
           "research development expense operating income deferred tax "
           "shareholders equity repurchase authorisation architecture software "
           "ecosystem developers automotive gaming visualization networking").split()


def sentence(words: int) -> str:
    body = " ".join(random.choice(LEXICON) for _ in range(words))
    return body[0].upper() + body[1:] + "."


def paragraph(target_chars: int) -> str:
    out = []
    while sum(len(s) + 1 for s in out) < target_chars:
        out.append(sentence(random.randint(9, 26)))
    return " ".join(out)


def money(lo: int, hi: int) -> str:
    return f"{random.randint(lo, hi):,}"


def table() -> str:
    rows = ["<tr><td>(in millions)</td><td>FY2025</td><td>FY2024</td></tr>"]
    for label in random.sample(["Data Center", "Gaming", "Professional Visualization",
                                "Automotive", "OEM and Other", "Total revenue",
                                "Cost of revenue", "Operating expenses"],
                               random.randint(3, 6)):
        rows.append(f"<tr><td>{label}</td><td>{money(400, 130000)}</td>"
                    f"<td>{money(300, 61000)}</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


def build() -> str:
    parts = ['<html><head><style>div{margin:0}</style></head><body>']

    # Inline-XBRL header: hidden machine metadata, ~18.7KB in the real filing.
    xbrl = " ".join(
        f"0001045810 2025 FY false P{random.randint(1,5)}Y "
        f"http://fasb.org/us-gaap/2024#{random.choice(LEXICON).title()}Member "
        f"2024-01-{random.randint(10,28)} 2025-01-26"
        for _ in range(210))
    parts.append(f'<div style="display:none"><ix:header><ix:hidden>{xbrl}'
                 f'</ix:hidden></ix:header></div>')

    section_at = {int(i * 86 / len(SECTIONS)) + 1: s for i, s in enumerate(SECTIONS)}
    paragraphs = 0

    for page in range(1, 88):
        if page in section_at:
            title, level = section_at[page]
            tag = "b" if level == 1 else "span"
            parts.append(f'<div><{tag}>{title}</{tag}></div>')

        for _ in range(random.randint(10, 17)):
            r = random.random()
            if r < 0.55:
                chars = random.randint(40, 200)      # median ~114
            elif r < 0.90:
                chars = random.randint(200, 800)     # p90 ~763
            elif r < 0.985:
                chars = random.randint(800, 2000)
            else:
                chars = random.randint(3000, 3800)   # the long risk paragraphs
            parts.append(f'<div><span>{paragraph(chars)}</span></div>')
            paragraphs += 1

        if random.random() < 0.78 and page > 3:
            parts.append(table())
        if page < 87:
            parts.append('<hr style="page-break-after:always">')

    parts.append("</body></html>")
    return "\n".join(parts)


if __name__ == "__main__":
    html = build()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write(html)
    print(f"{os.path.relpath(OUT)}  {len(html):,} bytes")
