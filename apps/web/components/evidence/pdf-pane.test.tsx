/**
 * The filing pane, in jsdom. jsdom has no layout, so positions are stubbed
 * where a test depends on them; real scrolling and painting are checked in a
 * browser. What these prove: which page and box the pane scrolls to, that
 * overlays land on the right pages, that a scroll never clears the highlight,
 * and that an unrendered filing still shows its citation.
 */
import { cleanup, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fy2025, unrendered, unrenderedText } from "./fixtures";
import { type PaneHighlight, PdfPane, SCROLL_OFFSET } from "./pdf-pane";

const box = (page: number, y0: number) =>
  ({ page, x0: 72, y0, x1: 540, y1: y0 + 40, page_width: 612, page_height: page === 27 ? 1224 : 792 });

const highlights: PaneHighlight[] = [
  { key: "a", anchor: "p-26_3", page: 26, box: box(26, 300), color: "#f87171", n: 2, active: false },
  { key: "b", anchor: "p-27_1", page: 27, box: box(27, 612), color: "#f87171", n: 3, active: true },
];

let scrollTo: ReturnType<typeof vi.fn>;
beforeEach(() => {
  scrollTo = vi.fn();
  HTMLElement.prototype.scrollTo = scrollTo as unknown as typeof HTMLElement.prototype.scrollTo;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { cb(0); return 1; });
  vi.stubGlobal("cancelAnimationFrame", () => {});
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as Partial<HTMLElement>).scrollTo;
});

function pane(props: Partial<Parameters<typeof PdfPane>[0]> = {}) {
  const paneRef = createRef<HTMLDivElement>();
  const onPage = vi.fn();
  const view = render(
    <PdfPane doc={fy2025} highlights={highlights}
      target={{ page: 27, box: box(27, 612), anchor: "p-27_1" }}
      pulse={0} zoom={1} onPage={onPage} paneRef={paneRef} {...props} />,
  );
  return { ...view, paneRef, onPage };
}

describe("rendered filing", () => {
  it("shows only rendered pages, each keeping its page's proportions", () => {
    pane();
    const pages = screen.getAllByRole("article");
    expect(pages.map((p) => p.getAttribute("aria-label"))).toEqual(["Page 26", "Page 27"]);
    expect(pages[1].style.aspectRatio).toBe("612 / 1224");
    expect(pages[1].querySelector("img")?.getAttribute("src")).toBe("/api/document/2/page/27/image?v=1");
  });

  it("draws each citation on its own page, the active one on top", () => {
    pane();
    const [p26, p27] = screen.getAllByRole("article");
    expect(p26.querySelectorAll('[data-testid="highlight"]')).toHaveLength(1);
    const active = p27.querySelector('[data-testid="highlight"]') as HTMLElement;
    expect(active.getAttribute("aria-current")).toBe("location");
    // 612pt down a 1224pt page, minus 4pt of padding
    expect(active.style.top).toBe(`${((608 / 1224) * 100).toFixed(4)}%`);
  });

  it("scrolls to page top + (y0 / page height) × drawn height − the offset", () => {
    // Lay out before the effect runs: stub the prototype for every element.
    const rect = vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      return ({ top: this.dataset.page === "27" ? 2000 : 0 }) as DOMRect;
    });
    const height = vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(1000);
    pane({ pulse: 0 });
    // 2000 + (612 / 1224) × 1000 − 96
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 2000 + 500 - SCROLL_OFFSET, behavior: "auto" });
    cleanup();
    pane({ pulse: 3 });
    expect(scrollTo).toHaveBeenLastCalledWith({ top: 2000 + 500 - SCROLL_OFFSET, behavior: "smooth" });
    rect.mockRestore();
    height.mockRestore();
  });

  it("keeps the highlight however the reader scrolls, and reports the page", () => {
    const { paneRef, onPage } = pane();
    const el = paneRef.current!;
    for (const top of [0, 5000, 100, 9000, 0]) {
      el.scrollTop = top;
      el.dispatchEvent(new Event("scroll"));
    }
    expect(screen.getByRole("article", { name: "Page 27" })
      .querySelector('[aria-current="location"]')).not.toBeNull();
    expect(onPage).toHaveBeenCalled();
  });

  it("scales with zoom", () => {
    const { container } = pane({ zoom: 1.5 });
    const pages = container.querySelector(".pp-pages") as HTMLElement;
    expect(pages.style.getPropertyValue("--zoom")).toBe("1.5");
  });
});

describe("unrendered filing", () => {
  it("falls back to the text, with the citation highlighted by its anchor", () => {
    pane({
      doc: unrendered, text: unrenderedText,
      highlights: [{ key: "t", anchor: "p-26_4", page: 26, box: null, color: "#f87171", n: 1, active: true }],
      target: { page: 26, box: null, anchor: "p-26_4" },
    });
    expect(screen.queryByRole("img")).toBeNull();
    const block = document.querySelector('[data-anchor="p-26_4"]') as HTMLElement;
    expect(block.className).toContain("is-cited");
    expect(block.getAttribute("aria-current")).toBe("location");
    expect(scrollTo).toHaveBeenCalled();
  });

  it("says so when there is neither pages nor text", () => {
    pane({ doc: unrendered, text: undefined, highlights: [], target: null });
    expect(screen.getByText("This filing has not been rendered yet.")).toBeTruthy();
  });
});
