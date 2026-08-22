# Ellivate — landing page

Evidence-backed company research. A static, dependency-free landing page built to
convert: search-first hero, an illustrated evidence panel, and a scroll experience
that flies the visitor down through a vortex of filings toward a single CTA.

```bash
python3 -m http.server 4321
```

Then open <http://localhost:4321>. There is no build step — three files, no packages.

| File | What's in it |
| --- | --- |
| `index.html` | Page content, plus a generated block of decorative 3D geometry (rings + vortex motes) |
| `styles.css` | Everything visual, including the scroll-driven flight |
| `main.js` | Motion toggle, the self-typing search box, click-to-fill, and fallbacks |

## How the motion works

The dive is **one composited transform**. `.flight` holds every ring and mote inside a
`transform-style: preserve-3d` scene, and a single CSS scroll-driven animation pushes it
20,000px toward the camera as the document scrolls:

```css
.flight { animation: dive linear both; animation-timeline: scroll(root block); }
```

Nothing runs on a per-frame JavaScript loop. The depth readout, progress bar, HUD cues,
sticky CTA and section reveals are all scroll- or view-timeline animations too. Browsers
without `animation-timeline` fall back to a passive, rAF-coalesced scroll listener in
`main.js` that sets a couple of custom properties — same visual result, slightly more work.

Measured in-browser during a full-page fast scroll: ~118fps, zero frames over 16.9ms.

The typewriter in the search box is a low-frequency `setTimeout` chain. It pauses when the
hero leaves the viewport, when the tab is hidden, when motion is switched off, and the
moment a human focuses or types into the field.

`Motion on/off` in the top-right kills all of it and persists the choice to `localStorage`.
`prefers-reduced-motion: reduce` defaults it to off.

## Before you launch — placeholders to replace

Everything below is written to look finished but is **not** real. Search the source for
these before shipping:

1. **Coverage numbers** — `5,000+ companies`, `20 yrs`, `<60s` in the `#coverage` band and
   repeated in the hero and FAQ. Marked with an HTML comment.
2. **Form endpoints** — both search forms `GET` to `https://app.ellivate.com/ask`. Point
   them at the real app (search `TODO` in `index.html`).
3. **Email / domain** — `hello@ellivate.com` does not exist yet.
4. **The evidence panel** (`#evidence`) is labelled *Illustrative* on purpose. The source
   excerpts are descriptive paraphrase, not verbatim filing text, and the visible caption
   says so. If you swap in real filing quotes, keep them verbatim and attributed — don't
   quietly drop the caption and leave invented text in place.

## Editing the vortex

The rings and motes are generated markup, not hand-written. Regeneration script lives in
the commit history; the knobs that matter are in `styles.css`:

- `--dive` — total Z travel (higher = faster rush per unit of scroll)
- `.scene { perspective }` — 800px; lower is a wider, more aggressive tunnel
- `.fog` — the black core, the green annulus, and the outer vignette, in that order

Mobile halves the geometry (`.ring:nth-child(2n)`) and shortens the perspective.
