# @evident/web

Next.js frontend for Company Memory.

```bash
npm install
npm run dev          # proxies /api/* to the FastAPI service via next.config.mjs
```

## The design decision worth keeping

The original reference mockup rendered each memory card as a **stat tile** —
current value plus a source chip. That chrome is right and is kept. What it
missed is that a memory card *is its revision history*: `Revenue $394.3B` is a
number any dashboard shows; that it has been revised four times, three of them
materially, is not.

So `RevisionRail` puts one tick per filing on every card front — filled when
something moved, hollow when the filing restated it without changing anything.
The hollow ticks are shown on purpose. A 10-Q that repeated a number is part of
that number's story, and "4 updates, 3 material" is more honest than implying
every filing mattered.

`RevisionDrawer` opens the full trail with diffs and evidence.

## Data flow

Card *summaries* come with the page (server component). Card *history* is
fetched when a card is opened — nine full trails is a lot of payload for
something most readers open one of.

## reference/static-sample.html

The zero-JavaScript design sample this was ported from, kept because it is
useful to diff against when the React version drifts. It runs standalone with
no build step.
