/**
 * The search page, rendered in jsdom from responses shaped like
 * POST /v1/search over the NVIDIA demo corpus.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { SearchView, excerpt, filedLabel } from "./SearchView";
import type { SearchHit, SearchOutcome } from "@/lib/types";

const TAIWAN =
  "Finally, our business depends on our ability to receive consistent and reliable supply " +
  "from our overseas partners, especially in Taiwan and South Korea.";

const hit = (over: Partial<SearchHit> = {}): SearchHit => ({
  chunk_id: 30, chunk_hash: "3f25", paragraph_ids: ["28_8", "28_9"], score: 0.7123,
  text: TAIWAN, accession: "0001045810-26-000021", form_type: "10-K",
  filed_at: "2026-02-25", page_number: 28, section_title: "Item 1A. Risk Factors",
  citation: "p. 28 · Item 1A. Risk Factors", ...over,
});

const answered = (hits: SearchHit[]): SearchOutcome =>
  ({ ok: true, result: { query: "Taiwan disruption", hits, took_ms: 612.4, embedder: "gemini/x" } });

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
