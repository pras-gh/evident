# Retrieval: voyage-4-large against keyword search

Run on 2026-11-26 with `python tools/eval_retrieval.py -k 5 --pause 25` against
the 907 chunks then in PostgreSQL:

- Apple's, Microsoft's and Amazon's latest 10-Ks, fetched from EDGAR in full;
- NVIDIA's three 10-K excerpts from the demo corpus (risk factors only).

**Semantic** is `POST /v1/search`: the question is embedded with
`input_type=query`, then ranked by pgvector cosine (`<=>`, HNSW) within the
companies it names, then blended for recency. **Keyword** is PostgreSQL
full-text search (`ts_rank_cd`, any term) over the same companies. Parallx has
no BM25 or hybrid retrieval; this is the nearest keyword ranking.

## Summary

Checked by the tool:

- every hit's citation resolves to its paragraph (`GET /v1/evidence/{id}`):
  25 of 25;
- every hit's vector is `voyage/voyage-4-large` at 1024 dimensions;
- no test-provider (hashing) vector is in the table.

Relevance was judged by Claude reading each top five. It is a judgement, not a
labelled benchmark.

| query | semantic: relevant of top 5 | keyword: relevant of top 5 | note |
| --- | --- | --- | --- |
| Risks management identifies | 5 | 3 | both find risk passages; keyword also returns forward-looking-statement boilerplate |
| NVIDIA and data centre demand | 3–4 | 1 | keyword matches "personal data" transfers, and "centre" does not match "center"; NVIDIA's chunks here are risk-factor excerpts, so there is no MD&A to find |
| Microsoft and AI infrastructure spending | 4 | 2 | semantic finds the MD&A lines on AI infrastructure investment and margin |
| Amazon and AWS strategy | 1–2 | 0 | weak for both: the 10-K says little about AWS strategy as such |
| Apple and capital allocation | 1 (ranked first) | 0–1 | semantic ranks the share repurchase programme first; the dividend and repurchase table in Item 5 is not in its top five |

**A change this run caused.** Search used to embed the topic a model pulled
from the question ("capital allocation") rather than the question itself.
With voyage-4-large that found nothing on Apple's buybacks in the top five
(best similarity 0.21). The question ranks them first (0.41). Search now
embeds the question as asked, and the company stays the filter. The tables
below are from after that change.

## What risks does management identify in its latest annual filing?

Scope: every company · embedder voyage/voyage-4-large · citations resolved 5/5 · vectors voyage/voyage-4-large at [1024] dimensions · shared with keyword top 5: 0

| # | semantic (cosine) | keyword (full-text) |
| --- | --- | --- |
| 1 | **AAPL** 0.365 · p. 10 · Item 1A. Risk Factors<br>Due to the highly volatile and competitive nature of the markets and industries in which the Company competes, the Company must continually introduce … | **AAPL** 1.500 · p. 22 · Item 1C. Cybersecurity<br>The Company’s management, led by its Head of Corporate Information Security, has overall responsibility for identifying, assessing and managing any ma… |
| 2 | **AAPL** 0.365 · p. 15 · Item 1A. Risk Factors<br>While the Company maintains insurance coverage that is intended to address certain aspects of data security risks, it may be insufficient to cover all… | **MSFT** 1.400 · p. 29 · ITEM 1C. CY BERSECURITY<br>Our computing environments, products, and services are reviewed by our internal audit teams as well as independent third-party assessors. We are commi… |
| 3 | **AAPL** 0.360 · p. 9 · Item 1A. Risk Factors<br>Many of the Company’s operations, retail stores, data centers and other facilities, as well as critical business operations of the Company’s suppliers… | **AMZN** 1.200 · p. 17 · PART I<br>As part of our risk management process, we conduct application security assessments, vulnerability management, penetration testing, security audits, a… |
| 4 | **MSFT** 0.379 · p. 11 · Item 1<br>For a description of the risks we face related to regulatory matters, refer to Risk Factors (Part I, Item 1A of this Form 10-K). 11… | **AMZN** 1.100 · p. 20 · PART II<br>This Annual Report on Form 10-K includes forward-looking statements within the meaning of the Private Securities Litigation Reform Act of 1995. All st… |
| 5 | **MSFT** 0.359 · p. 26 · Item 1A<br>GENERAL RISKS If our reputation or our brands are damaged, our business and results of operations may be harmed . Our reputation and brands are global… | **MSFT** 1.000 · p. 49 · ITEM 7A. QUANTITATIVE AND QUALITAT IVE DISCLOSURES ABOUT MARKET RISK<br>RISKS We are exposed to economic risk from foreign exchange rates, interest rates, credit risk, and equity prices. We use derivatives instruments to m… |

## How has NVIDIA discussed data centre demand?

Scope: NVDA · embedder voyage/voyage-4-large · citations resolved 5/5 · vectors voyage/voyage-4-large at [1024] dimensions · shared with keyword top 5: 0

| # | semantic (cosine) | keyword (full-text) |
| --- | --- | --- |
| 1 | **NVDA** 0.439 · p. 16 · Item 1A. Risk Factors<br>Product transitions are complex and we often ship both new and prior architecture products simultaneously as our channel partners prepare to ship and … | **NVDA** 2.000 · p. 29 · Item 1A. Risk Factors<br>In the ordinary course of business, we may transfer personal data from Europe, China, and other jurisdictions to the United States or other countries.… |
| 2 | **NVDA** 0.417 · p. 28 · Item 1A. Risk Factors<br>Export controls restricting our ability to sell data center GPUs may also negatively impact demand for our networking products used in servers contain… | **NVDA** 2.000 · p. 30 · Item 1A. Risk Factors<br>In the ordinary course of business, we transfer personal data from Europe, China, and other jurisdictions to the United States or other countries. Cer… |
| 3 | **NVDA** 0.375 · p. 26 · Item 1A. Risk Factors<br>and financial results. Export controls targeting GPUs and semiconductors associated with AI have subjected and may in the future subject downstream us… | **NVDA** 1.900 · p. 31 · Item 1A. Risk Factors<br>transfer laws. For example, the GDPR governs the transfer of personal data to countries outside of the EEA. The European Commission released a set of … |
| 4 | **NVDA** 0.356 · p. 27 · Item 1A. Risk Factors<br>Open-source foundation models are rapidly growing in popularity with developers worldwide. Any regulatory control or other restriction that limits our… | **NVDA** 1.400 · p. 16 · Item 1A. Risk Factors<br>•government actions or changes in governmental policies, such as export controls or increased restrictions on gaming usage. Demand for our data center… |
| 5 | **NVDA** 0.349 · p. 25 · Item 1A. Risk Factors<br>we may face increased compliance costs as a result of changes or increases in antitrust legislation, regulation, administrative rule making, increased… | **NVDA** 0.600 · p. 13 · Item 1A. Risk Factors<br>•Product, system security and data protection incidents or breaches, as well as cyber-attacks could disrupt our operations and adversely affect our fi… |

## What has Microsoft said about AI infrastructure spending?

Scope: MSFT · embedder voyage/voyage-4-large · citations resolved 5/5 · vectors voyage/voyage-4-large at [1024] dimensions · shared with keyword top 5: 2

| # | semantic (cosine) | keyword (full-text) |
| --- | --- | --- |
| 1 | **MSFT** 0.481 · p. 15 · Item 1A<br>Our cloud and AI strategy requires substantial investments and depends on evolving customer demand, technological developments, competitive dynamics, … | **MSFT** 1.400 · p. 21 · Item 1A<br>Issues in the development, deployment, and use of AI may result in reputational or competitive harm or liability . We are providing access to AI acros… |
| 2 | **MSFT** 0.468 · p. 22 · Item 1A<br>manner inconsistent with our responsible AI policies and practices. These outcomes may give rise to public controversy, societal concerns, or regulato… | **MSFT** 0.900 · p. 7 · Item 1<br>Server Products and Cloud Services Azure is a comprehensive set of cloud services that offer developers, IT professionals, and enterprises freedom to … |
| 3 | **MSFT** 0.434 · p. 37 · Item 7<br>Microsoft Cloud gross margin percentage decreased to 66% driven by continued investments in AI infrastructure and growing AI product usage, offset in … | **MSFT** 0.900 · p. 22 · Item 1A<br>manner inconsistent with our responsible AI policies and practices. These outcomes may give rise to public controversy, societal concerns, or regulato… |
| 4 | **MSFT** 0.433 · p. 16 · Item 1A<br>Demand for cloud-based and AI products and services is evolving and difficult to forecast. Overestimation of demand or misalignment of capacity invest… | **MSFT** 0.900 · p. 15 · Item 1A<br>Our cloud and AI strategy requires substantial investments and depends on evolving customer demand, technological developments, competitive dynamics, … |
| 5 | **MSFT** 0.414 · p. 39 · Item 7<br>Operating income increased $14.1 billion or 20%. • Cost of revenue increased $2.6 billion or 12% driven by investments in AI infrastructure to support… | **MSFT** 0.800 · p. 16 · Item 1A<br>Our success depends on our ability to develop, deliver, and maintain competitive cloud-based and AI products and services that achieve broad customer … |

## What changes has Amazon disclosed in AWS strategy?

Scope: AMZN · embedder voyage/voyage-4-large · citations resolved 5/5 · vectors voyage/voyage-4-large at [1024] dimensions · shared with keyword top 5: 0

| # | semantic (cosine) | keyword (full-text) |
| --- | --- | --- |
| 1 | **AMZN** 0.432 · p. 20 · PART II<br>20 Table of Contents time since they are affected by several factors, including the mix of product sales, the mix of sales by third-party sellers, the… | **AMZN** 0.800 · p. 22 · PART II<br>Our effective tax rates could be affected by numerous factors, such as changes in our business operations, acquisitions, investments, entry into new b… |
| 2 | **AMZN** 0.375 · p. 23 · PART II<br>The sale of additional equity or convertible debt securities would be dilutive to our shareholders. In addition, we will, from time to time, consider … | **AMZN** 0.700 · p. 27 · PART II<br>Other income (expense), net was $(2.3) billion and $15.2 billion during 2024 and 2025. The primary components of other income (expense), net are relat… |
| 3 | **AMZN** 0.361 · p. 39 · PART II<br>Table of Contents AMAZON.COM, INC. CONSOLIDATED BALANCE SHEETS (in millions, except per share data) See accompanying notes to consolidated financial s… | **AMZN** 0.600 · p. 13 · PART I<br>• for foreign transactions, additional risks related to the integration of operations across different cultures and languages, and the economic, polit… |
| 4 | **AMZN** 0.353 · p. 41 · PART II<br>Effective January 1, 2025 we changed our estimate of the useful lives of a subset of our servers and networking equipment from six years to five years… | **AMZN** 0.500 · p. 16 · PART I<br>Our results of operations and cash flows could be adversely affected by additional taxes imposed on us prospectively or retroactively or additional ta… |
| 5 | **AMZN** 0.343 · p. 67 · PART II<br>Changes in tax laws, regulations, administrative practices, principles, and interpretations may impact our tax contingencies. Due to various factors, … | **AMZN** 0.500 · p. 14 · PART I<br>• conditions or trends in the internet, other technologies including artificial intelligence, and the industry segments we operate in; • quarterly var… |

## What has Apple said about capital allocation?

Scope: AAPL · embedder voyage/voyage-4-large · citations resolved 5/5 · vectors voyage/voyage-4-large at [1024] dimensions · shared with keyword top 5: 1

| # | semantic (cosine) | keyword (full-text) |
| --- | --- | --- |
| 1 | **AAPL** 0.405 · p. 30 · Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations<br>In addition to its contractual cash requirements, the Company has authorized share repurchase programs. The programs do not obligate the Company to ac… | **AAPL** 0.500 · p. 40 · Item 8. Financial Statements and Supplementary Data<br>The Company has identified the performance obligations regularly included in arrangements involving the sale of iPhone, Mac and iPad. The first materi… |
| 2 | **AAPL** 0.357 · p. 13 · Item 1A. Risk Factors<br>The Company believes that its distinctive and inclusive culture is a significant driver of its success. If the Company is unable to nurture its cultur… | **AAPL** 0.200 · p. 30 · Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations<br>The Company also issues unsecured short-term promissory notes pursuant to a commercial paper program. As of September 26, 2026, the Company had $2.0 b… |
| 3 | **AAPL** 0.352 · p. 11 · Item 1A. Risk Factors<br>Apple Inc. / 2026 Form 10-K / 9 security of a user’s device. This risk increases as artificial intelligence technologies are used to produce highly ta… | **AAPL** 0.200 · p. 11 · Item 1A. Risk Factors<br>Apple Inc. / 2026 Form 10-K / 9 security of a user’s device. This risk increases as artificial intelligence technologies are used to produce highly ta… |
| 4 | **AAPL** 0.352 · p. 5 · Item 1. Business<br>Apple Inc. / 2026 Form 10-K / 3 The Company’s business, including its artificial intelligence and machine learning offerings, also depends on access t… | **AAPL** 0.200 · p. 12 · Item 1A. Risk Factors<br>The Company records a write-down for product and component inventories if cost exceeds net realizable value. The Company reviews other assets, includi… |
| 5 | **AAPL** 0.350 · p. 17 · Item 1A. Risk Factors<br>Any of the foregoing could materially adversely affect the Company’s business, reputation, results of operations, financial condition and stock price.… | **AAPL** 0.200 · p. 30 · Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations<br>The Company’s effective tax rate for 2026 was higher compared to 2025 primarily due to a higher effective tax rate on foreign earnings, the impact of … |

