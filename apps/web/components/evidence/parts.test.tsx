/** The viewer's small parts: the overlay, a citation chip, the thumbnail rail. */
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CitationChip } from "./citation-chip";
import { citations, fy2025 } from "./fixtures";
import { HighlightOverlay, boxStyle } from "./highlight-overlay";
import { PageThumbnailRail } from "./page-thumbnail";

afterEach(cleanup);

describe("highlight overlay", () => {
  it("places a box as percentages of its page, with a little room around the text", () => {
    // 72pt in on a 612pt page, 90pt down a 792pt page; 4pt of padding each side
    expect(boxStyle({ x0: 72, y0: 90, x1: 540, y1: 130 }, 612, 792)).toEqual({
      left: `${((68 / 612) * 100).toFixed(4)}%`,
      top: `${((86 / 792) * 100).toFixed(4)}%`,
      width: `${((476 / 612) * 100).toFixed(4)}%`,
      height: `${((48 / 792) * 100).toFixed(4)}%`,
    });
  });

  it("never spills past the page edge", () => {
    const s = boxStyle({ x0: 1, y0: 1, x1: 611, y1: 791 }, 612, 792);
    expect([s.left, s.top, s.width, s.height]).toEqual(["0.0000%", "0.0000%", "100.0000%", "100.0000%"]);
  });

  it("marks the active citation, in its entity's colour", () => {
    const { container } = render(
      <HighlightOverlay box={{ page: 27, x0: 72, y0: 90, x1: 540, y1: 130, page_width: 612, page_height: 792 }}
        pageWidth={612} pageHeight={792} color="#f87171" active n={35} pulse={1} anchor="p-27_1" />,
    );
    const hl = screen.getByTestId("highlight");
    expect(hl.className).toBe("hl is-active");
    expect(hl.getAttribute("aria-current")).toBe("location");
    expect(hl.style.getPropertyValue("--hl")).toBe("#f87171");
    expect(hl.textContent).toBe("35");
    expect(container.querySelector(".hl-arrive")).not.toBeNull();
  });

  it("draws other citations quietly, without the arrival", () => {
    const { container } = render(
      <HighlightOverlay box={{ page: 27, x0: 72, y0: 90, x1: 540, y1: 130, page_width: 612, page_height: 792 }}
        pageWidth={612} pageHeight={792} color="#f87171" active={false} n={36} anchor="p-27_2" />,
    );
    expect(screen.getByTestId("highlight").className).toBe("hl");
    expect(container.querySelector(".hl-arrive")).toBeNull();
  });
});

describe("citation chip", () => {
  it("names the page, the filing when several are cited, and is pressed when active", () => {
    const onSelect = vi.fn();
    render(<CitationChip n={3} citation={citations[2]} active showFiling onSelect={onSelect} />);
    const chip = screen.getByRole("button");
    expect(chip.textContent).toBe("3FY2025p. 27");
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    expect(chip.getAttribute("title")).toBe("10-K · p. 27 · Item 1A. Risk Factors");
    fireEvent.click(chip);
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("keeps an unresolvable citation in place, disabled, with its reason", () => {
    render(<CitationChip n={4} citation={citations[3]} active={false} showFiling onSelect={() => {}} />);
    const chip = screen.getByRole("button");
    expect(chip.hasAttribute("disabled")).toBe(true);
    expect(chip.getAttribute("title")).toBe("no chunk 999");
  });
});

describe("thumbnail rail", () => {
  const pages = fy2025.pages.filter((p) => p.image_url);

  it("shows each rendered page, the current one, and dots for its citations", () => {
    render(<PageThumbnailRail pages={pages} current={27}
      marks={new Map([[27, ["#f87171", "#f87171"]]])} onSelect={() => {}} />);
    const thumbs = within(screen.getByRole("navigation", { name: "Pages" })).getAllByRole("button");
    expect(thumbs.map((t) => t.getAttribute("aria-label"))).toEqual(["Page 26", "Page 27, 2 citations"]);
    expect(thumbs[1].getAttribute("aria-current")).toBe("page");
    expect(thumbs[1].querySelectorAll(".thumb-dot")).toHaveLength(2);
    // thumbnails come through the web app's own origin
    expect(thumbs[0].querySelector("img")?.getAttribute("src"))
      .toBe("/api/document/2/page/26/image?v=1&size=thumb");
  });

  it("goes to a page when its thumbnail is clicked", () => {
    const onSelect = vi.fn();
    render(<PageThumbnailRail pages={pages} current={27} marks={new Map()} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Page 26" }));
    expect(onSelect).toHaveBeenCalledWith(26);
  });
});
