#!/usr/bin/env python3
"""Draw the link-preview cards: one per year a bet can be judged by.

    python3 make_cards.py              the years a bet can carry from today
    python3 make_cards.py 2026 2065    a range of your own

A pasted bet shows a picture, and the same picture under every bet makes
every bet look like the same bet. What varies between one bet and the
next, though, is a year - and there are only ever a few dozen of those.
So they are drawn once, here, and served as ordinary static files: no
image library at runtime, no drawing on the fly, nothing to go wrong on
the machine that answers the requests.

This is a build step, like redrawing static/card.png, and needs something
that can turn an SVG into a PNG: rsvg-convert, or Chrome, whichever is on
the machine. The notebook itself never runs it, and a year with no card
falls back to the notebook's own (see render.card_for).
"""

import os
import subprocess
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
CARDS = os.path.join(ROOT, "static", "cards")
HORIZON_YEARS = 75          # as far ahead as a bet may be written; see app.bet_trouble

CARD = """<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#fbf9f4"/>
  <rect x="0" y="0" width="1200" height="630" fill="none" stroke="#e9e3d6" stroke-width="24"/>
  <g stroke="#d8cfba" stroke-width="2">
    <line x1="120" y1="150" x2="1080" y2="150"/>
    <line x1="120" y1="286" x2="1080" y2="286"/>
  </g>
  <line x1="168" y1="60" x2="168" y2="570" stroke="#e0bdb4" stroke-width="3"/>

  <text x="200" y="132" font-family="Palatino, Georgia, serif" font-size="34"
        fill="#726a5e" letter-spacing="6">A BETTING NOTEBOOK</text>

  <text x="200" y="252" font-family="Palatino, Georgia, serif" font-size="76"
        fill="#2a2724">The Future with AI</text>

  <text x="200" y="368" font-family="Palatino, Georgia, serif" font-size="40"
        font-style="italic" fill="#726a5e">a bet to be judged by</text>

  <text x="196" y="536" font-family="Palatino, Georgia, serif" font-size="180"
        fill="#93301f" letter-spacing="4">%(year)d</text>

  <path d="M96 300 l26 30 l50 -60" fill="none" stroke="#93301f" stroke-width="14"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>
"""

# Chrome will not size a page to an SVG on its own; in a page of its own,
# with the margins taken off, it renders at exactly the size asked for.
PAGE = """<!doctype html><meta charset="utf-8">
<style>html,body{margin:0;padding:0;background:#fbf9f4}svg{display:block}</style>
%s"""

CHROMES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
)


def have(command):
    try:
        subprocess.run([command, "--version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def renderer():
    """Whatever on this machine can turn an SVG into a 1200x630 PNG."""
    if have("rsvg-convert"):
        return "rsvg", "rsvg-convert"
    for chrome in CHROMES:
        if os.path.isfile(chrome) or have(chrome):
            return "chrome", chrome
    sys.exit(
        "Nothing here can draw a PNG. Install librsvg (rsvg-convert) or Chrome,\n"
        "or draw static/cards/<year>.png by hand - a year with no card simply\n"
        "falls back to the notebook's own picture."
    )


def draw(kind, tool, year, into):
    svg = CARD % {"year": year}
    with tempfile.TemporaryDirectory() as work:
        source = os.path.join(work, "card.svg")
        with open(source, "w") as fh:
            fh.write(svg)
        if kind == "rsvg":
            subprocess.run(
                [tool, "-w", "1200", "-h", "630", "-o", into, source],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return
        page = os.path.join(work, "card.html")
        with open(page, "w") as fh:
            fh.write(PAGE % svg)
        subprocess.run(
            [tool, "--headless", "--disable-gpu", "--hide-scrollbars",
             "--force-device-scale-factor=1", "--window-size=1200,630",
             "--screenshot=%s" % into, "--virtual-time-budget=2000",
             "file://%s" % page],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def main():
    first = date.today().year
    last = first + HORIZON_YEARS
    if len(sys.argv) == 3:
        first, last = int(sys.argv[1]), int(sys.argv[2])
    kind, tool = renderer()
    os.makedirs(CARDS, exist_ok=True)
    drawn = 0
    for year in range(first, last + 1):
        into = os.path.join(CARDS, "%d.png" % year)
        draw(kind, tool, year, into)
        drawn += 1
        sys.stdout.write("\r  %d cards drawn with %s" % (drawn, kind))
        sys.stdout.flush()
    print("\n  %s\n" % CARDS)


if __name__ == "__main__":
    main()
