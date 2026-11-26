"""Financial statements from XBRL: what Parallx keeps of the Company Facts API.

A company's Company Facts payload holds every value it ever tagged, each as
filed: the same quarter's revenue appears in the 10-Q that reported it, again
as a comparative in next year's 10-Q, and again in the 10-K. EDGAR picks one
of them per calendar period — the one filed last — and labels it with a
`frame` (CY2025 for a year, CY2025Q3 for a quarter, CY2025Q3I for a balance
at a quarter's end). Keeping only framed facts gives one value per period,
restatements included, without re-deriving EDGAR's choice.

Kept: the statement lines in `METRICS`, from periodic reports (10-K, 10-Q
and their foreign and amended forms). A value is cited to the filing it came
from and its XBRL concept, not to a paragraph — XBRL tags a number, not a
sentence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterator


@dataclass(frozen=True, slots=True)
class Metric:
    key: str
    label: str
    #: XBRL concepts, preferred first: companies tag one line differently, and
    #: change tags over the years (SalesRevenueNet gave way to
    #: RevenueFromContractWithCustomer... in 2018)
    concepts: tuple[str, ...]
    unit: str
    #: a balance at a date (instant), or a flow over a period
    instant: bool = False
    #: a quarter can be had as the year less its first three
    additive: bool = True
    #: dated on the report's cover, weeks after the period ends (shares
    #: outstanding), so matched to the first date after it
    cover: bool = False


METRICS: tuple[Metric, ...] = (
    Metric("revenue", "Revenue", (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "us-gaap:Revenues",
        "us-gaap:SalesRevenueNet", "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    ), "USD"),
    Metric("cost_of_revenue", "Cost of revenue", (
        "us-gaap:CostOfRevenue", "us-gaap:CostOfGoodsAndServicesSold",
    ), "USD"),
    Metric("gross_profit", "Gross profit", ("us-gaap:GrossProfit",), "USD"),
    Metric("rd_expense", "R&D expense", ("us-gaap:ResearchAndDevelopmentExpense",), "USD"),
    Metric("operating_income", "Operating income", ("us-gaap:OperatingIncomeLoss",), "USD"),
    Metric("net_income", "Net income", ("us-gaap:NetIncomeLoss",), "USD"),
    Metric("eps_diluted", "Diluted EPS", ("us-gaap:EarningsPerShareDiluted",), "USD/shares",
           additive=False),
    Metric("operating_cash_flow", "Operating cash flow", (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",), "USD"),
    Metric("capex", "Capital expenditure", (
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",), "USD"),
    Metric("cash", "Cash and equivalents", (
        "us-gaap:CashAndCashEquivalentsAtCarryingValue",), "USD", instant=True),
    Metric("total_assets", "Total assets", ("us-gaap:Assets",), "USD", instant=True),
    Metric("total_liabilities", "Total liabilities", ("us-gaap:Liabilities",), "USD",
           instant=True),
    Metric("equity", "Shareholders' equity", ("us-gaap:StockholdersEquity",), "USD",
           instant=True),
    Metric("long_term_debt", "Long-term debt", (
        "us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebt"), "USD", instant=True),
    Metric("shares_outstanding", "Shares outstanding", (
        "dei:EntityCommonStockSharesOutstanding",), "shares", instant=True, additive=False,
        cover=True),
)

BY_KEY = {m.key: m for m in METRICS}
CONCEPTS = {c: m for m in METRICS for c in m.concepts}
#: forms whose facts are kept: periodic reports, not registration statements
#: or 8-K exhibits, whose numbers are previews of these
PERIODIC = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A",
                      "10-KT", "10-QT"})


@dataclass(frozen=True, slots=True)
class Fact:
    concept: str            # "us-gaap:Revenues"
    unit: str
    period_start: date | None
    period_end: date
    value: Decimal
    accession: str
    form: str
    filed: date
    fiscal_year: int | None
    fiscal_period: str | None
    frame: str


def facts(payload: dict[str, Any]) -> Iterator[Fact]:
    """The framed facts of `METRICS`' concepts, from periodic reports."""
    for taxonomy, concepts in (payload.get("facts") or {}).items():
        for name, body in concepts.items():
            concept = f"{taxonomy}:{name}"
            metric = CONCEPTS.get(concept)
            if metric is None:
                continue
            for unit, rows in (body.get("units") or {}).items():
                if unit != metric.unit:
                    continue
                for r in rows:
                    frame = r.get("frame")
                    if not frame or r.get("form") not in PERIODIC or r.get("val") is None:
                        continue
                    yield Fact(
                        concept=concept, unit=unit,
                        period_start=date.fromisoformat(r["start"]) if r.get("start") else None,
                        period_end=date.fromisoformat(r["end"]),
                        value=Decimal(str(r["val"])), accession=r["accn"], form=r["form"],
                        filed=date.fromisoformat(r["filed"]),
                        fiscal_year=r.get("fy"), fiscal_period=r.get("fp"), frame=frame)


# ------------------------------------------------------------- statements


_YEAR = re.compile(r"^CY\d{4}$")
_QUARTER = re.compile(r"^CY\d{4}Q\d$")
_INSTANT = re.compile(r"^CY\d{4}Q\dI$")
#: the lines whose periods make a statement's columns
_ANCHORS = ("revenue", "net_income", "operating_income")


@dataclass(frozen=True, slots=True)
class Cell:
    value: Decimal
    concept: str
    accession: str
    form: str
    filed: date
    #: a fiscal Q4: the year less its first three quarters, not a filed number
    derived: bool = False


@dataclass(frozen=True, slots=True)
class Column:
    label: str              # "FY2026", "Q3 FY2026"
    period_start: date | None
    period_end: date


@dataclass(slots=True)
class Statement:
    period: str             # "annual" | "quarterly"
    columns: list[Column]
    #: metric key → a cell per column, None where nothing was filed
    rows: dict[str, list[Cell | None]]


def _label(f, end: date, *, quarter: str | None = None) -> str:
    """The company's own name for a period, from the report that filed it
    (a 10-K's `fy`), or the calendar when it came from a later filing's
    comparatives — `fy`/`fp` describe the filing, not the fact."""
    original = f is not None and abs((f.filed - end).days) <= 120
    if quarter:
        if quarter == "Q4":
            return f"Q4 FY{f.fiscal_year if original and f.fiscal_year else end.year}"
        if original and f.fiscal_period in ("Q1", "Q2", "Q3") and f.fiscal_year:
            return f"{f.fiscal_period} FY{f.fiscal_year}"
        return end.strftime("%b %Y")
    return f"FY{f.fiscal_year if original and f.fiscal_year else end.year}"


def statement(rows: list, *, period: str = "annual", periods: int = 5) -> Statement:
    """The latest `periods` fiscal years or quarters, a row per metric.
    `rows` are stored facts: anything with Fact's attributes."""
    flows = _YEAR if period == "annual" else _QUARTER
    by_concept: dict[str, list] = {}
    for r in rows:
        by_concept.setdefault(r.concept, []).append(r)

    def pick(metric: Metric, test) -> object | None:
        for c in metric.concepts:
            hit = [r for r in by_concept.get(c, []) if test(r)]
            if hit:
                return max(hit, key=lambda r: r.filed)
        return None

    def years(metric: Metric) -> list:
        return [r for c in metric.concepts for r in by_concept.get(c, []) if _YEAR.match(r.frame)]

    # columns: the periods the anchor lines were filed for — and, quarterly,
    # the last quarter of each fiscal year with quarters filed, which only
    # the 10-K covers (as the year)
    ends: dict[date, object] = {}
    for key in _ANCHORS:
        m = BY_KEY[key]
        quarters = []
        for c in m.concepts:
            for r in by_concept.get(c, []):
                if flows.match(r.frame):
                    ends.setdefault(r.period_end, r)
                    quarters.append(r)
        if period == "quarterly":
            for y in years(m):
                if y.period_start and any(q.period_start and q.period_start >= y.period_start
                                          and q.period_end < y.period_end for q in quarters):
                    ends.setdefault(y.period_end, y)
    if not ends:
        return Statement(period, [], {m.key: [] for m in METRICS})
    chosen = sorted(ends)[-periods:]

    annual_ends = {r.period_end: r for key in _ANCHORS for r in years(BY_KEY[key])}
    columns = []
    for end in chosen:
        anchor = ends[end]
        is_q4 = period == "quarterly" and not _QUARTER.match(anchor.frame)
        if period == "annual":
            label = _label(anchor, end)
        elif is_q4:
            label = _label(annual_ends.get(end, anchor), end, quarter="Q4")
        else:
            label = _label(anchor, end, quarter=anchor.fiscal_period or "Q")
        start = anchor.period_start
        if is_q4:
            start = None    # the quarter's own start is not filed
        columns.append(Column(label, start, end))

    out: dict[str, list[Cell | None]] = {}
    for m in METRICS:
        cells: list[Cell | None] = []
        for col in columns:
            end = col.period_end
            if m.cover:
                near = [r for c in m.concepts for r in by_concept.get(c, [])
                        if 0 <= (r.period_end - end).days <= 100]
                f = min(near, key=lambda r: r.period_end) if near else None
            elif m.instant:
                f = pick(m, lambda r, e=end: r.period_end == e and _INSTANT.match(r.frame))
            else:
                f = pick(m, lambda r, e=end: r.period_end == e and flows.match(r.frame))
            if f is not None:
                cells.append(Cell(f.value, f.concept, f.accession, f.form, f.filed))
                continue
            cells.append(_derived_q4(m, end, by_concept) if (
                period == "quarterly" and not m.instant and m.additive) else None)
        out[m.key] = cells
    return Statement(period, columns, out)


def _derived_q4(m: Metric, end: date, by_concept: dict[str, list]) -> Cell | None:
    """A fiscal Q4 as the year less its first three quarters, all from one
    concept, when the year and all three were filed."""
    for c in m.concepts:
        rows = by_concept.get(c, [])
        year = next((r for r in rows if _YEAR.match(r.frame) and r.period_end == end), None)
        if year is None or year.period_start is None:
            continue
        quarters = {r.period_end: r for r in rows if _QUARTER.match(r.frame)
                    and r.period_start and r.period_start >= year.period_start
                    and r.period_end < end}
        if len(quarters) == 3:
            value = year.value - sum(q.value for q in quarters.values())
            return Cell(value, c, year.accession, year.form, year.filed, derived=True)
    return None
