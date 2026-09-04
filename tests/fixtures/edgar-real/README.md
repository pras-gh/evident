# Real filing excerpt

Verbatim paragraphs from NVIDIA's FY2025 Form 10-K, accession
`0001045810-25-000023`, read from `www.sec.gov`. SEC filings are public domain.

This exists because the sibling `edgar/` fixture is **synthetic** — it has real
inline-XBRL structure and word-salad prose, which is right for testing the
parser and useless for testing extraction. Running a language model over
"Accelerated concentration gross concentration supply constraints" tells you
nothing about whether extraction works.

Two sections, five paragraphs, chosen because they carry the things the
taxonomy is supposed to find: a product (Blackwell), a strategy (accelerated
computing), a geography (China), a risk (export licensing), and other companies.

Not a whole filing. Enough to prove the intelligence layer on real text without
sending a few hundred chunks to an API.
