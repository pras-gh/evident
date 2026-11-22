/**
 * The Parallx mark, drawn inline so it takes the colour of the text around it
 * (`currentColor`) — a dark sidebar, a light page, a header that changes
 * colour as you scroll. Recreated as SVG from the brand image; the source of
 * truth is public/brand/parallx-logo.svg.
 */
export function ParallxMark({ size = 18, className }: { size?: number; className?: string }) {
  return (
    <svg className={className} width={size} height={size} viewBox="370 230 563 562"
         aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth={size < 24 ? 44 : 26}
         strokeLinecap="round" strokeLinejoin="round">
        <path d="M385 706 V528 L666 246 L793 438" />
        <path d="M661 276 V418 L449 640" />
        <path d="M385 706 L447 646 L498 735 L620 776 L918 480 L793 292 L741 346" />
        <path d="M500 718 L793 438 L918 480" />
      </g>
    </svg>
  );
}
