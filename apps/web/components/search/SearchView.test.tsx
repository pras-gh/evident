/**
 * The search page, rendered in jsdom from responses shaped like
 * POST /v1/search over the NVIDIA demo corpus.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SearchView, excerpt, filedLabel } from "./SearchView";
import type {
  CompanyTimeline,
  SearchHit,
  SearchOutcome,
  SearchResponse,
} from "@/lib/types";

const TAIWAN =
  "Finally, our business depends on our ability to receive consistent and reliable supply " +
  "from our overseas partners, especially in Taiwan and South Korea.";

const hit = (over: Partial<SearchHit> = {}): SearchHit => ({
  chunk_id: 30, chunk_hash: "3f25", paragraph_ids: ["28_8", "28_9"], score: 0.7123,
  text: TAIWAN, accession: "0001045810-26-000021", form_type: "10-K",
  filed_at: "2026-02-25", page_number: 28, section_title: "Item 1A. Risk Factors",
  citation: "p. 28 · Item 1A. Risk Factors", ...over,
});

const NVDA = { name: "NVIDIA CORP", ticker: "NVDA", covered: true };

const answered = (hits: SearchHit[], over: Partial<SearchResponse> = {}): SearchOutcome => ({
  ok: true,
  result: {
    query: "Taiwan disruption", hits, took_ms: 612.4, embedder: "gemini/x",
    companies: [], scope: { tickers: null, reason: "all" }, topic: null,
    on_record: [NVDA], understood_by: "rules+gemini:flash", ...over,
  },
});

afterEach(cleanup);

describe("search page", () => {
  it("offers a box and example searches before anything is asked", () => {
    render(<SearchView query="" ticker="nvda" outcome={null} />);
    expect(screen.getByRole("searchbox")).toHaveProperty("value", "");
    const examples = within(screen.getByRole("list", { name: "Example searches" }))
      .getAllByRole("link");
    expect(examples[0].getAttribute("href"))
      .toBe("/search?q=restrictions+on+selling+chips+to+China&ticker=nvda");
  });

  it("keeps the company filter on the next search", () => {
    const { container } = render(<SearchView query="" ticker="nvda" outcome={null} />);
    const hidden = container.querySelector('form input[type="hidden"][name="ticker"]');
    expect(hidden?.getAttribute("value")).toBe("nvda");
  });

  it("shows each passage with its filing and a link to the cited paragraph", () => {
    render(<SearchView query="Taiwan disruption" outcome={answered([hit()])} />);
    expect(screen.getByRole("searchbox")).toHaveProperty("value", "Taiwan disruption");
    expect(screen.getByText("1 passage · 612 ms")).toBeTruthy();
    const result = within(screen.getByRole("list", { name: "Results for Taiwan disruption" }))
      .getByRole("listitem");
    expect(within(result).getByText(TAIWAN)).toBeTruthy();
    expect(within(result).getByText("filed Feb 2026")).toBeTruthy();
    expect(within(result).getByText("p. 28 · Item 1A. Risk Factors")).toBeTruthy();
    expect(within(result).getByText("71% match")).toBeTruthy();
    expect(within(result).getByRole("link", { name: "Open in the filing →" })
      .getAttribute("href")).toBe("/evidence/30?cite=30%3A28_8");
  });

  it("says so when nothing matched", () => {
    render(<SearchView query="Taiwan disruption" outcome={answered([])} />);
    expect(screen.getByText("No passages found for “Taiwan disruption”.")).toBeTruthy();
  });

  it("explains why search could not answer, in the API's words", () => {
    render(<SearchView query="chips" outcome={{ ok: false, error: "search_not_indexed",
      message: "No chunks are embedded with gemini/gemini-embedding-2 yet." }} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toBe(
      "Nothing is indexed yet. No chunks are embedded with gemini/gemini-embedding-2 yet.");
  });
});

describe("which companies were searched", () => {
  it("says a company with no filings here has none, and shows nobody else's", () => {
    render(<SearchView query="wesco international" outcome={answered([], {
      query: "wesco international",
      companies: [{ name: "WESCO International, Inc.", ticker: "WCC", covered: false }],
      scope: { tickers: [], reason: "named" }, embedder: null,
    })} />);
    expect(screen.getByRole("status").textContent).toBe(
      "No filings for WESCO International, Inc. (WCC) yet. Evident has filings for NVIDIA CORP (NVDA).");
    expect(screen.queryByRole("list", { name: /Results/ })).toBeNull();
    expect(screen.queryByText(/No passages found/)).toBeNull();
  });

  it("names the company searched, and the topic it searched for", () => {
    render(<SearchView query="nvidia export controls" outcome={answered([hit()], {
      query: "nvidia export controls", companies: [NVDA],
      scope: { tickers: ["NVDA"], reason: "named" }, topic: "export controls",
    })} />);
    expect(screen.getByTestId("scope").textContent).toBe(
      "Showing filings from NVIDIA CORP (NVDA) about “export controls”.");
  });

  it("covers both when a query names one company we have and one we don't", () => {
    render(<SearchView query="nvidia vs wesco" outcome={answered([hit()], {
      companies: [NVDA, { name: "WESCO International", ticker: "WCC", covered: false }],
      scope: { tickers: ["NVDA"], reason: "named" },
    })} />);
    expect(screen.getByRole("status").textContent).toMatch(/^No filings for WESCO International \(WCC\) yet\./);
    expect(screen.getByTestId("scope").textContent).toContain("Showing filings from NVIDIA CORP (NVDA).");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("offers to drop the company filter", () => {
    render(<SearchView query="supply" ticker="nvda" outcome={answered([hit()], {
      query: "supply", scope: { tickers: ["NVDA"], reason: "filter" },
    })} />);
    expect(screen.getByRole("link", { name: "Search every company" }).getAttribute("href"))
      .toBe("/search?q=supply");
  });

  it("is honest when it could not tell which company was meant", () => {
    render(<SearchView query="wesco" outcome={answered([hit()], {
      query: "wesco", understood_by: "rules" })} />);
    expect(screen.getByTestId("scope").textContent).toBe(
      "Couldn’t check which company you meant, so this searches every company on record: " +
      "NVIDIA CORP (NVDA).");
  });
});

describe("company card", () => {
  const timeline = {
    ticker: "NVDA", company: "NVIDIA CORP",
    filings: [{ document_id: 1, accession: "A26", form_type: "10-K", fiscal_period: "FY2026",
                filed_at: "2026-02-25", page_count: 85, coverage: "compared", url: "" }],
    events: [
      { id: "A26:filed:", kind: "filed", title: "FY2026 10-K Filed", topic: null, evidence: null,
        filing: { accession: "A26" } },
      { id: "A26:newly_disclosed:h20", kind: "newly_disclosed", title: "H20 Newly Disclosed",
        topic: { slug: "h20", name: "H20", entity_type: "product" },
        evidence: { chunk_id: 21, paragraph_id: "26_8" }, filing: { accession: "A26" } },
      { id: "A25:newly_disclosed:blackwell", kind: "newly_disclosed", title: "Blackwell Newly Disclosed",
        topic: { slug: "blackwell", name: "Blackwell", entity_type: "product" },
        evidence: { chunk_id: 9, paragraph_id: "17_1" }, filing: { accession: "A25" } },
    ],
  } as unknown as CompanyTimeline;

  it("shows what the latest filing changed, each change linked to its evidence", () => {
    render(<SearchView query="what is going on with nvda" outcome={answered([hit()], {
      companies: [NVDA], scope: { tickers: ["NVDA"], reason: "named" } })} company={timeline} />);
    const card = screen.getByRole("region", { name: "NVIDIA CORP at a glance" });
    expect(within(card).getByText("1 filing on record, latest 10-K filed Feb 2026")).toBeTruthy();
    const changes = within(card).getAllByRole("listitem");
    expect(changes.map((li) => li.textContent)).toEqual(["H20 Newly Disclosed"]);
    expect(within(changes[0]).getByRole("link").getAttribute("href"))
      .toBe("/evidence/21?cite=21%3A26_8&entity=h20");
    expect(within(card).getByRole("link", { name: "Full timeline →" }).getAttribute("href"))
      .toBe("/timeline/nvda");
  });
});

describe("formatting", () => {
  it("labels the filing month without a timezone moving the day", () => {
    expect(filedLabel("2026-02-01")).toBe("filed Feb 2026");
  });

  it("cuts a long passage at a word", () => {
    const long = "word ".repeat(200);
    const out = excerpt(long, 50);
    expect(out.endsWith("word…")).toBe(true);
    expect(out.length).toBeLessThanOrEqual(51);
    expect(excerpt("  short   text ")).toBe("short text");
  });
});
