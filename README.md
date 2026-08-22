# Ellivate — landing page

Evidence-backed company research. A static, dependency-free landing page: a
search-first hero over a pixel-art world you travel down through — sky, meadow,
the buried archive, and back up into the light at the CTA.

```bash
python3 -m http.server 4321
```

Then open <http://localhost:4321>. No build step, no packages.

## The two-layer idea

The pixel art is **scenery**. The interface is **crisp modern sans on paper-white
cards** floating over it. That contrast is deliberate and it is what keeps this
reading as a research tool rather than a game — pixel type everywhere would sink
it. Silkscreen appears only on micro-labels, badges and the depth readout.

| File | What's in it |
| --- | --- |
| `index.html` | Page content + a generated parallax world (do not hand-edit between the `WORLD:` markers) |
| `styles.css` | The whole visual system, including the scroll choreography |
| `main.js` | Motion toggle, the self-typing search box, click-to-fill, fallbacks |
| `tools/sprites.py` | **The pixel art itself**, as editable ASCII grids |
| `tools/scene.py` | Composes the world and splices it into `index.html` |
| `assets/*.svg` | Compiled sprites — 14 files, ~21KB total |

## Editing the art

Sprites are ASCII grids with a colour key, compiled to SVG `<rect>` runs. SVG
rather than PNG means they stay perfectly crisp at any size with no
`image-rendering` hacks, and the whole set is 21KB.

```bash
python3 tools/sprites.py   # ASCII grids  -> assets/*.svg
python3 tools/scene.py     # scatter them -> index.html
```

To change a cloud, edit the grid in `tools/sprites.py` and re-run both. To change
how many sunflowers grow in the field, edit the loop counts in `tools/scene.py`.

## How the motion works

Six parallax layers, each one taller than the viewport, each panning by
`(its height − 100vh)` across the page scroll. A taller layer travels further and
therefore reads as nearer. One CSS scroll-driven animation per layer:

```css
@keyframes pan { to { transform: translate3d(0, calc(100vh - var(--h)), 0) } }
.layer { animation: pan linear both; animation-timeline: scroll(root block) }
```

Sky colour is four hard-banded gradient panes cross-faded by scroll position, and
each layer fades in and out of its own chapter. Every one of those is a single
animation spanning the whole scroll with keyframe percentages — **not** several
range-limited animations stacked, which silently leaks each animation's fill
state outside its own range.

Sprites that stand on a horizon are anchored by their feet inside a `.standing`
wrapper, so a tall tree and a tuft of grass share one ground line that a single
`--line` value controls.

Nothing runs on a per-frame JavaScript loop. Browsers without `animation-timeline`
fall back to a passive, rAF-coalesced scroll listener that sets one custom
property, `--sp`, which the same rules read through `calc()` and `clamp()`.

Measured in-browser over a full-page fast scroll at 1280×800: **120fps, worst
frame 9.4ms, zero frames over 16.9ms** — with 241 sprites, 116 specks and 152
ridge columns on the page. It is cheap because only six elements actually
animate; everything else is a static child along for the ride.

`Motion on/off` in the header stops all of it and persists to `localStorage`.
`prefers-reduced-motion: reduce` defaults it to off.

The typewriter in the search box is a low-frequency `setTimeout` chain. It pauses
when the hero scrolls out of view, when the tab is hidden, when motion is off, and
the instant a human focuses or types into the field.

## Scroll choreography

| Scroll | What you see |
| --- | --- |
| 0 – 12% | Sky. Clouds, sun, birds. The hero. |
| 12 – 26% | The treeline rises; the meadow horizon comes up to meet you |
| 26 – 44% | You pass through the ground; soil closes over |
| 44 – 80% | The archive: buried filings, roots, lantern light |
| 80 – 100% | You surface into a sunflower field. The CTA. |

Retiming a chapter means moving one `--line` value and the matching keyframe
percentages together — the layer's `--h` sets its speed, the `--line` sets where
its horizon sits, and they interact.

## Before you launch — placeholders to replace

Everything below is written to look finished but is **not** real:

1. **Coverage numbers** — `5,000+ companies`, `20 yrs`, `<60s` in the `#coverage`
   band, repeated in the hero and FAQ. Marked with an HTML comment.
2. **Form endpoints** — both search forms `GET` to `https://app.ellivate.com/ask`.
   Search `TODO` in `index.html`.
3. **Email / domain** — `hello@ellivate.com` does not exist yet.
4. **The evidence panel** (`#evidence`) is labelled *Illustrative* on purpose. Its
   source excerpts are descriptive paraphrase, not verbatim filing text, and the
   caption under it says so. If you swap in real filing quotes, keep them verbatim
   and attributed — don't drop the caption and leave invented text behind it.
