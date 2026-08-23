# Company Memory — product UI

The application surface, separate from the marketing site at the repo root.

`index.html` is a design sample of the Company Memory dashboard: nine memory
cards, a timeline rail, and a revision drawer.

## The one change from the mockup

The reference mockup renders each card as a stat tile — a current value plus
"from 10-K". That's the right chrome, but it drops the thing that makes a memory
card different from a KPI tile: **a card is its revision history**.

`"Revenue $394.3B"` is a number any dashboard shows. What only this product can
show is that the number has been revised four times, three of those materially,
and that a risk factor quietly stopped being disclosed between two 10-Ks.

So every card front carries a **revision rail** — one tick per filing that
touched it, filled when something moved, hollow when the filing restated without
change. Opening a card reveals the full trail with diffs and evidence.

Built with no JavaScript: tabs and the drawer are radio inputs.
