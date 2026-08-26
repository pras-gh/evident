#!/usr/bin/env python3
"""
Pixel art source of truth.

Each sprite is an ASCII grid + a colour key. This compiles them to SVG made of
<rect> runs, which stay perfectly crisp at any scale (no image-rendering hacks,
no raster assets). Edit the grids below and re-run:

    python3 tools/sprites.py
"""
import os, re

PALETTE = {
    'W': '#ffffff',  'w': '#dceefb',  'v': '#c3ddf2',   # cloud white / shade / deep shade
    'Y': '#ffd94d',  'y': '#f2ae1c',  'o': '#d97706',   # sun + petal yellows
    'n': '#8a5a1e',                                      # sunflower centre
    'L': '#8ed44f',  'G': '#4fa653',  'g': '#357a3d',   # grass / leaf / deep leaf
    'B': '#8a5c37',  'b': '#5c3a20',                     # bark
    'E': '#a8763f',  'e': '#8a5c2e',  'd': '#3d2717',   # root tan / darker / earth
    'P': '#fffdf5',  'p': '#ded7c4',  'I': '#3a3428',   # paper / paper shade / ink
    'A': '#ffc65c',  'a': '#e08c1e',                     # lantern amber
    'K': '#2b2119',  'S': '#7b93a3',                     # outline / bird slate
    '.': None,
}

SPRITES = {}

SPRITES['cloud-a'] = """
......WWWWWW......
....WWWWWWWWWWW...
..WWWWWWWWWWWWWWW.
.WWWWWWWWWWWWWWWWW
WWWWWWWWWWWWWWWWWW
WWWWWWWWWWWWWWWWWW
.wwwwwwwwwwwwwwww.
..wwww......wwww..
"""

SPRITES['cloud-b'] = """
.........WWWWWW..........
.......WWWWWWWWWW........
.....WWWWWWWWWWWWWW..WWW.
...WWWWWWWWWWWWWWWWWWWWWW
..WWWWWWWWWWWWWWWWWWWWWWW
.WWWWWWWWWWWWWWWWWWWWWWWW
WWWWWWWWWWWWWWWWWWWWWWWWW
.wwwwwwwwwwwwwwwwwwwwwww.
..wwwww......wwwwwww.....
"""

SPRITES['cloud-c'] = """
....WWWW....
..WWWWWWWW..
.WWWWWWWWWW.
WWWWWWWWWWWW
.wwwwwwwwww.
..ww....ww..
"""

SPRITES['sun'] = """
.....YYYYYY.....
...YYYYYYYYYY...
..YYYYYYYYYYYY..
.YYYYYYYYYYYYYY.
.YYYYYYYYYYYYYY.
YYYYYYYYYYYYYYYY
YYYYYYYYYYYYYYYY
YYYYYYYYYYYYYYYY
YYYYYYYYYYYYYYYY
YYYYYYYYYYYYYYYY
.YYYYYYYYYYYYYY.
.yyyyyyyyyyyyyy.
..yyyyyyyyyyyy..
...yyyyyyyyyy...
.....yyyyyy.....
................
"""

SPRITES['pine'] = """
.......KK.......
......KGGK......
......KGGK......
.....KGGGGK.....
....KGGGGGGK....
....KGgggGGK....
...KGGGGGGGGK...
..KGGGGGGGGGGK..
..KGgggggggGGK..
.KGGGGGGGGGGGGK.
KGGGGGGGGGGGGGGK
KGgggggggggggGGK
..KKKKBBBBKKKK..
......BBBB......
......BbbB......
......BBBB......
"""

SPRITES['tree'] = """
......KKKKKK......
....KKGGGGGGKK....
..KKGGGGGGGGGGKK..
.KGGGGGGGGGGGGGGK.
KGGGGGGGGGGGGGGGGK
KGGGGGGGGGGGGGGGGK
KGGgggGGGGGGgggGGK
KGGGGGGGGGGGGGGGGK
.KGGGGGGGGGGGGGGK.
..KKGGGGGGGGGGKK..
....KKKBBBBKKK....
.......BBBB.......
.......BbbB.......
.......BBBB.......
.......BbbB.......
......BBBBBB......
"""

SPRITES['sunflower'] = """
.....yYy.....
....yYYYy....
...oYnnnYo...
..yYnnnnnYy..
..yYnnnnnYy..
...oYnnnYo...
....yYYYy....
.....yYy.....
......G......
......G......
.LLG..G......
LLLLLGG......
.LLG..G..GLL.
......GGLLLLL
......G..GLL.
......G......
.LLG..G......
LLLLLGG......
.LLG..G......
......G......
......G......
......G......
"""

SPRITES['grass'] = """
..L....L...L....
.LL..L.LL.LL..L.
LLL.LLLLLLLLL.LL
GGGGGGGGGGGGGGGG
"""

SPRITES['bird'] = """
SS.....SS
.SS...SS.
..SSSSS..
"""

SPRITES['doc'] = """
PPPPPPPPPPPPp.
PIIIIIIIIIIPp.
PPPPPPPPPPPPp.
PIIIIIIIPPPPp.
PPPPPPPPPPPPp.
PIIIIIIIIIIPp.
PPPPPPPPPPPPp.
PIIIIIPPPPPPp.
PPPPPPPPPPPPp.
PIIIIIIIIIIPp.
PPPPPPPPPPPPp.
PIIIIIIIPPPPp.
PPPPPPPPPPPPp.
ppppppppppppp.
"""

SPRITES['lantern'] = """
...KKKK...
...K..K...
..KKKKKK..
.KAAAAAAK.
.KAAAAAAK.
.KAaaaaAK.
.KAaaaaAK.
.KAAAAAAK.
..KKKKKK..
...KKKK...
"""

SPRITES['root'] = """
....EE....
....Ee....
...EEe....
...EE.....
..EEe.....
..EE..EE..
.EEe.EEe..
.EE..EE...
EEe..Ee...
EE...EE...
"""

# square mark for the browser tab
SPRITES['favicon'] = """
................
.....yYYYy......
....yYYYYYy.....
...oYYnnnYYo....
..yYYnnnnnYYy...
..yYnnnnnnnYy...
..yYYnnnnnYYy...
...oYYnnnYYo....
....yYYYYYy.....
.....yYYYy......
.......GG.......
..LL...GG...LL..
.LLLLLLGGLLLLLL.
..LL...GG...LL..
.......GG.......
.......GG.......
"""

SPRITES['crystal'] = """
...AA...
..AAAA..
.AAAAAA.
.AaaaaA.
.AaaaaA.
..AaaA..
...AA...
"""


def grid(art):
    rows = [r for r in art.strip('\n').split('\n')]
    w = max(len(r) for r in rows)
    return [r.ljust(w, '.') for r in rows], w, len(rows)


def to_svg(name, art):
    rows, w, h = grid(art)
    parts = []
    for y, row in enumerate(rows):
        x = 0
        while x < w:
            ch = row[x]
            if PALETTE.get(ch) is None:
                x += 1
                continue
            run = 1
            while x + run < w and row[x + run] == ch:      # merge horizontal runs
                run += 1
            parts.append(f'<rect x="{x}" y="{y}" width="{run}" height="1" fill="{PALETTE[ch]}"/>')
            x += run
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}" shape-rendering="crispEdges">'
            + ''.join(parts) + '</svg>\n'), w, h, len(parts)


if __name__ == '__main__':
    out = os.path.join(os.path.dirname(__file__), '..', 'apps', 'marketing', 'assets')
    os.makedirs(out, exist_ok=True)
    for name, art in SPRITES.items():
        svg, w, h, n = to_svg(name, art)
        open(os.path.join(out, name + '.svg'), 'w').write(svg)
        print(f'{name:12s} {w:>3}x{h:<3} {n:>4} rects  {len(svg):>5}b')
