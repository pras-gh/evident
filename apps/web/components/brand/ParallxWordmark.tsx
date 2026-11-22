/**
 * The PARALLX wordmark — the logo's own letterforms (the A's are open Λ's),
 * not a font approximating them. Drawn inline in `currentColor`; `height` is
 * the cap height in pixels, and the strokes stay thin at any size.
 */
export function ParallxWordmark({ height = 11, className }: { height?: number; className?: string }) {
  return (
    <svg className={className} height={height} width={(height * 696) / 70} viewBox="266 847 696 70"
         aria-hidden="true" focusable="false">
      <g fill="none" stroke="currentColor" strokeWidth={10} strokeLinecap="butt"
         strokeLinejoin="miter">
        <path d="M272 912 V853 H302 Q322 853 322 870 Q322 887 302 887 H272" />
        <path d="M372 912 L401 853 L430 912" />
        <path d="M478 912 V853 H508 Q528 853 528 870 Q528 887 508 887 H478 M506 887 L532 912" />
        <path d="M582 912 L611 853 L640 912" />
        <path d="M692 853 V907 H737" />
        <path d="M795 853 V907 H840" />
        <path d="M900 853 L955 912 M955 853 L900 912" />
      </g>
    </svg>
  );
}
