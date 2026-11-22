/**
 * The answer card: a web answer with its sources and Google's suggestions, a
 * filings answer whose citations open the paragraphs, and the notices.
 */
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { AnswerView, Body } from "./Answer";
import type { AskResponse } from "@/lib/types";

afterEach(cleanup);

const base: AskResponse = {
  mode: "none", answer: null, sources: [], search_suggestions: null, citations: [],
  notice: null, model: null, cost_usd: null,
};

describe("a web answer", () => {
  const web: AskResponse = {
    ...base, mode: "web", answer: "Shares fell after **export curbs** were reported.",
    sources: [{ title: "reuters.com", uri: "https://example.test/r" }],
    search_suggestions: "<div>chips</div>",
  };

  it("is labelled as from the web, with its sources and Google's suggestions", () => {
    render(<AnswerView answer={web} />);
    const card = screen.getByRole("region", { name: "Answer from the web" });
    expect(within(card).getByText(/from the web, via Google Search/)).toBeTruthy();
    expect(within(card).getByText("export curbs").tagName).toBe("STRONG");
    const link = within(card).getByRole("link", { name: "reuters.com" });
    expect(link.getAttribute("href")).toBe("https://example.test/r");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    const frame = card.querySelector("iframe")!;
    expect(frame.getAttribute("srcdoc")).toBe("<div>chips</div>");
    expect(frame.getAttribute("sandbox")).toBe("allow-popups allow-popups-to-escape-sandbox");
    expect(within(card).getByText(/Not from SEC filings · not investment advice/)).toBeTruthy();
  });
});

describe("a filings answer", () => {
  const filings: AskResponse = {
    ...base, mode: "filings",
    answer: "NVIDIA faces risks in China:\n\n* **Export controls** limit sales [1].\n* Doubts about the H20 [2][9].",
    citations: [
      { n: 1, chunk_id: 18, paragraph_id: "26_1", company: "NVIDIA CORP", form_type: "10-K",
        filed_at: "2026-02-25", citation: "p. 26 · Item 1A. Risk Factors" },
      { n: 2, chunk_id: 19, paragraph_id: "27_8", company: "NVIDIA CORP", form_type: "10-K",
        filed_at: "2026-02-25", citation: "p. 27 · Item 1A. Risk Factors" },
    ],
    notice: "Live market answers need billing enabled on the Gemini API key: Google’s web " +
            "search isn’t available on the free tier. Here is what the SEC filings say instead.",
  };

  it("says why it isn't a web answer, then cites its paragraphs", () => {
    render(<AnswerView answer={filings} />);
    expect(screen.getByRole("status").textContent).toMatch(/need billing enabled/);
    const card = screen.getByRole("region", { name: "Answer from SEC filings" });
    expect(within(card).getByText("Answer · from NVIDIA CORP’s SEC filings")).toBeTruthy();
    const bullets = within(card).getAllByRole("listitem").slice(0, 2);
    expect(bullets[0].textContent).toBe("Export controls limit sales [1].");
    expect(within(bullets[0]).getByRole("link", { name: "[1]" }).getAttribute("href"))
      .toBe("/evidence/18?cite=18%3A26_1");
    // a marker for a passage that isn't cited stays text, not a dead link
    expect(within(bullets[1]).queryByRole("link", { name: "[9]" })).toBeNull();
    const cited = within(card).getByRole("list", { name: "Cited passages" });
    expect(within(cited).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "NVIDIA CORP 10-K, filed Feb 2026 · p. 26 · Item 1A. Risk Factors",
      "NVIDIA CORP 10-K, filed Feb 2026 · p. 27 · Item 1A. Risk Factors",
    ]);
  });
});

describe("no answer", () => {
  it("shows only the notice, or nothing", () => {
    const { container } = render(<AnswerView answer={base} />);
    expect(container.textContent).toBe("");
    cleanup();
    render(<AnswerView answer={{ ...base, notice: "Couldn’t write an answer right now." }} />);
    expect(screen.getByRole("status").textContent).toBe("Couldn’t write an answer right now.");
  });
});

describe("the answer's text", () => {
  it("is rendered as elements, never as HTML", () => {
    const { container } = render(<Body text={"<img src=x onerror=alert(1)> **bold**"} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("<img src=x onerror=alert(1)> bold");
  });
});
