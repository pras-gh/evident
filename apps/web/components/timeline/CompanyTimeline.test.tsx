/**
 * The company timeline, rendered in jsdom from a response shaped exactly like
 * GET /v1/company/NVDA/timeline over the three-year NVIDIA corpus.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CompanyTimelineView, coverageNote, evidenceHref } from "./CompanyTimeline";
import type { CompanyTimeline, TimelineEvent, TimelineFiling } from "@/lib/types";

// ------------------------------------------------------------------ fixtures
const FY24: TimelineFiling = {
  document_id: 3, accession: "0001045810-24-000029", form_type: "10-K",
  fiscal_period: "FY2024", filed_at: "2024-02-21", page_count: 85, coverage: "baseline",
  url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581024000029/0001045810-24-000029-index.htm",
};
const FY25: TimelineFiling = {
  ...FY24, document_id: 2, accession: "0001045810-25-000023", fiscal_period: "FY2025",
  filed_at: "2025-02-26", page_count: 87, coverage: "compared",
  url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/0001045810-25-000023-index.htm",
};
const FY26: TimelineFiling = {
  ...FY24, document_id: 1, accession: "0001045810-26-000021", fiscal_period: "FY2026",
  filed_at: "2026-02-25", coverage: "compared",
  url: "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/0001045810-26-000021-index.htm",
};

const ref = ({ document_id, accession, form_type, fiscal_period, filed_at }: TimelineFiling) =>
  ({ document_id, accession, form_type, fiscal_period, filed_at });

function filed(f: TimelineFiling): TimelineEvent {
  return {
    id: `${f.accession}:filed:`, date: f.filed_at,
    date_label: new Date(f.filed_at + "T12:00:00Z").toLocaleString("en-US", {
      month: "short", year: "numeric", timeZone: "UTC",
    }),
    kind: "filed", category: "Filing", title: `${f.fiscal_period} 10-K Filed`,
    summary: `Form 10-K filed, ${f.page_count} pages.`, topic: null, filing: ref(f),
    compared_with: null, paragraphs: null, previous_paragraphs: null, evidence: null,
  };
}

function change(over: Partial<TimelineEvent> & Pick<TimelineEvent, "kind" | "title">): TimelineEvent {
  return {
    id: `${FY26.accession}:${over.kind}:${over.topic?.slug ?? "x"}`,
    date: "2026-02-25", date_label: "Feb 2026", category: "Risk", summary: "",
    topic: { slug: "export_controls", name: "Export Controls", entity_type: "risk" },
    filing: ref(FY26), compared_with: ref(FY25), paragraphs: 26, previous_paragraphs: 20,
    evidence: {
      chunk_id: 12, paragraph_id: "26_1", document_id: 1, page: 26,
      quote: "Export controls targeting GPUs and semiconductors associated with AI…",
      confidence: 1, new_paragraph: true, citation: "10-K · p. 26",
    },
    ...over,
  };
}

const events: TimelineEvent[] = [
  filed(FY26),
  change({ kind: "expanded", title: "Export Controls Expanded",
           summary: "Cited in 26 paragraphs, up from 20 in the FY2025 10-K." }),
  change({
    kind: "no_longer_disclosed", title: "DGX Cloud No Longer Disclosed", category: "Product",
    topic: { slug: "dgx_cloud", name: "DGX Cloud", entity_type: "product" },
    paragraphs: 0, previous_paragraphs: 1,
    // nothing in FY2026 to cite, so it points at where it last appeared
    evidence: { chunk_id: 40, paragraph_id: "15_1", document_id: 2, page: 15,
                quote: "We offer enterprise customers NVIDIA DGX Cloud services…",
                confidence: 1, new_paragraph: null, citation: "10-K · p. 15" },
  }),
  filed(FY25),
  change({
    id: `${FY25.accession}:expanded:china`, kind: "expanded", title: "China Expanded",
    category: "Geography", date: "2025-02-26", date_label: "Feb 2025",
    topic: { slug: "china", name: "China", entity_type: "geography" },
    filing: ref(FY25), compared_with: ref(FY24), paragraphs: 17, previous_paragraphs: 13,
    evidence: { chunk_id: 77, paragraph_id: "25_1#2", document_id: 2, page: 25,
                quote: "We have also received…", confidence: 1, new_paragraph: false,
                citation: "10-K · p. 25" },
  }),
  filed(FY24),
];

const timeline: CompanyTimeline = {
  ticker: "NVDA", company: "NVIDIA CORP", filings: [FY26, FY25, FY24],
  thresholds: { min_delta: 3, min_ratio: 1.25 },
  categories: { Filing: 3, Risk: 1, Product: 1, Geography: 1 },
  total: events.length, events,
};

afterEach(cleanup);

const card = (title: string) => screen.getByRole("article", { name: title });

// --------------------------------------------------------------------- tests
describe("layout", () => {
  it("groups events under their filing's date, newest first", () => {
    const { container } = render(<CompanyTimelineView timeline={timeline} />);
    const dates = [...container.querySelectorAll(".tl2-date")].map((d) => d.textContent);
    expect(dates).toEqual(["Feb2026", "Feb2025", "Feb2024"]);
    const groups = screen.getAllByRole("list").filter((l) => l.classList.contains("tl2-events"));
    expect(groups.map((g) => within(g).getAllByRole("article").map((a) => a.getAttribute("aria-label"))))
      .toEqual([
        ["FY2026 10-K Filed", "Export Controls Expanded", "DGX Cloud No Longer Disclosed"],
        ["FY2025 10-K Filed", "China Expanded"],
        ["FY2024 10-K Filed"],
      ]);
  });

  it("shows date, category, title and evidence on every change", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    const c = card("Export Controls Expanded");
    expect(within(c).getByText("Feb 2026").tagName).toBe("TIME");
    expect(within(c).getByText("Risk")).toBeTruthy();
    expect(within(c).getByText("Expanded")).toBeTruthy();
    expect(within(c).getByRole("link", { name: /10-K · p\. 26/ })).toBeTruthy();
    expect(c.querySelector(".tl2-delta")?.textContent).toBe("20 → 26¶");
  });
});

describe("evidence links", () => {
  it("open the cited paragraph in the evidence viewer", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    const link = within(card("Export Controls Expanded")).getByRole("link", { name: /p\. 26/ });
    expect(link.getAttribute("href")).toBe("/evidence/nvda/export_controls?cite=12%3A26_1");
  });

  it("encode a split paragraph's # so it is not read as a fragment", () => {
    expect(evidenceHref("nvda", "china", { chunk_id: 77, paragraph_id: "25_1#2" }))
      .toBe("/evidence/nvda/china?cite=77%3A25_1%232");
    expect(new URL(evidenceHref("nvda", "china", { chunk_id: 77, paragraph_id: "25_1#2" }),
                   "http://x").searchParams.get("cite")).toBe("77:25_1#2");
  });

  it("say which filing a dropped topic was last cited in", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    const link = within(card("DGX Cloud No Longer Disclosed")).getByRole("link", { name: /p\. 15/ });
    expect(link.textContent).toBe(" 10-K · p. 15 · FY2025");
  });

  it("mark new material, and only new material", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    expect(within(card("Export Controls Expanded")).queryByText("new this filing")).toBeTruthy();
    expect(within(card("China Expanded")).queryByText("new this filing")).toBeNull();
    expect(within(card("DGX Cloud No Longer Disclosed")).queryByText("new this filing")).toBeNull();
  });

  it("link filings to EDGAR, and say which one is the baseline", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    const base = card("FY2024 10-K Filed");
    expect(within(base).getByText(/baseline/)).toBeTruthy();
    expect(within(base).getByRole("link").getAttribute("href")).toBe(FY24.url);
    expect(within(card("FY2025 10-K Filed")).queryByText(/baseline/)).toBeNull();
  });
});

describe("filters", () => {
  it("offer every category with its count, plus All", () => {
    render(<CompanyTimelineView timeline={timeline} />);
    const nav = screen.getByRole("navigation", { name: "Filter by category" });
    expect(within(nav).getAllByRole("link").map((a) => [a.textContent, a.getAttribute("href")]))
      .toEqual([
        ["All 6", "?"],
        ["Risk 1", "?category=risk"],
        ["Product 1", "?category=product"],
        ["Geography 1", "?category=geography"],
        ["Filing 3", "?category=filing"],
      ]);
  });

  it("mark the active one", () => {
    render(<CompanyTimelineView timeline={{ ...timeline, events: [events[1]] }} category="risk" />);
    const nav = screen.getByRole("navigation", { name: "Filter by category" });
    const current = within(nav).getAllByRole("link").filter((a) => a.getAttribute("aria-current"));
    expect(current.map((a) => a.textContent)).toEqual(["Risk 1"]);
  });

  it("say so when a category is empty", () => {
    render(<CompanyTimelineView timeline={{ ...timeline, events: [] }} category="Metric" />);
    expect(screen.getByText("No events in Metric.")).toBeTruthy();
  });
});

describe("coverage note", () => {
  it("names the baseline and the thresholds", () => {
    expect(coverageNote(timeline)).toBe(
      "On record: 3 10-Ks, each compared with the one before; the oldest, FY2024, is the " +
        "baseline. A topic is marked expanded when it is cited in at least 3 more paragraphs " +
        "than in the previous filing of the same form, and at least 1.25× as many; narrowed " +
        "is the reverse.",
    );
  });

  it("says there is nothing to compare with one filing — not that nothing happened", () => {
    const one = { ...timeline, filings: [{ ...FY26, coverage: "baseline" as const }] };
    expect(coverageNote(one)).toMatch(/^On record: one 10-K \(FY2026\), so nothing to compare it with yet\./);
  });
});
