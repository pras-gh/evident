#!/usr/bin/env python3
"""Builds app/index.html — the Company Memory dashboard.

Card content is data here rather than hand-written markup, because the point of
a memory card is that its body is *derived*. Keeping it structured means the
sample stays honest about what the backend would actually produce.

    python3 tools/app_memory.py
"""
import os

HERE = os.path.dirname(__file__)
OUT = os.path.join(HERE, "..", "apps", "web", "reference", "static-sample.html")

NAV = [("home", "Home"), ("watchlist", "Watchlist"), ("ask", "Ask Elevate"),
       ("memory", "Company Memory"), ("pulse", "Market Pulse"),
       ("screeners", "Screeners"), ("alerts", "Alerts"), ("notebook", "Notebook")]

TABS = ["Memory Cards", "Timeline", "Topics", "Promises", "Risks",
        "Financials", "People", "Documents"]

ICONS = {
    "revenue":  '<path d="M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>',
    "ai":       '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2"/>',
    "products": '<path d="M21 8l-9-5-9 5 9 5 9-5Z"/><path d="M3 12l9 5 9-5"/><path d="M3 16l9 5 9-5"/>',
    "guidance": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "risks":    '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    "capital":  '<circle cx="12" cy="12" r="9"/><path d="M12 6v12M15 9H10a2 2 0 0 0 0 4h4a2 2 0 0 1 0 4H9"/>',
    "promises": '<path d="m12 2 3 6.5 7 .9-5 4.8 1.3 7L12 18l-6.3 3.2L7 14.2l-5-4.8 7-.9L12 2Z"/>',
    "headcount":'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
    "rd":       '<path d="M9 2v6L4.6 17a2 2 0 0 0 1.8 3h11.2a2 2 0 0 0 1.8-3L15 8V2"/><path d="M9 2h6M7.5 13h9"/>',
    "chevron":  '<path d="m9 18 6-6-6-6"/>',
    "search":   '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
}

# --- the nine cards ------------------------------------------------------
# `revisions` is the part the reference mockup omits: one entry per filing that
# touched the card, `material` false when it restated without moving anything.
CARDS = [
 dict(kind="revenue", title="Revenue", tone="emerald", source="10-K", updated="2d ago",
      value="$394.3B", sub="FY 2024", delta="2.1% YoY", dir="up",
      spark=[268,274,260,281,290,283,297,305,299,318,330,326,341,355,349,368,381,394],
      revisions=[
        dict(n=4, date="Nov 1, 2024", doc="FY2024 10-K", material=True,
             summary="Revenue rose from $383.3B to $394.3B.",
             facts=["Revenue FY2024 — $394,328M", "Revenue FY2023 — $383,285M"],
             ev="p. 31 · Item 7 MD&A"),
        dict(n=3, date="Aug 2, 2024", doc="Q3 2024 10-Q", material=False,
             summary="Restated without change.", facts=["Revenue FY2023 — $383,285M"],
             ev="p. 12 · Condensed statements"),
        dict(n=2, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="Revenue fell from $394.3B to $383.3B.",
             facts=["Revenue FY2023 — $383,285M"], ev="p. 29 · Item 7 MD&A"),
        dict(n=1, date="Oct 28, 2022", doc="FY2022 10-K", material=True,
             summary="1 new item: Revenue.", facts=["Revenue FY2022 — $394,328M"],
             ev="p. 28 · Item 7 MD&A")]),

 dict(kind="ai", title="AI Strategy", tone="indigo", source="Q2 2024 Call", updated="5d ago",
      headline="Apple Intelligence",
      body="Enterprise AI focus, on-device processing, privacy-first approach.",
      foot='Latest mention in <b>Q2 2024 Call</b>', chevron=True,
      revisions=[
        dict(n=3, date="May 7, 2024", doc="Q2 2024 Earnings Call", material=True,
             summary="Positioning shifted from “machine learning” to on-device “Apple Intelligence”.",
             facts=["Topic — Apple Intelligence", "Topic — on-device inference"],
             ev="CEO prepared remarks"),
        dict(n=2, date="Feb 1, 2024", doc="Q1 2024 Earnings Call", material=True,
             summary="1 new item: generative AI investment.",
             facts=["Topic — generative AI"], ev="Q&A, analyst question 4"),
        dict(n=1, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="1 new item: machine learning R&D.",
             facts=["Topic — machine learning"], ev="p. 6 · Item 1 Business")]),

 dict(kind="products", title="Products", tone="orange", source="Earnings Call", updated="5d ago",
      value="7", sub="Active Categories", chevron=True,
      chips=["iPhone", "Mac", "iPad", "Wearables", "Services", "Vision", "Accessories"],
      foot='Latest: <b>Vision Pro</b> launch',
      revisions=[
        dict(n=3, date="Feb 2, 2024", doc="Q1 2024 Earnings Call", material=True,
             summary="1 new item: Vision Pro. Now shipping.",
             facts=["Vision Pro — shipping", "iPhone — shipping"], ev="CEO prepared remarks"),
        dict(n=2, date="Jun 5, 2023", doc="Investor Day", material=True,
             summary="1 new item: Vision Pro. Announced.",
             facts=["Vision Pro — announced"], ev="Slide 12"),
        dict(n=1, date="Oct 28, 2022", doc="FY2022 10-K", material=True,
             summary="6 new items: iPhone, Mac, iPad, Wearables, Services, Accessories.",
             facts=["6 categories"], ev="p. 4 · Item 1 Business")]),

 dict(kind="guidance", title="Guidance", tone="teal", source="Earnings Call", updated="3w ago",
      value="$117B – $126B", sub="Q1 2025 revenue", foot="Issued Jan 30, 2025",
      revisions=[
        dict(n=3, date="Jan 30, 2025", doc="Q1 2025 Earnings Call", material=True,
             summary="Range widened from $121–126B to $117–126B.",
             facts=["Q1 2025 revenue — $117B–$126B [open]"], ev="CFO prepared remarks"),
        dict(n=2, date="Oct 31, 2024", doc="Q4 2024 Earnings Call", material=True,
             summary="1 new item: Q1 2025 revenue guidance.",
             facts=["Q1 2025 revenue — $121B–$126B [open]"], ev="CFO prepared remarks"),
        dict(n=1, date="Aug 1, 2024", doc="Q3 2024 Earnings Call", material=True,
             summary="Kept: Q4 2024 revenue guidance met.",
             facts=["Q4 2024 revenue — met [kept]"], ev="CFO prepared remarks")]),

 dict(kind="risks", title="Risks", tone="red", source="Risk section", updated="2d ago",
      value="12", sub="Active Risk Factors", chevron=True,
      foot='Top: <b>Regulatory</b>, <b>Competition</b>, <b>Supply chain</b>',
      alert="1 no longer disclosed",
      revisions=[
        dict(n=4, date="Nov 1, 2024", doc="FY2024 10-K", material=True,
             summary="1 new risk factor: AI regulation; 1 no longer disclosed: COVID-19 disruption.",
             facts=["AI regulation", "Regulatory", "Competition", "Supply concentration"],
             ev="p. 12 · Item 1A"),
        dict(n=3, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="1 new risk factor: Supply concentration.",
             facts=["Supply concentration", "Regulatory", "Competition", "COVID-19 disruption"],
             ev="p. 11 · Item 1A"),
        dict(n=2, date="Aug 3, 2023", doc="Q3 2023 10-Q", material=False,
             summary="Restated without change.", facts=["No change to risk factors"],
             ev="p. 22 · Part II Item 1A"),
        dict(n=1, date="Oct 28, 2022", doc="FY2022 10-K", material=True,
             summary="11 new risk factors.", facts=["11 factors disclosed"],
             ev="p. 10 · Item 1A")]),

 dict(kind="capital", title="Capital Allocation", tone="cyan", source="Cash Flow", updated="2d ago",
      value="$110B", sub="Share buybacks (FY 2024)",
      foot='Total returned to shareholders <b>$124B</b> in FY 2024',
      revisions=[
        dict(n=3, date="Nov 1, 2024", doc="FY2024 10-K", material=True,
             summary="Share buybacks rose from $77.5B to $110B.",
             facts=["Buybacks FY2024 — $110,000M", "Dividends FY2024 — $15,234M"],
             ev="p. 34 · Consolidated statements of cash flows"),
        dict(n=2, date="Apr 19, 2024", doc="8-K", material=True,
             summary="1 new item: $110B repurchase authorisation.",
             facts=["Authorisation — $110,000M"], ev="Item 8.01"),
        dict(n=1, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="1 new item: Share buybacks.",
             facts=["Buybacks FY2023 — $77,550M"], ev="p. 33 · Cash flows")]),

 dict(kind="promises", title="Promises", tone="amber", source="CEO statements", updated="3w ago",
      value="5", sub="Active promises",
      pills=[("2", "on track", "ok"), ("3", "in progress", "warn"), ("1", "unclear", "mute")],
      revisions=[
        dict(n=4, date="Jan 30, 2025", doc="Q1 2025 Earnings Call", material=True,
             summary="1 new item: expand Apple Intelligence to more languages in 2025.",
             facts=["Apple Intelligence languages — 2025 [open]"], ev="CEO prepared remarks"),
        dict(n=3, date="Oct 31, 2024", doc="Q4 2024 Earnings Call", material=True,
             summary="Kept: Apple Intelligence ships in US English in 2024.",
             facts=["US English rollout — [kept]"], ev="CEO prepared remarks"),
        dict(n=2, date="May 2, 2024", doc="Q2 2024 Earnings Call", material=True,
             summary="1 new item: on-device processing for most requests.",
             facts=["On-device processing — [open]"], ev="Q&A, analyst question 2"),
        dict(n=1, date="Feb 1, 2024", doc="Q1 2024 Earnings Call", material=True,
             summary="1 new item: Vision Pro availability outside the US in 2024.",
             facts=["Vision Pro international — [unclear]"], ev="CEO prepared remarks")]),

 dict(kind="headcount", title="Headcount", tone="blue", source="10-K", updated="2d ago",
      value="161K", sub="Total employees", delta="3.2% YoY", dir="up",
      foot="As of Sep 28, 2024",
      revisions=[
        dict(n=3, date="Nov 1, 2024", doc="FY2024 10-K", material=True,
             summary="Headcount rose from 161,000 to 164,000.",
             facts=["Employees FY2024 — 164,000"], ev="p. 8 · Item 1 Human capital"),
        dict(n=2, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="Headcount fell from 164,000 to 161,000.",
             facts=["Employees FY2023 — 161,000"], ev="p. 7 · Item 1 Human capital"),
        dict(n=1, date="Oct 28, 2022", doc="FY2022 10-K", material=True,
             summary="1 new item: Headcount.", facts=["Employees FY2022 — 164,000"],
             ev="p. 7 · Item 1 Human capital")]),

 dict(kind="rd", title="R&D", tone="violet", source="10-K", updated="2d ago",
      value="$31.4B", sub="Spend in FY 2024", delta="6.7% YoY", dir="up",
      foot="<b>7.9%</b> of revenue",
      revisions=[
        dict(n=3, date="Nov 1, 2024", doc="FY2024 10-K", material=True,
             summary="R&D rose from $29,915M to $31,370M.",
             facts=["R&D FY2024 — $31,370M"], ev="p. 32 · Item 7 MD&A"),
        dict(n=2, date="Nov 3, 2023", doc="FY2023 10-K", material=True,
             summary="R&D rose from $26,251M to $29,915M.",
             facts=["R&D FY2023 — $29,915M"], ev="p. 30 · Item 7 MD&A"),
        dict(n=1, date="Oct 28, 2022", doc="FY2022 10-K", material=True,
             summary="1 new item: R&D.", facts=["R&D FY2022 — $26,251M"],
             ev="p. 30 · Item 7 MD&A")]),
]

TIMELINE = [
 ("May 7, 2024", "Q2 2024 Earnings Call", "Revenue $90.8B, EPS $1.53", "emerald"),
 ("Apr 19, 2024", "Share buyback announcement", "$110B repurchase authorisation", "cyan"),
 ("Feb 2, 2024", "Q1 2024 Earnings Call", "Revenue $119.6B, EPS $2.18", "emerald"),
 ("Jan 30, 2024", "Services revenue record", "$23.1B, all-time quarterly high", "indigo"),
 ("Dec 22, 2023", "Vision Pro launch", "Available in US markets", "orange"),
]


def icon(name, cls="ic"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" '
            f'aria-hidden="true">{ICONS[name]}</svg>')


def spark(values):
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    n = len(values) - 1
    pts = " ".join(f"{i / n * 100:.2f},{28 - (v - lo) / span * 24:.2f}"
                   for i, v in enumerate(values))
    return (f'<svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}"/></svg>')


def rail(revs):
    """One tick per filing. Filled = something moved; hollow = restated only."""
    ticks = "".join(
        f'<i class="tick{" tick-material" if r["material"] else ""}" '
        f'title="rev {r["n"]} · {r["date"]} · {"material" if r["material"] else "no change"}"></i>'
        for r in reversed(revs))
    material = sum(1 for r in revs if r["material"])
    return (f'<div class="rail"><div class="ticks">{ticks}</div>'
            f'<span class="rail-n">{len(revs)} revisions · {material} material</span></div>')


def card(c):
    body = []
    if c.get("value"):
        delta = ""
        if c.get("delta"):
            arrow = "&#8599;" if c["dir"] == "up" else "&#8600;"
            delta = f'<span class="delta d-{c["dir"]}">{arrow} {c["delta"]}</span>'
        body.append(f'<div class="val-row"><p class="val">{c["value"]}</p>{delta}</div>')
        body.append(f'<p class="sub">{c["sub"]}</p>')
    if c.get("headline"):
        body.append(f'<p class="headline">{c["headline"]}</p>')
    if c.get("body"):
        body.append(f'<p class="body">{c["body"]}</p>')
    if c.get("spark"):
        body.append(spark(c["spark"]))
    if c.get("chips"):
        body.append('<ul class="chips">' + "".join(f"<li>{x}</li>" for x in c["chips"]) + "</ul>")
    if c.get("pills"):
        body.append('<ul class="pills">' + "".join(
            f'<li class="p-{t}"><b>{n}</b> {label}</li>' for n, label, t in c["pills"]) + "</ul>")
    if c.get("alert"):
        body.append(f'<p class="alert">{icon("risks", "ic-xs")} {c["alert"]}</p>')

    foot = c.get("foot", "")
    chev = f'<span class="chev">{icon("chevron", "ic-sm")}</span>' if c.get("chevron") else ""
    return f'''
        <label class="card t-{c["tone"]}" for="open-{c["kind"]}">
          <div class="card-top">
            <span class="badge">{icon(c["kind"])}</span>
            <span class="card-title">{c["title"]}</span>
            {chev}
          </div>
          <div class="card-body">{"".join(body)}</div>
          {rail(c["revisions"])}
          <div class="card-foot">
            <span class="upd">Updated {c["updated"]}</span>
            <span class="src">from <b>{c["source"]}</b></span>
          </div>
          <p class="card-foot-note">{foot}</p>
        </label>'''


def drawer(c):
    revs = "".join(f'''
            <li class="rev{" rev-material" if r["material"] else ""}">
              <div class="rev-head">
                <span class="rev-n">rev {r["n"]}</span>
                <span class="rev-date">{r["date"]}</span>
                <span class="rev-doc">{r["doc"]}</span>
                {'<span class="rev-tag">no change</span>' if not r["material"] else ""}
              </div>
              <p class="rev-sum">{r["summary"]}</p>
              <ul class="rev-facts">{"".join(f"<li>{f}</li>" for f in r["facts"])}</ul>
              <p class="rev-ev">{r["ev"]}</p>
            </li>''' for r in c["revisions"])
    material = sum(1 for r in c["revisions"] if r["material"])
    return f'''
      <aside class="drawer d-{c["kind"]} t-{c["tone"]}" aria-label="{c["title"]} revision history">
        <header class="drawer-top">
          <span class="badge">{icon(c["kind"])}</span>
          <div>
            <p class="drawer-title">{c["title"]}</p>
            <p class="drawer-src">Updates from <b>{c["source"]}</b> ·
               {len(c["revisions"])} revisions, {material} material</p>
          </div>
          <label class="drawer-x" for="open-none" aria-label="Close">&#10005;</label>
        </header>
        <ol class="revs">{revs}</ol>
      </aside>'''


def build():
    radios = '<input class="sr" type="radio" name="open" id="open-none" checked>\n' + "\n".join(
        f'<input class="sr" type="radio" name="open" id="open-{c["kind"]}">' for c in CARDS)
    tabs = "\n".join(
        f'<input class="sr" type="radio" name="tab" id="tab-{i}"{" checked" if i == 0 else ""}>'
        for i in range(len(TABS)))
    tab_labels = "\n".join(
        f'<label class="tab" for="tab-{i}">{t}</label>' for i, t in enumerate(TABS))
    nav = "\n".join(
        f'<a class="nav-item{" is-on" if k == "memory" else ""}" href="#">'
        f'<span class="nav-dot"></span>{label}</a>' for k, label in NAV)
    timeline = "\n".join(f'''
            <li class="tl-item tone-{tone}">
              <span class="tl-dot"></span>
              <p class="tl-date">{d}</p>
              <p class="tl-title">{t}</p>
              <p class="tl-sub">{s}</p>
            </li>''' for d, t, s, tone in TIMELINE)

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Company Memory — Elevate</title>
<meta name="theme-color" content="#0a0a0b">
<link rel="icon" href="../assets/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="app.css">
</head>
<body>
<!-- tabs and the revision drawer are radio inputs; this page runs no JavaScript -->
{radios}
{tabs}

<div class="shell">
  <nav class="side" aria-label="Primary">
    <a class="brand" href="../index.html">
      <span class="brand-mark"></span> elevate
    </a>
    <div class="nav">{nav}</div>
    <div class="promo">
      <p class="promo-t">Upgrade to Pro</p>
      <p class="promo-b">Unlimited memory history, cross-company compare, and alerting.</p>
      <span class="promo-btn">Upgrade now</span>
    </div>
  </nav>

  <main class="main">
    <header class="head">
      <span class="tickmark">AAPL</span>
      <div class="head-id">
        <h1>Apple Inc. <span class="tick-chip">AAPL</span></h1>
        <p class="head-sub">Company Memory</p>
        <p class="head-meta">Memory built from <b>1,248 documents</b> · earliest filing Oct 2014</p>
      </div>
      <div class="head-actions">
        <span class="search">{icon("search", "ic-sm")} Search memory<kbd>/</kbd></span>
        <span class="btn btn-ghost">Compare</span>
        <span class="btn btn-go">Add note</span>
      </div>
    </header>

    <div class="tabs" role="tablist">{tab_labels}<span class="tabs-right">All time</span></div>

    <div class="stage">
      <section class="panel">
        <div class="panel-head">
          <div>
            <h2>Memory cards</h2>
            <p class="panel-sub">Every filing updates the cards it touches. Each card keeps its history.</p>
          </div>
          <span class="demo-chip">Demo data</span>
        </div>
        <div class="grid">{"".join(card(c) for c in CARDS)}</div>
        <label class="view-all" for="open-revenue">Open a card to see its revision history &rarr;</label>
      </section>

      <aside class="rails">
        <section class="rail-card">
          <h3>Recent timeline</h3>
          <ol class="tl">{timeline}</ol>
          <p class="rail-more">View full timeline &rarr;</p>
        </section>
        <section class="rail-card">
          <h3>Memory health</h3>
          <div class="health">
            <svg class="ring" viewBox="0 0 72 72" aria-hidden="true">
              <circle class="ring-bg" cx="36" cy="36" r="31"/>
              <circle class="ring-fg" cx="36" cy="36" r="31"/>
            </svg>
            <div>
              <p class="health-n">98%</p>
              <p class="health-l">Completeness</p>
            </div>
          </div>
          <ul class="health-rows">
            <li><span>Documents parsed</span><b>1,248</b></li>
            <li><span>Cards with history</span><b>9 / 9</b></li>
            <li><span>Claims without evidence</span><b class="ok">0</b></li>
          </ul>
        </section>
      </aside>

      {"".join(drawer(c) for c in CARDS)}
      <label class="scrim" for="open-none" aria-hidden="true"></label>
    </div>
  </main>
</div>
</body>
</html>
'''


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html = build()
    open(OUT, "w").write(html)
    print(f"app/index.html — {len(CARDS)} cards, "
          f"{sum(len(c['revisions']) for c in CARDS)} revisions, {len(html):,} bytes")
