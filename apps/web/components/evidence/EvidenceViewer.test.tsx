/**
 * The evidence viewer, rendered in jsdom.
 *
 * jsdom has no layout, so element positions are stubbed where a test depends
 * on them. That is enough to prove *what* the viewer does — which element it
 * scrolls to, which paragraphs it highlights, that a scroll never clears the
 * highlight — while real scrolling and painting are checked in a browser.
 */
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvidenceViewer, splitMarkers } from "./EvidenceViewer";
import type { Answer, DocumentView, ResolvedCitation, ResolvedEvidence } from "@/lib/types";

// ------------------------------------------------------------------ fixtures
function block(pid: string, text = `paragraph ${pid}`) {
  return {
    anchor: `p-${pid}`,
    kind: "paragraph" as const,
    chunk_id: 1,
    paragraph_id: pid,
    section_title: "Item 1A",
    text,
  };
}

const tenK: DocumentView = {
  document_id: 1,
  accession: "0001045810-25-000023",
  form_type: "10-K",
  filed_at: "2025-02-26",
  source_format: "html",
  ticker: "NVDA",
  page_count: 87,
  pages: [
    { page: 26, blocks: [block("26_1"), block("26_2")] },
    { page: 27, blocks: [block("27_1"), block("27_2"), block("27_3", "Export licensing")] },
    { page: 28, blocks: [block("28_1"), block("28_2")] },
  ],
};

const tenQ: DocumentView = {
  ...tenK,
  document_id: 2,
  accession: "0001045810-24-000316",
  form_type: "10-Q",
  filed_at: "2024-11-20",
  page_count: 40,
  pages: [{ page: 12, blocks: [block("12_1", "Supply constraints")] }],
};

function evidence(over: Partial<ResolvedEvidence>): ResolvedEvidence {
  return {
    chunk_id: 1,
    document_id: 1,
    accession: tenK.accession,
    form_type: "10-K",
    filed_at: "2025-02-26",
    source_format: "html",
    page: 27,
    paragraph_id: "27_3",
    paragraph_ids: ["27_1", "27_2", "27_3"],
    anchors: ["p-27_3"],
    section_title: "Item 1A",
    text: "Export licensing",
    confidence: 0.95,
    bounding_box: null,
    citation: "10-K · p. 27 · Item 1A",
    ...over,
  };
}

const citations: ResolvedCitation[] = [
  // a paragraph on page 27
  { index: 0, resolved: true, error: null, evidence: evidence({}) },
  // a whole chunk on page 28: both its paragraphs light up
  {
    index: 1,
    resolved: true,
    error: null,
    evidence: evidence({
      chunk_id: 2,
      page: 28,
      paragraph_id: "28_1",
      paragraph_ids: ["28_1", "28_2"],
      anchors: ["p-28_1", "p-28_2"],
      citation: "10-K · p. 28",
      confidence: 0.6,
    }),
  },
  // a different filing altogether
  {
    index: 2,
    resolved: true,
    error: null,
    evidence: evidence({
      chunk_id: 3,
      document_id: 2,
      form_type: "10-Q",
      filed_at: "2024-11-20",
      page: 12,
      paragraph_id: "12_1",
      paragraph_ids: ["12_1"],
      anchors: ["p-12_1"],
      text: "Supply constraints",
      citation: "10-Q · p. 12",
      confidence: null,
    }),
  },
  // a stale reference
  { index: 3, resolved: false, error: "no chunk 999", evidence: null },
];

const answer: Answer = {
  text: "Export licensing restricts shipments [1] and supply is tight [3].",
  citations,
};

// ------------------------------------------------------------------- helpers
let scrollTo: ReturnType<typeof vi.fn>;

function renderViewer(a: Answer = answer) {
  const view = render(
    <EvidenceViewer title="Export Controls" answer={a} documents={{ 1: tenK, 2: tenQ }} />,
  );
  const pane = screen.getByTestId("document-pane");
  pane.scrollTo = scrollTo as unknown as typeof pane.scrollTo;
  return { ...view, pane };
}

const chip = (n: number) =>
  within(screen.getByRole("list", { name: "Citations" })).getAllByRole("button")[n - 1];
const anchor = (pane: HTMLElement, a: string) =>
  pane.querySelector(`[data-anchor="${a}"]`) as HTMLElement;
const highlighted = (pane: HTMLElement) =>
  [...pane.querySelectorAll(".is-cited")].map((el) => el.getAttribute("data-anchor"));

function place(el: Element, top: number) {
  el.getBoundingClientRect = () => ({ top, bottom: top + 40 }) as DOMRect;
}

beforeEach(() => {
  scrollTo = vi.fn();
  // run animation frames synchronously so scroll handling is observable
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    cb(0);
    return 1;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// --------------------------------------------------------------------- tests
describe("multiple citations", () => {
  it("renders one chip per citation, in order, labelled with form and page", () => {
    renderViewer();
    const chips = within(screen.getByRole("list", { name: "Citations" })).getAllByRole("button");
    expect(chips).toHaveLength(4);
    // two filings are cited, so each chip names its filing as well
    expect(chips[0].textContent).toBe("110-K 2025-02 · p. 27");
    expect(chips[1].textContent).toBe("210-K 2025-02 · p. 28");
    expect(chips[2].textContent).toBe("310-Q 2024-11 · p. 12");
  });

  it("keeps an unresolvable citation in place, disabled, so numbering still lines up", () => {
    renderViewer();
    const stale = chip(4);
    expect(stale.hasAttribute("disabled")).toBe(true);
    expect(stale.getAttribute("title")).toBe("no chunk 999");
    expect(stale.textContent).toContain("unavailable");
  });

  it("marks every paragraph the answer cites in the shown filing", () => {
    const { pane } = renderViewer();
    const gutters = [...pane.querySelectorAll(".is-referenced")].map((el) =>
      el.getAttribute("data-anchor"),
    );
    // citation 3 is in the 10-Q, which is not the document on screen
    expect(gutters).toEqual(["p-27_3", "p-28_1", "p-28_2"]);
    expect(anchor(pane, "p-27_3").querySelector(".ev-gutter")?.textContent).toBe("1");
  });

  it("offers a tab per cited filing", () => {
    renderViewer();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["10-K · 2025-02-26", "10-Q · 2024-11-20"]);
  });
});

describe("clicking a citation", () => {
  it("scrolls to the cited paragraph on the right page", () => {
    const { pane } = renderViewer();
    const target = anchor(pane, "p-27_3");
    // the paragraph sits 1,000px down a pane that is already scrolled 200px
    place(pane, 0);
    place(target, 1000);
    pane.scrollTop = 200;

    fireEvent.click(chip(1));

    expect(target.closest("[data-page]")?.getAttribute("data-page")).toBe("27");
    expect(scrollTo).toHaveBeenCalledTimes(1);
    // 1000 + 200 already scrolled - 96 of context above it
    expect(scrollTo.mock.calls[0][0].top).toBe(1104);
  });

  it("highlights exactly the cited paragraph", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(1));
    expect(highlighted(pane)).toEqual(["p-27_3"]);
    expect(anchor(pane, "p-27_3").getAttribute("aria-current")).toBe("location");
  });

  it("highlights every paragraph of a whole-chunk citation, scrolling to the first", () => {
    const { pane } = renderViewer();
    place(pane, 0);
    place(anchor(pane, "p-28_1"), 300);
    place(anchor(pane, "p-28_2"), 500);
    fireEvent.click(chip(2));
    expect(highlighted(pane)).toEqual(["p-28_1", "p-28_2"]);
    expect(scrollTo.mock.calls[0][0].top).toBe(300 - 96);
  });

  it("switches to the citation's filing when it is in another one", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(3));
    expect(screen.getByRole("tab", { selected: true }).textContent).toBe("10-Q · 2024-11-20");
    expect(highlighted(pane)).toEqual(["p-12_1"]);
    expect(anchor(pane, "p-27_3")).toBeNull();
  });

  it("moves the highlight rather than adding to it", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(1));
    fireEvent.click(chip(2));
    expect(highlighted(pane)).toEqual(["p-28_1", "p-28_2"]);
    expect(chip(1).getAttribute("aria-pressed")).toBe("false");
    expect(chip(2).getAttribute("aria-pressed")).toBe("true");
  });

  it("scrolls back when the active citation is clicked again", () => {
    renderViewer();
    fireEvent.click(chip(1));
    fireEvent.click(chip(1));
    expect(scrollTo).toHaveBeenCalledTimes(2);
  });

  it("shows the cited text and its confidence, or says there is none", () => {
    renderViewer();
    fireEvent.click(chip(1));
    expect(document.querySelector(".ev-quote blockquote")?.textContent).toBe("Export licensing");
    expect(screen.getByText("95% confidence")).toBeTruthy();
    fireEvent.click(chip(3));
    expect(screen.getByText("no confidence recorded")).toBeTruthy();
  });

  it("does nothing for an unresolvable citation", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(4));
    expect(highlighted(pane)).toEqual([]);
    expect(scrollTo).not.toHaveBeenCalled();
  });

  it("follows [n] markers in the answer text", () => {
    const { pane } = renderViewer();
    fireEvent.click(screen.getByRole("button", { name: "Show citation 3" }));
    expect(highlighted(pane)).toEqual(["p-12_1"]);
  });
});

describe("the highlight persists while scrolling", () => {
  it("survives scrolling away and back", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(1));
    expect(highlighted(pane)).toEqual(["p-27_3"]);

    const pages = [...pane.querySelectorAll("[data-page]")];
    place(pane, 0);
    // scrolled down to page 28 ...
    pages.forEach((p, i) => place(p, [-900, -400, 20][i]));
    act(() => {
      pane.scrollTop = 1800;
      fireEvent.scroll(pane);
    });
    expect(screen.getByTestId("page-indicator").textContent).toBe("p. 28 / 87");
    expect(highlighted(pane)).toEqual(["p-27_3"]);

    // ... and back up to page 26
    pages.forEach((p, i) => place(p, [10, 500, 900][i]));
    act(() => {
      pane.scrollTop = 0;
      fireEvent.scroll(pane);
    });
    expect(screen.getByTestId("page-indicator").textContent).toBe("p. 26 / 87");
    expect(highlighted(pane)).toEqual(["p-27_3"]);
  });

  it("is not re-scrolled by scrolling — the reader stays where they went", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(1));
    act(() => {
      for (let i = 0; i < 20; i++) fireEvent.scroll(pane);
    });
    // one scroll, from the click; none from the reader's own scrolling
    expect(scrollTo).toHaveBeenCalledTimes(1);
  });

  it("survives a re-render with the same data", () => {
    const { pane, rerender } = renderViewer();
    fireEvent.click(chip(2));
    rerender(
      <EvidenceViewer title="Export Controls" answer={answer} documents={{ 1: tenK, 2: tenQ }} />,
    );
    expect(highlighted(pane)).toEqual(["p-28_1", "p-28_2"]);
  });

  it("survives switching filings and switching back", () => {
    const { pane } = renderViewer();
    fireEvent.click(chip(1));
    fireEvent.click(screen.getByRole("tab", { name: "10-Q · 2024-11-20" }));
    expect(highlighted(pane)).toEqual([]);
    fireEvent.click(screen.getByRole("tab", { name: "10-K · 2025-02-26" }));
    expect(highlighted(pane)).toEqual(["p-27_3"]);
  });
});

describe("splitMarkers", () => {
  it("separates text from citation numbers", () => {
    expect(splitMarkers("a [1] b [12]c")).toEqual(["a ", 1, " b ", 12, "c"]);
  });

  it("leaves text without markers alone", () => {
    expect(splitMarkers("no citations here")).toEqual(["no citations here"]);
    expect(splitMarkers("")).toEqual([]);
  });
});
