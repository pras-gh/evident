/**
 * The evidence viewer as a whole, in jsdom: what a reader does, and what the
 * viewer shows and where it goes in response.
 */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EvidenceViewer, SPLIT } from "./evidence-viewer";
import { citations, claims, fy2025, fy2026 } from "./fixtures";

const entity = { slug: "export_controls", name: "Export Controls", entity_type: "risk",
                 confidence: 0.97, highlight_color: "#f87171" };

/** Node's own experimental `localStorage` global shadows jsdom's and has no
 *  methods, so the tests bring a plain one. The viewer tolerates either. */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() { return m.size; },
    key: (i) => [...m.keys()][i] ?? null,
    getItem: (k) => m.get(k) ?? null,
    setItem: (k, v) => void m.set(k, String(v)),
    removeItem: (k) => void m.delete(k),
    clear: () => m.clear(),
  };
}

let scrollTo: ReturnType<typeof vi.fn>;
let replaceState: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  Object.defineProperty(window, "localStorage", { value: memoryStorage(), configurable: true });
  scrollTo = vi.fn();
  HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo;
  replaceState = vi.spyOn(window.history, "replaceState");
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  replaceState.mockRestore();
  delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
});

function viewer(initialActive = 2) {
  return render(
    <EvidenceViewer entity={entity} claims={claims} citations={citations}
      initialActive={initialActive} documents={{ 1: fy2026, 2: fy2025 }} texts={{}} />,
  );
}

const chip = (n: number) =>
  within(screen.getByRole("navigation", { name: "Citations" })).getAllByRole("button")[n - 1];
const activeHighlight = () =>
  document.querySelector<HTMLElement>('[data-testid="highlight"][aria-current="location"]');

describe("opening at a citation (a deep link)", () => {
  it("shows the claim, the confidence and the source for that citation", () => {
    viewer(2);
    expect(screen.getByRole("heading", { name: "Export Controls" })).toBeTruthy();
    expect(screen.getByTestId("claim").textContent)
      .toBe("Export Controls Expanded. Cited in 20 paragraphs, up from 13 in the FY2024 10-K.");
    expect(screen.getByTestId("source").textContent).toBe("FY2025 10-K • Item 1A • Page 27");
    expect(screen.getByText("97%")).toBeTruthy();
  });

  it("opens its filing, at its page, with its paragraph highlighted", () => {
    viewer(2);
    expect(screen.getByRole("tab", { selected: true }).textContent).toBe("FY2025 10-K");
    expect(screen.getByTestId("page-indicator").textContent).toBe("p. 27 / 87");
    expect(activeHighlight()?.closest("[data-page]")?.getAttribute("data-page")).toBe("27");
    expect(chip(3).getAttribute("aria-pressed")).toBe("true");
    // landed, not animated: arriving by link
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "auto" }));
  });
});

describe("choosing another citation", () => {
  it("switches filing, page, claim and highlight, and the URL follows", () => {
    viewer(2);
    fireEvent.click(chip(1));
    expect(screen.getByRole("tab", { selected: true }).textContent).toBe("FY2026 10-K");
    expect(screen.getByTestId("page-indicator").textContent).toBe("p. 19 / 85");
    expect(screen.getByTestId("claim").textContent).toContain("up from 20 in the FY2025 10-K");
    expect(activeHighlight()?.closest("[data-page]")?.getAttribute("data-page")).toBe("19");
    expect(replaceState).toHaveBeenLastCalledWith(null, "",
      "/evidence/5?cite=5%3A19_1&entity=export_controls");
    expect(scrollTo).toHaveBeenLastCalledWith(expect.objectContaining({ behavior: "smooth" }));
  });

  it("steps through citations with the arrows, skipping ones that did not resolve", () => {
    viewer(1);
    const next = screen.getByRole("button", { name: "Next citation" });
    fireEvent.click(next);
    expect(screen.getByText("3 / 3")).toBeTruthy();
    expect((next as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Previous citation" }));
    expect(chip(2).getAttribute("aria-pressed")).toBe("true");
  });

  it("switches filing from the tabs, to that filing's first citation", () => {
    viewer(0);
    fireEvent.click(screen.getByRole("tab", { name: "FY2025 10-K" }));
    expect(chip(2).getAttribute("aria-pressed")).toBe("true");
  });

  it("keeps an unresolvable citation in place, disabled", () => {
    viewer();
    expect((chip(4) as HTMLButtonElement).disabled).toBe(true);
    expect(chip(4).textContent).toContain("unavailable");
  });
});

describe("the highlight", () => {
  it("draws every citation in the shown filing, and only the active one as active", () => {
    viewer(2);
    const all = document.querySelectorAll('[data-testid="highlight"]');
    expect(all).toHaveLength(2);                       // 26_3 and 27_1 are in FY2025
    expect(document.querySelectorAll('[aria-current="location"]')).toHaveLength(1);
  });

  it("stays while the reader scrolls, and Jump to Evidence takes them back", () => {
    viewer(2);
    const pane = screen.getByTestId("document-pane");
    for (const top of [0, 8000, 300, 12000]) {
      pane.scrollTop = top;
      pane.dispatchEvent(new Event("scroll"));
    }
    expect(activeHighlight()).not.toBeNull();
    scrollTo.mockClear();
    fireEvent.click(screen.getByRole("button", { name: /Jump to Evidence/ }));
    expect(scrollTo).toHaveBeenCalledOnce();
    expect(activeHighlight()?.closest("[data-page]")?.getAttribute("data-page")).toBe("27");
  });
});

describe("the divider", () => {
  it("resizes by keyboard, within bounds, and remembers", () => {
    const { container } = viewer();
    const sep = screen.getByRole("separator", { name: "Resize panels" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(SPLIT.initial));
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    fireEvent.keyDown(sep, { key: "ArrowRight" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(SPLIT.initial + 4));
    expect((container.firstChild as HTMLElement).style.gridTemplateColumns)
      .toBe(`${SPLIT.initial + 4}% 10px minmax(0, 1fr)`);
    expect(window.localStorage.getItem("evident.evidence.split")).toBe(String(SPLIT.initial + 4));
    fireEvent.keyDown(sep, { key: "End" });
    expect(sep.getAttribute("aria-valuenow")).toBe(String(SPLIT.max));
  });

  it("comes back where the reader left it", () => {
    window.localStorage.setItem("evident.evidence.split", "50");
    viewer();
    expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe("50");
  });
});

describe("the rest of the paragraph", () => {
  it("links the other entities cited in it, each to its own evidence", () => {
    viewer(2);
    const link = screen.getByRole("link", { name: "China" });
    expect(link.getAttribute("href")).toBe("/evidence/54?cite=54%3A27_1&entity=china");
  });

  it("offers the filing as a PDF and on EDGAR", () => {
    viewer(2);
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe("/api/document/2/pdf?v=1");
    expect(screen.getByRole("link", { name: /EDGAR/ }).getAttribute("href")).toBe(fy2025.url);
  });
});
