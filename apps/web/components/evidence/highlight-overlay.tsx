import type { CSSProperties } from "react";
import type { BoundingBox } from "@/lib/types";

/** Breathing room around the text, in points, so the highlight frames the
 *  paragraph rather than touching its first and last glyphs. */
const PAD = 4;

/**
 * A box in page points as CSS percentages of its page. The page element keeps
 * the page's aspect ratio, so the overlay scales with the image at any width
 * or zoom without measuring anything.
 */
export function boxStyle(
  box: Pick<BoundingBox, "x0" | "y0" | "x1" | "y1">,
  pageWidth: number,
  pageHeight: number,
): CSSProperties {
  const x0 = Math.max(0, box.x0 - PAD);
  const y0 = Math.max(0, box.y0 - PAD);
  const x1 = Math.min(pageWidth, box.x1 + PAD);
  const y1 = Math.min(pageHeight, box.y1 + PAD);
  const pct = (v: number, of: number) => `${((v / of) * 100).toFixed(4)}%`;
  return {
    left: pct(x0, pageWidth),
    top: pct(y0, pageHeight),
    width: pct(x1 - x0, pageWidth),
    height: pct(y1 - y0, pageHeight),
  };
}

/**
 * One citation drawn over its paragraph on the page image.
 *
 * The highlight is state, not an animation: it is there for as long as the
 * citation is active, however far the reader scrolls away and back. Only the
 * arrival animates — keyed on `pulse`, so selecting the same citation again
 * replays it.
 */
export function HighlightOverlay({
  box,
  pageWidth,
  pageHeight,
  color,
  active,
  n,
  pulse,
  anchor,
}: {
  box: BoundingBox;
  pageWidth: number;
  pageHeight: number;
  color: string;
  active: boolean;
  n?: number;
  pulse?: number;
  anchor: string;
}) {
  return (
    <div
      className={`hl${active ? " is-active" : ""}`}
      style={{ ...boxStyle(box, pageWidth, pageHeight), ["--hl" as string]: color }}
      data-testid="highlight"
      data-anchor={anchor}
      aria-current={active ? "location" : undefined}
      aria-label={n ? `Citation ${n}` : "Citation"}
    >
      {active && <span key={pulse} className="hl-arrive" aria-hidden />}
      {n != null && <span className="hl-n">{n}</span>}
    </div>
  );
}
