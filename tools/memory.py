#!/usr/bin/env python3
"""
Builds section 4 — Company Memory — and splices it into index.html between the
MEMORY markers. Node positions are computed on two ellipses and the edge paths
are derived from them, so moving a node moves its wires too.

    python3 tools/memory.py
"""
import math, re, os

# calc() inside animation-range is not honoured, so every stagger has to be
# written out as a literal range. These helpers do that.
def rng(a, b):
    return f'animation-range:contain {min(a,100):.2f}% contain {min(b,100):.2f}%'


HERE = os.path.dirname(__file__)
HTML = os.path.join(HERE, '..', 'index.html')

# ---- the graph ------------------------------------------------------------
TOPICS = [
    ('ai',       'AI Infrastructure',  'blue',   -90),
    ('blackwell','Blackwell',          'violet', -18),
    ('cuda',     'CUDA',               'green',   54),
    ('china',    'China Export Risk',  'red',    126),
    ('gaming',   'Gaming Revenue',     'amber',  198),
]
DOCS = [
    ('k22',  '10-K FY2022',   -60, ('ai', 'cuda')),
    ('q1',   'Q1 Earnings',     0, ('blackwell', 'gaming')),
    ('q2',   'Q2 Earnings',    60, ('blackwell', 'cuda')),
    ('inv',  'Investor Day',  120, ('ai', 'china')),
    ('pr',   'Press Release', 180, ('china', 'gaming')),
    ('ceo',  'CEO Letter',    240, ('ai', 'blackwell')),
]

def ellipse(angle, rx, ry):
    a = math.radians(angle)
    return round(50 + rx * math.cos(a), 2), round(50 + ry * math.sin(a), 2)

TPOS = {k: ellipse(a, 25, 27) for k, _, _, a in TOPICS}
DPOS = {k: ellipse(a, 39, 41) for k, _, a, _ in DOCS}

# ---- the timeline ---------------------------------------------------------
YEARS = [
    ('2022', [('10-K FY2022', 'AI Infrastructure first appears', 'ai')]),
    ('2023', [('Q2 Earnings call', 'CUDA moat discussed at length', 'cuda'),
              ('Investor Day', 'Data-centre roadmap expands', 'ai')]),
    ('2024', [('Investor Day', 'Blackwell introduced', 'blackwell'),
              ('8-K', 'China export controls disclosed', 'china')]),
    ('2025', [('10-K FY2025', 'Blackwell revenue reported', 'blackwell'),
              ('Press Release', 'Gaming re-segmented', 'gaming')]),
    ('2026', [('Q1 Earnings call', 'Blackwell supply commentary', 'blackwell')]),
]

# ---- topic explorer -------------------------------------------------------
# Previews are descriptive, not verbatim — the canvas is marked Demo data.
MENTIONS = {
 'blackwell': [
   ('Investor Day', 'Mar 2024', 'Slide 12', 'Architecture introduced alongside a multi-year data-centre roadmap.'),
   ('Q3 Earnings call', 'Nov 2024', 'CFO remarks', 'Ramp described as supply-constrained into the following year.'),
   ('10-K FY2025', 'Item 7 · p.44', 'MD&A', 'Revenue attributed to the architecture reported for the first time.'),
   ('Q1 Earnings call', 'Feb 2026', 'Q&A', 'Commentary on capacity reservations made ahead of demand.')],
 'ai': [
   ('10-K FY2022', 'Item 1 · p.6', 'Business', 'Data-centre segment framed around accelerated computing.'),
   ('Investor Day', 'Jun 2023', 'Slide 31', 'Multi-year infrastructure build-out laid out by region.'),
   ('CEO Letter', 'Apr 2025', 'p.2', 'Compute capacity described as the binding constraint on growth.')],
 'cuda': [
   ('10-K FY2022', 'Item 1A · p.19', 'Risk factors', 'Developer ecosystem named as a competitive advantage.'),
   ('Q2 Earnings call', 'Aug 2023', 'Prepared remarks', 'Installed base and toolchain lock-in discussed.'),
   ('Investor Day', 'Jun 2023', 'Slide 44', 'Library and framework coverage presented as a moat.')],
 'china': [
   ('8-K', 'Oct 2024', 'Item 8.01', 'Licence requirements for certain products disclosed.'),
   ('10-K FY2025', 'Item 1A · p.22', 'Risk factors', 'Revenue exposure to the region quantified.'),
   ('Press Release', 'Jan 2026', '—', 'Compliant product variant announced for the region.')],
 'gaming': [
   ('Q1 Earnings', 'May 2024', 'Segment detail', 'Channel inventory normalisation described.'),
   ('Press Release', 'Sep 2025', '—', 'Segment reporting boundaries restated.'),
   ('10-K FY2025', 'Item 7 · p.51', 'MD&A', 'Year-over-year change decomposed by driver.')],
}

STAGES = [
 ('01', 'Nvidia has no memory.',
  'Every filing, call and deck it has ever published exists — scattered across a decade of PDFs '
  'nobody reads twice. Nothing connects them.'),
 ('02', 'Then you give it documents.',
  'Ten years of 10-Ks, quarterly calls, investor days, press releases and shareholder letters. '
  'Each one enters as a node, not a summary.'),
 ('03', 'It builds a graph.',
  'Topics surface on their own and wire themselves to the documents that discuss them. '
  'No taxonomy to maintain, no tagging to keep up with.'),
 ('04', 'Across four years.',
  'The graph is dated. Every connection knows when it was made, so a topic can be read '
  'as a story rather than a snapshot.'),
 ('05', 'Ask it anything.',
  'Pick a topic and every mention of it comes back in order, with the document, the date '
  'and the page attached to each one.'),
 ('06', 'Watch the story unfold.',
  'Replay how a single topic moved through four years of disclosure — from first mention '
  'to the line item it eventually became.'),
]


def build():
    L = []
    A = '          '

    # radios drive the explorer with no JavaScript at all
    for i, (k, _, _, _) in enumerate(TOPICS):
        chk = ' checked' if k == 'blackwell' else ''
        L.append(f'{A}<input class="sr-only tpick" type="radio" name="cm-topic" id="cm-{k}"{chk}>')

    # --- documents: cards that fly in, then shrink to graph chips ----------
    L.append(f'{A}<div class="cm-docs">')
    for i, (k, label, _, links) in enumerate(DOCS):
        x, y = DPOS[k]
        fx, fy = (x - 50) * 2.6, (y - 50) * 2.6          # where it flies in from
        L.append(f'{A}  <div class="cm-doc d-{k}" style="--x:{x}%;--y:{y}%;'
                 f'--fx:{fx:.0f}%;--fy:{fy:.0f}%;--i:{i};{rng(14 + i * 2.2, 30 + i * 2.2)}">'
                 f'<span class="cm-doc-ico"></span>{label}</div>')
    L.append(f'{A}</div>')

    # --- edges ------------------------------------------------------------
    L.append(f'{A}<svg class="cm-wires" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">')
    n = 0
    for k, _, _, _ in TOPICS:                              # topic -> core
        tx, ty = TPOS[k]
        L.append(f'{A}  <path class="w w-{k}" style="{rng(33 + n * .7, 46 + n * .7)}" '
                 f'd="M50 50 L{tx} {ty}" vector-effect="non-scaling-stroke"/>')
        n += 1
    for dk, _, _, links in DOCS:                           # doc -> topic
        dx, dy = DPOS[dk]
        for tk in links:
            tx, ty = TPOS[tk]
            mx, my = (dx + tx) / 2 + (50 - (dx + tx) / 2) * .18, (dy + ty) / 2 + (50 - (dy + ty) / 2) * .18
            L.append(f'{A}  <path class="w w-{tk}" style="{rng(33 + n * .7, 46 + n * .7)}" '
                     f'd="M{dx} {dy} Q{mx:.1f} {my:.1f} {tx} {ty}" vector-effect="non-scaling-stroke"/>')
            n += 1
    L.append(f'{A}</svg>')

    # --- the company core --------------------------------------------------
    L.append(f'{A}<div class="cm-core"><span class="cm-core-ring"></span>'
             f'<span class="cm-core-ticker">NVDA</span></div>')

    # --- topic nodes (labels for the radios above) -------------------------
    L.append(f'{A}<div class="cm-topics">')
    for i, (k, label, tone, _) in enumerate(TOPICS):
        x, y = TPOS[k]
        L.append(f'{A}  <label class="cm-topic t-{tone}" for="cm-{k}" data-topic="{k}" '
                 f'style="--x:{x}%;--y:{y}%;{rng(31 + i * 2, 41 + i * 2)}">'
                 f'<span class="cm-pip"></span>{label}</label>')
    L.append(f'{A}</div>')

    # --- timeline rail -----------------------------------------------------
    L.append(f'{A}<div class="cm-rail">')
    L.append(f'{A}  <p class="cm-rail-h">Memory timeline</p>')
    L.append(f'{A}  <ol class="cm-years">')
    ev = 0
    for yi, (year, events) in enumerate(YEARS):
        L.append(f'{A}    <li class="cm-year" style="{rng(54 + yi * 2.4, 62 + yi * 2.4)}">'
                 f'<span class="cm-y">{year}</span><ul>')
        for doc, what, tone in events:
            L.append(f'{A}      <li class="cm-ev e-{tone}" style="--i:{ev};{rng(56 + ev * 1.6, 63 + ev * 1.6)}">'
                     f'<span class="cm-dot"></span><b>{doc}</b><span>{what}</span></li>')
            ev += 1
        L.append(f'{A}    </ul></li>')
    L.append(f'{A}  </ol>')
    L.append(f'{A}  <button class="cm-replay" type="button" data-replay>'
             f'<span class="cm-replay-ico" aria-hidden="true">&#9654;</span> Replay Nvidia&rsquo;s AI story</button>')
    L.append(f'{A}</div>')

    # --- topic explorer ----------------------------------------------------
    L.append(f'{A}<div class="cm-explorer">')
    L.append(f'{A}  <div class="cm-exp-top"><span class="cm-exp-t"></span>'
             f'<span class="cm-exp-n"></span></div>')
    for k, label, tone, _ in TOPICS:
        L.append(f'{A}  <ul class="cm-mentions m-{k}">')
        for j, (doc, date, page, quote) in enumerate(MENTIONS[k]):
            L.append(f'{A}    <li style="--i:{j}"><div class="cm-m-top"><span class="cm-m-doc">{doc}</span>'
                     f'<span class="cm-m-date">{date}</span><span class="cm-m-page">{page}</span></div>'
                     f'<p class="cm-m-q">{quote}</p>'
                     f'<button class="cm-m-btn" type="button">View evidence &rarr;</button></li>')
        L.append(f'{A}  </ul>')
    L.append(f'{A}</div>')
    return '\n'.join(L)


def copy_blocks():
    out = []
    for i, (num, head, sub) in enumerate(STAGES):
        out.append(f'        <div class="cm-step s{num}" style="{rng(i * 16, i * 16 + 22)}">'
                   f'<p class="cm-num">{num} <i>/ 06</i></p>'
                   f'<h3 class="cm-h">{head}</h3><p class="cm-p">{sub}</p></div>')
    return '\n'.join(out)


SECTION = f'''
<!-- ===================== 04 — COMPANY MEMORY =====================
     Generated by tools/memory.py — do not hand-edit. -->
<section class="cm" id="memory">
  <div class="cm-stage">
    <header class="cm-head">
      <p class="cm-eyebrow">04 &mdash; Company memory</p>
      <h2 class="cm-h2">Every company gets a memory.</h2>
    </header>

    <div class="cm-cols">
      <div class="cm-copy">
{copy_blocks()}
        <div class="cm-progress" aria-hidden="true"><i></i></div>
      </div>

      <div class="cm-canvas">
        <div class="cm-canvas-top">
          <span class="cm-tick">NVDA</span>
          <span class="cm-demo">Demo data</span>
        </div>
        <div class="cm-field">
{build()}
        </div>
        <p class="cm-narration" aria-hidden="true"><span></span></p>
      </div>
    </div>
  </div>
</section>
'''

src = open(HTML).read()
out = re.sub(r'(<!-- MEMORY:START -->\n).*?(\s*<!-- MEMORY:END -->)',
             lambda m: m.group(1) + SECTION + m.group(2), src, flags=re.S)
open(HTML, 'w').write(out)
print(f'memory written: {len(TOPICS)} topics, {len(DOCS)} documents, '
      f'{SECTION.count("<path class=")} edges, {sum(len(v) for v in MENTIONS.values())} mentions')
