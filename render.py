"""Every page in the notebook, written out as plain HTML strings."""

import difflib
import os
import re
from html import escape
from urllib.parse import quote, urlencode

import db

# Where the notebook answers from, for the links a link preview reads.
# The same variable app.py takes its BASE_URL from; read here rather than
# imported, since app imports render and not the other way about.
SITE_URL = os.environ.get("NOTEBOOK_URL", "http://localhost:8420").rstrip("/")
SITE_NAME = "The Future with AI Betting Notebook"
# What somebody sees when the address is pasted into a chat, above any
# words of ours. A page with something better to say passes its own.
DESCRIPTION = (
    "A public ledger of bets on what the world will look like with artificial "
    "intelligence in it. Write down what you expect, say which year should "
    "judge it, and get a letter when it comes due."
)

TAGLINE = (
    "Take your bet on what you think the future with AI will look like,\n"
    "    get reminded in a few years to observe what happened"
)


def e(value):
    return escape("" if value is None else str(value), quote=True)


def csrf_field(csrf):
    return '<input type="hidden" name="csrf" value="%s">' % e(csrf)


def subject_boxes(chosen):
    """Every subject, to tick. A bet is usually one thing, but some
    genuinely sit at a crossroads - so up to db.MAX_SUBJECTS of them."""
    chosen = set(chosen or [])
    boxes = "".join(
        '<label class="tick"><input type="checkbox" name="subject" value="%s"%s> %s</label>'
        % (e(c), " checked" if c in chosen else "", e(c))
        for c in db.CATEGORIES
    )
    return """<div class="field">
    <span class="name">Subject</span>
    <p class="hint">What it is about. Up to %d of them, if it really sits at a
       crossroads &mdash; the first one down this list is the one it is filed
       under.</p>
    <div class="subjects">%s</div>
  </div>""" % (db.MAX_SUBJECTS, boxes)


def subject_link(subject):
    return '<a href="/subject/%s">%s</a>' % (e(db.subject_slug(subject)), e(subject))


def subject_line(bet, linked=True):
    """Every subject a bet carries, for a meta line. Each one leads to the
    rest of the ledger filed under it."""
    shown = db.subjects_on(bet)
    if not linked:
        return " &middot; ".join(e(s) for s in shown)
    return " &middot; ".join(subject_link(s) for s in shown)


def subjects_anywhere(row):
    """The subjects on either a bet as it stands or an earlier wording of
    one - the two keep them differently."""
    try:
        joined = row["subjects"]
    except (IndexError, KeyError):
        return db.subjects_on(row)
    # Rows written before a bet could carry more than one have the column
    # empty, and only their single category to go on.
    return [s for s in (joined or "").split("|") if s] or [row["category"]]


def subject_line_was(was):
    """The same for an earlier wording, which keeps its own list."""
    return " &middot; ".join(e(s) for s in subjects_anywhere(was))


def qs(**parts):
    clean = {k: v for k, v in parts.items() if v not in (None, "", 0)}
    return ("?" + urlencode(clean)) if clean else ""


def date_of(value):
    stamp = db.parse(value)
    return stamp.strftime("%d %B %Y") if stamp else ""


def social_head(title, description, path=""):
    """The handful of tags that decide what a pasted link looks like.

    `path` is the page's own address, and only a page worth arriving at
    from outside has one: a desk or a printing room says nothing, rather
    than claiming to be the front door."""
    url = SITE_URL + (path or "/")
    # A preview shows the title alone, with no masthead under it, so the
    # name of the place has to travel with the name of the page.
    shown = "%s \u00b7 %s" % (title, SITE_NAME)
    where = ('<link rel="canonical" href="%s">\n<meta property="og:url" content="%s">\n'
             % (e(url), e(url))) if path else ""
    return """<meta name="description" content="%(desc)s">
%(where)s<meta property="og:site_name" content="%(site)s">
<meta property="og:type" content="website">
<meta property="og:title" content="%(title)s">
<meta property="og:description" content="%(desc)s">
<meta property="og:image" content="%(card)s">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="A ruled cream page: The Future with AI, a betting notebook">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="%(card)s">
<meta name="twitter:title" content="%(title)s">
<meta name="twitter:description" content="%(desc)s">""" % {
        "title": e(shown),
        "desc": e(description),
        "where": where,
        "site": e(SITE_NAME),
        "card": e(SITE_URL + "/static/card.png"),
    }


def layout(title, body, user=None, wide_footer=True, description="", path=""):
    if user:
        who = 'signed as <b>%s</b>' % e(user["pseudo"])
        room = (
            '<a href="/propose">propose a bet</a>'
            '<a href="/desk">your desk</a>'
            '<a href="/desk#copies">your copies</a>'
            '<a href="/leave">sign out</a>'
        )
        if db.is_keeper(user):
            room += '<a href="/keep">keep the ledger</a>'
    else:
        who = "not signed"
        # Writing comes before signing: a stranger may take the first door
        # as readily as the second, and is asked for an address at the end.
        room = (
            '<a href="/propose">propose a bet</a>'
            '<a href="/enter">sign in</a>'
        )
    room += '<a href="/house">house rules</a>'
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s &middot; The Future with AI Betting Notebook</title>
%(social)s
<link rel="stylesheet" href="/static/notebook.css">
<link rel="icon" href="/static/notebook.svg" type="image/svg+xml">
<script src="/static/notebook.js" defer></script>
</head>
<body>
<div class="sheet">
  <header class="masthead">
    <h1><a href="/">The Future with AI</a></h1>
    <p class="sub">%(tagline)s</p>
    <div class="rules"></div>
  </header>
  <nav class="hall">
    <a href="/">the ledger</a>
    %(room)s
    <span class="who">%(who)s</span>
  </nav>
  %(body)s
  <footer class="colophon">
    Kept by hand since 2026. Nothing here is a prediction; everything here is a wager.
    %(footer)s
  </footer>
</div>
</body>
</html>
""" % {
        "title": e(title),
        "social": social_head(title, description or DESCRIPTION, path),
        "tagline": TAGLINE,
        "room": room,
        "who": who,
        "body": body,
        "footer": '<br>Entries may be printed, torn out and kept.' if wide_footer else "",
    }


# --- pieces ---------------------------------------------------------------

def vote_control(bet, user, csrf):
    n = bet["votes"]
    word = "vote" if n == 1 else "votes"
    cast = " cast" if bet["voted"] else ""
    mark = "&minus;" if bet["voted"] else "+"
    return (
        '<div class="tally">'
        '<form method="post" action="/bet/%d/vote" class="inline">%s'
        '<span class="count">%d</span>'
        '<button class="vote%s" name="back" value="1" title="mark this one interesting">%s</button>'
        '<span class="word">%s</span>'
        "</form></div>" % (bet["id"], csrf_field(csrf), n, cast, mark, word)
    )


# Passing a bet on. Every one of these is a plain link to the platform's
# own compose page: nothing of theirs loads here, nothing counts who read
# the page, and pressing the link is the only way anything reaches them.
# That is the only kind of share button this notebook is willing to carry.
SHARE_PLACES = (
    ("X", "https://twitter.com/intent/tweet?text=%(saying)s&url=%(where)s"),
    ("Bluesky", "https://bsky.app/intent/compose?text=%(both)s"),
    ("Facebook", "https://www.facebook.com/sharer/sharer.php?u=%(where)s"),
    ("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=%(where)s"),
    ("WhatsApp", "https://wa.me/?text=%(both)s"),
    ("Email", "mailto:?subject=%(subject)s&body=%(both)s"),
)


def share_words(bet):
    """What goes in the box when the platform opens it: the claim, the
    year that settles it, and where it is written down. Whoever is
    sharing can say it better - this is only so nobody has to."""
    claim = " ".join((bet["claim"] or "").split())
    where = "%s/bet/%d" % (SITE_URL, bet["id"])
    return (
        "\u201c%s\u201d \u2014 a bet on the future with AI, to be judged by %d."
        % (claim, bet["horizon"]),
        where,
    )


def share_row(bet):
    saying, where = share_words(bet)
    bits = {
        "saying": quote(saying, safe=""),
        "where": quote(where, safe=""),
        "both": quote("%s\n%s" % (saying, where), safe=""),
        "subject": quote("A bet on the future with AI", safe=""),
    }
    links = "".join(
        '<a class="share" href="%s" target="_blank" rel="noopener nofollow">%s</a>'
        % (e(pattern % bits), e(name))
        for name, pattern in SHARE_PLACES
    )
    # Hidden until the script that makes it work has run: a button that
    # copies nothing is worse than no button at all.
    copy = ('<button type="button" class="share copy-link" hidden'
            ' data-link="%s">Copy link</button>' % e(where))
    return ('<div class="share-row no-print"><span class="label">pass it on</span>'
            "%s%s</div>" % (links, copy))


def status_stamp(bet):
    if bet["status"] == "open":
        return ""
    quiet = " quiet" if bet["status"] == "unclear" else ""
    return ' <span class="stamp%s">%s</span>' % (quiet, e(db.STATUSES[bet["status"]]))


# How much of the reasoning the ledger shows before it offers the rest.
# Long enough to carry the thought, short enough that a screenful of
# entries still reads as a list rather than a wall.
LEDGER_EXCERPT = 180


def shorten(text, limit=LEDGER_EXCERPT, slack=60):
    """The opening of a piece of reasoning and whatever is left of it.

    Cut at a space, so no word is broken in half, and never at all when
    the whole thing is short enough to stand as it is - most reasoning is,
    and an entry that fits has no business wearing a button. The slack is
    there for the paragraph that runs a line over: folding away twenty
    words asks more of a reader than it saves them."""
    text = (text or "").strip()
    if len(text) <= limit + slack:
        return text, ""
    cut = text.rfind(" ", 0, limit)
    if cut < limit // 2:      # one improbably long word: cut where it falls
        cut = limit
    return text[:cut].rstrip(" ,;:-\u2014"), text[cut:].strip()


def entry(bet, user, csrf, with_reasoning=True):
    because = ""
    if with_reasoning and bet["reasoning"].strip():
        opening, rest = shorten(bet["reasoning"])
        if rest:
            # A disclosure, not a script: the rest of the reasoning is on
            # the page already, folded away until it is asked for.
            because = (
                '<details class="because">'
                '<summary>%s<span class="cut">&hellip;</span>'
                '<span class="fold open">see more</span>'
                '<span class="fold shut">see less</span></summary>'
                "<p>%s</p></details>" % (e(opening), e(rest))
            )
        else:
            because = '<p class="because">%s</p>' % e(opening)
    return """<li class="entry">
  %(vote)s
  <div>
    <p class="claim"><a href="/bet/%(id)d">%(claim)s</a>%(stamp)s</p>
    <p class="meta"><span class="cat">%(cat)s</span> &middot; by %(year)d &middot;
       written by %(who)s, %(when)s</p>
    %(because)s
  </div>
</li>""" % {
        "vote": vote_control(bet, user, csrf),
        "id": bet["id"],
        "claim": e(bet["claim"]),
        "stamp": status_stamp(bet),
        "cat": subject_line(bet),
        "year": bet["horizon"],
        "who": e(db.byline(bet)),
        "when": date_of(bet["created_at"]),
        "because": because,
    }


def filter_bar(counts, query, category, status, sort):
    def link(label, extra, active, count=None):
        args = {"q": query, "category": category, "status": status, "sort": sort}
        args.update(extra)
        n = ' <span class="n">%d</span>' % count if count is not None else ""
        return '<a class="%s" href="/%s">%s%s</a>' % (
            "on" if active else "",
            qs(**args),
            e(label),
            n,
        )

    sorts = [
        link("most interesting", {"sort": "interesting"}, sort == "interesting"),
        link("newest", {"sort": "newest"}, sort == "newest"),
        link("soonest horizon", {"sort": "horizon"}, sort == "horizon"),
    ]
    states = [link("all", {"status": ""}, not status)] + [
        link(word, {"status": key}, status == key) for key, word in db.STATUSES.items()
    ]

    return """<div class="filters">
  <div class="row"><span class="label">order</span>%s</div>
  <div class="row"><span class="label">standing</span>%s</div>
</div>""" % ("".join(sorts), "".join(states))


def search_form(query, counts, category, status, sort):
    """One form: the words you are looking for, and the subject to look in."""
    seen = dict(counts)
    names = sorted(set(db.CATEGORIES) | set(seen))
    options = ['<option value="">every subject</option>']
    for name in names:
        n = seen.get(name, 0)
        options.append(
            '<option value="%s"%s>%s (%d)</option>'
            % (e(name), " selected" if category == name else "", e(name), n)
        )
    hidden = "".join(
        '<input type="hidden" name="%s" value="%s">' % (k, e(v))
        for k, v in (("status", status), ("sort", sort))
        if v
    )
    return """<form class="search" method="get" action="/">
  <input type="search" name="q" value="%s" placeholder="search the ledger &mdash; a word, a name, a year">
  <select name="category" onchange="this.form.submit()" aria-label="subject">%s</select>
  %s
  <button type="submit">look</button>
</form>""" % (e(query), "".join(options), hidden)


# --- pages ----------------------------------------------------------------

def index(bets, counts, user, query, category, status, sort, csrf, note=""):
    if bets:
        ledger = '<ol class="ledger">%s</ol>' % "".join(entry(b, user, csrf) for b in bets)
    elif query or category or status:
        ledger = '<p class="lede">Nothing in the ledger matches. Try a wider net, or <a href="/propose">write the bet yourself</a>.</p>'
    else:
        ledger = '<p class="lede">The ledger is empty. Somebody has to go first.</p>'

    head = "The ledger"
    if category:
        head = "The ledger &mdash; %s" % e(category)
    if query:
        head += " &mdash; searching &ldquo;%s&rdquo;" % e(query)

    body = """%(note)s
%(search)s
%(filters)s
<h2>%(head)s <span class="hint">(%(n)d %(word)s)</span></h2>
%(ledger)s
<div class="deeds">
  <a class="button" href="/propose">Propose a bet</a>
  <a class="button" href="/subjects">Browse by subject</a>
  %(take)s
</div>""" % {
        "note": note,
        "search": search_form(query, counts, category, status, sort),
        "filters": filter_bar(counts, query, category, status, sort),
        "head": head,
        "n": len(bets),
        "word": "bet" if len(bets) == 1 else "bets",
        "ledger": ledger,
        "take": (
            '<a class="button" href="/print%(pq)s">Print this selection</a>'
            '<a class="button" href="/export.txt%(pq)s">Take it as plain text</a>'
            % {"pq": qs(q=query, category=category, status=status, sort=sort)}
        )
        if user
        else '<a class="button" href="/enter">Sign in to keep a copy</a>',
    }
    return layout("The ledger", body, user, description=DESCRIPTION, path="/")


def revise_page(bet, user, csrf, values=None, error=""):
    """Changing a bet you wrote. The form says plainly that the old
    wording stays on the page, because that is the whole bargain."""
    values = values or {
        "claim": bet["claim"], "reasoning": bet["reasoning"],
        "subjects": db.subjects_on(bet), "horizon": bet["horizon"],
    }
    year = db.now().year
    note = '<div class="notice">%s</div>' % e(error) if error else ""
    # A horizon already gone by may stand, but cannot be picked afresh.
    low = min(year, bet["horizon"])
    body = """%(note)s
<h2>Change this bet</h2>
<p class="lede">Correct the wording, sharpen the claim, say more about why.
   What it said before stays on the page underneath, with the date it
   changed &mdash; a hand may correct itself here, but not quietly.</p>
<form method="post" action="/bet/%(id)d/revise">
  %(csrf)s
  <label class="field"><span class="name">The claim</span>
    <input type="text" name="claim" maxlength="240" required value="%(claim)s"></label>
  <label class="field"><span class="name">Why you think so</span>
    <textarea name="reasoning" maxlength="4000">%(reasoning)s</textarea></label>
  %(subjects)s
  <label class="field"><span class="name">Judged by the year</span>
    <input type="number" name="horizon" min="%(min)d" max="%(max)d" value="%(horizon)s" required></label>
  <div class="deeds">
    <button type="submit">Write the change in</button>
    <a class="button" href="/bet/%(id)d">Leave it as it is</a>
  </div>
</form>""" % {
        "note": note,
        "id": bet["id"],
        "csrf": csrf_field(csrf),
        "claim": e(values.get("claim", "")),
        "reasoning": e(values.get("reasoning", "")),
        "subjects": subject_boxes(values.get("subjects")),
        "min": low,
        "max": year + 75,
        "horizon": e(values.get("horizon", bet["horizon"])),
    }
    return layout("Change a bet", body, user)


def words_of(text):
    """Words and the spaces between them, so a diff can be put back
    together exactly as it was written."""
    return re.findall(r"\S+|\s+", text or "")


def word_diff(before, after):
    """What was taken out and what was put in, and nothing else.

    The whole wording is kept in the ledger; it is only the page that is
    better for showing the handful of words that moved rather than two
    near-identical paragraphs one above the other."""
    moved = difflib.SequenceMatcher(
        a=words_of(before), b=words_of(after), autojunk=False
    )
    out = []
    for what, i1, i2, j1, j2 in moved.get_opcodes():
        gone = "".join(words_of(before)[i1:i2])
        came = "".join(words_of(after)[j1:j2])
        if what == "equal":
            out.append(e(gone))
        else:
            if gone:
                out.append("<del>%s</del>" % e(gone))
            if came:
                out.append("<ins>%s</ins>" % e(came))
    return "".join(out)


def earlier_wording(bet, earlier):
    """Every change to a bet, newest first, as what went and what came.

    Each row holds the wording it replaced, so what a row was changed
    *into* is the row above it - or the bet as it stands now, for the
    most recent one."""
    if not earlier:
        return ""

    def changes(before, after):
        """Only the fields that actually moved, each shown as a diff."""
        shown = []
        if before["claim"] != after["claim"]:
            shown.append(("The claim",
                          '<p class="claim">%s</p>' % word_diff(before["claim"], after["claim"])))
        if (before["reasoning"] or "").strip() != (after["reasoning"] or "").strip():
            shown.append(("The reasoning",
                          '<p class="because">%s</p>'
                          % word_diff(before["reasoning"], after["reasoning"])))
        had, has = subjects_anywhere(before), subjects_anywhere(after)
        if had != has:
            # A list, so say which went and which came rather than running
            # a word diff over the whole line and repeating the unchanged.
            moved = ["<del>%s</del>" % e(s) for s in had if s not in has]
            moved += ["<ins>%s</ins>" % e(s) for s in has if s not in had]
            kept = [e(s) for s in has if s in had]
            shown.append((
                "The subject" if len(had) == len(has) == 1 else "The subjects",
                '<p class="meta">%s</p>' % " &middot; ".join(kept + moved),
            ))
        if before["horizon"] != after["horizon"]:
            shown.append(("The horizon",
                          '<p class="meta">%s</p>'
                          % word_diff(str(before["horizon"]), str(after["horizon"]))))
        return shown

    blocks = []
    after = bet
    for was in earlier:
        moved = changes(was, after)
        if not moved:
            after = was
            continue
        blocks.append(
            '<li class="was"><p class="when">%s</p>%s</li>'
            % (
                e("Changed %s" % date_of(was["replaced_at"])),
                "".join(
                    '<div class="moved"><span class="which">%s</span>%s</div>' % (e(label), html)
                    for label, html in moved
                ),
            )
        )
        after = was

    if not blocks:
        return ""
    return """<h2>What has changed</h2>
<p class="hint">Every earlier wording is kept; what is shown here is only
   what moved &mdash; <del>struck</del> for what went,
   <ins>underlined</ins> for what came.</p>
<ol class="earlier">%s</ol>""" % "".join(blocks)


def bet_page(bet, user, csrf, note="", earlier=()):
    mine = user and user["id"] == bet["user_id"]
    resolve = ""
    if mine:
        options = "".join(
            '<option value="%s"%s>%s</option>'
            % (key, " selected" if bet["status"] == key else "", e(word))
            for key, word in db.STATUSES.items()
        )
        resolve = """<h2>Settle it</h2>
<p class="hint">Yours to call, whenever the world has made up its mind.</p>
<form method="post" action="/bet/%d/resolve">
  %s
  <label class="field"><span class="name">How it turned out</span>
    <select name="status">%s</select></label>
  <label class="field"><span class="name">A line on why</span>
    <textarea name="verdict" placeholder="What actually happened, and how you judged it.">%s</textarea></label>
  <div class="deeds"><button type="submit">Record the verdict</button></div>
</form>""" % (bet["id"], csrf_field(csrf), options, e(bet["verdict"]))

    verdict = ""
    if bet["status"] != "open":
        verdict = """<h2>The verdict</h2>
<p><span class="stamp">%s</span> &nbsp;recorded %s</p>
<p class="because">%s</p>""" % (
            e(db.STATUSES[bet["status"]]),
            date_of(bet["resolved_at"]),
            e(bet["verdict"] or "No note was left."),
        )

    # The reasoning is the substance of a bet - the claim is only its
    # headline - so it is set out under its own name rather than left to
    # run in with the furniture.
    because = (
        """<div class="reasoning">
    <span class="name">The reasoning</span>
    <div class="because">%s</div>
  </div>""" % e(bet["reasoning"].strip())
        if bet["reasoning"].strip()
        else '<p class="hint">No reasoning was written down.</p>'
    )

    body = """%(note)s
<div class="bet-sheet">
  <p class="claim">%(claim)s%(stamp)s</p>
  <p class="colophon">%(cat)s &middot; to be judged by %(year)d &middot;
     written by %(who)s on %(when)s</p>
  %(because)s
  <div class="bet-foot">
    <dl class="record">
      <dt>Found interesting by</dt><dd>%(votes)d %(word)s</dd>
      %(changed)s
      <dt>Entry number</dt><dd>%(id)d</dd>
    </dl>
    <div class="deeds no-print">
      %(votebtn)s
      %(revise)s
      <a class="button" href="/">Back to the ledger</a>
    </div>
  </div>
  %(share)s
  %(earlier)s
  %(verdict)s
  %(resolve)s
</div>""" % {
        "note": note,
        "claim": e(bet["claim"]),
        "stamp": status_stamp(bet),
        "cat": subject_line(bet),
        "year": bet["horizon"],
        "who": e(db.byline(bet)),
        "when": date_of(bet["created_at"]),
        "because": because,
        "votes": bet["votes"],
        "word": "person" if bet["votes"] == 1 else "people",
        "id": bet["id"],
        "votebtn": (
            '<form method="post" action="/bet/%d/vote" class="inline">%s'
            '<button type="submit" name="back" value="1">%s</button></form>'
            % (bet["id"], csrf_field(csrf), "Take back my vote" if bet["voted"] else "Mark it interesting")
        ),
        "changed": (
            "<dt>Changed</dt><dd>%d %s since it was written</dd>"
            % (bet["revisions"], "time" if bet["revisions"] == 1 else "times")
            if bet["revisions"] else ""
        ),
        "revise": (
            '<a class="button" href="/bet/%d/revise">Change it</a>' % bet["id"]
            if mine and bet["status"] == "open"
            else ""
        ),
        "share": share_row(bet),
        "earlier": earlier_wording(bet, earlier),
        "verdict": verdict,
        "resolve": resolve,
    }
    # A bet pasted into a chat should read as the bet, not as the notebook:
    # the claim is the title, the reasoning behind it the description.
    because = " ".join(bet["reasoning"].split())
    return layout(
        bet["claim"][:60], body, user,
        description=(because[:280] or DESCRIPTION),
        path="/bet/%d" % bet["id"],
    )


# Put in front of anyone writing their first bet. The full rules are at
# /house; this is the part that changes what somebody is about to write.
FIRST_TIME_RULES = """<div class="notice plain first-time">
  <h3>Your first bet &mdash; the house in short</h3>
  <ul class="rules-list">
    <li><b>A bet, not a banner.</b> Write what you <i>expect</i>, not what you
        want. The notebook takes no side on whether any future here is a good
        one, and nothing is struck for being unwelcome or probably wrong.</li>
    <li><b>Something a stranger could settle.</b> In ten years somebody who has
        never met you should be able to say whether you were right, without
        having to ask what you meant.</li>
    <li><b>A year to judge it by.</b> A bet with no horizon is an opinion.</li>
    <li><b>Your reasoning is the point.</b> Say what would prove you wrong;
        that is what makes the entry worth reading later.</li>
  </ul>
  <p>The <a href="/house">house rules</a> say all of it, including what does
     get struck. You will not be shown this again.</p>
</div>"""


def propose_page(user, csrf, values=None, error="", first_time=False):
    values = values or {}
    year = db.now().year
    note = '<div class="notice">%s</div>' % e(error) if error else ""
    lede = FIRST_TIME_RULES if first_time else """<p class="lede">State it so that in ten years a stranger could tell whether you were right.
   A bet is not a banner: what you expect, not what you want &mdash;
   the <a href="/house">house rules</a> put it at more length.</p>"""
    if not user:
        # Write first, sign afterwards: an address is what the letter in
        # ten years needs, and there is no reason to ask for it before
        # there is anything to sign.
        lede += """<p class="hint">You are not signed in, and need not be to write this.
   Write the bet; the last step asks for an address, and the bet goes in
   under whatever pen name you choose then.</p>"""
    body = """%(note)s
<h2>Propose a bet</h2>
%(lede)s
<form method="post" action="/propose">
  %(csrf)s
  <label class="field"><span class="name">The claim</span>
    <input type="text" name="claim" maxlength="240" required
           placeholder="By 2032, most people will assume a photograph is fake until proven otherwise."
           value="%(claim)s"></label>
  <label class="field"><span class="name">Why you think so</span>
    <textarea name="reasoning" maxlength="4000"
              placeholder="The reasoning, the thing that would prove you wrong, what you would accept as settled.">%(reasoning)s</textarea></label>
  %(subjects)s
  <label class="field"><span class="name">Judged by the year</span>
    <input type="number" name="horizon" min="%(min)d" max="%(max)d" value="%(horizon)s" required></label>
  <label class="tick"><input type="checkbox" name="anonymous" value="1"%(anon)s>
    Sign this one with no name, whatever my desk says</label>
  <div class="deeds"><button type="submit">%(deed)s</button></div>
</form>""" % {
        "deed": "Write it into the ledger" if user else "Write it, then sign it",
        "note": note,
        "lede": lede,
        "csrf": csrf_field(csrf),
        "claim": e(values.get("claim", "")),
        "reasoning": e(values.get("reasoning", "")),
        "subjects": subject_boxes(values.get("subjects")),
        "min": year,
        "max": year + 75,
        "horizon": e(values.get("horizon", year + 5)),
        "anon": " checked" if values.get("anonymous") else "",
    }
    return layout("Propose a bet", body, user)


def sign_off_page(csrf, values, error=""):
    """The last step of writing a bet you began before signing in.

    The bet itself is carried back in hidden fields and checked again on
    the way in - nothing here is trusted because it came from our own
    form. Only the address is new, and it is asked for last, once the
    writing is done."""
    note = '<div class="notice">%s</div>' % e(error) if error else ""
    kept = "".join(
        '<input type="hidden" name="%s" value="%s">' % (name, e(value))
        for name, value in (
            ("claim", values.get("claim", "")),
            ("reasoning", values.get("reasoning", "")),
            ("horizon", values.get("horizon", "")),
        )
    ) + "".join(
        '<input type="hidden" name="subject" value="%s">' % e(s)
        for s in values.get("subjects", [])
    ) + ('<input type="hidden" name="anonymous" value="1">'
         if values.get("anonymous") else "")

    body = """%(note)s
<h2>One last thing</h2>
<p class="lede">Your bet is written. It is not in the ledger yet &mdash; leave an
   address and we post you a key; opening it signs the bet in your hand and
   puts it on the page. No passwords are kept here, and the address is never
   shown to anyone.</p>
<div class="bet-sheet quoted">
  <p class="claim">%(claim)s</p>
  <p class="colophon">%(subjects)s &middot; to be judged by %(horizon)s</p>
  %(because)s
</div>
<form method="post" action="/propose/sign">
  %(csrf)s
  %(kept)s
  <label class="field"><span class="name">Your address</span>
    <input type="email" name="email" required placeholder="you@example.org"></label>
  <div class="deeds"><button type="submit">Post me a key and hold the bet</button></div>
</form>
<!-- Going back posts the bet to itself rather than linking to an empty
     form: what somebody has written is not thrown away by a second
     thought about the wording. -->
<form method="post" action="/propose" class="second-thoughts">
  %(csrf)s
  %(kept)s
  <input type="hidden" name="again" value="1">
  <div class="deeds"><button type="submit">Go back and change it</button></div>
</form>
<p class="hint">The key lasts an hour. If it is never opened, the bet is never
   written &mdash; nothing of it is shown to anyone in the meantime.</p>""" % {
        "note": note,
        "csrf": csrf_field(csrf),
        "kept": kept,
        "claim": e(values.get("claim", "")),
        "subjects": " &middot; ".join(e(s) for s in values.get("subjects", [])),
        "horizon": e(values.get("horizon", "")),
        "because": ('<div class="because">%s</div>' % e(values["reasoning"].strip())
                    if values.get("reasoning", "").strip() else ""),
    }
    return layout("One last thing", body)


def enter_page(csrf, error="", sent_to="", link="", keeping=False):
    if sent_to:
        shortcut = (
            '<p class="hint">This prototype posts nothing to the internet. Your key was'
            ' written to <span class="mono">data/outbox/</span> and printed in the terminal:'
            '<br><a href="%s">%s</a></p>' % (e(link), e(link))
            if link
            else ""
        )
        held = (
            "<p>Your bet is being held against that key. It goes into the ledger, in "
            "your hand, the moment the key is opened &mdash; and nowhere at all if it "
            "is not.</p>" if keeping else ""
        )
        body = """<div class="notice plain">
  <p>A key has been posted to <b>%s</b>. It opens the notebook once, within the hour.</p>
  %s
  %s
</div>""" % (e(sent_to), held, shortcut)
        return layout("Key sent", body)

    note = '<div class="notice">%s</div>' % e(error) if error else ""
    body = """%s
<h2>Sign in</h2>
<p class="lede">No passwords are kept here. Leave an address; we post you a key.
   You will be given a pen name, which you may change, hide or keep.</p>
<form method="post" action="/enter">
  %s
  <label class="field"><span class="name">Your address</span>
    <input type="email" name="email" required placeholder="you@example.org"></label>
  <div class="deeds"><button type="submit">Post me a key</button></div>
</form>""" % (note, csrf_field(csrf))
    return layout("Sign in", body)


def desk_page(user, csrf, mine, backed, note="", error=""):
    banner = ""
    if note:
        banner = '<div class="notice plain">%s</div>' % e(note)
    if error:
        banner += '<div class="notice">%s</div>' % e(error)

    def brief(rows, empty):
        if not rows:
            return '<p class="hint">%s</p>' % empty
        return '<ol class="ledger">%s</ol>' % "".join(
            """<li class="entry"><div class="tally"><span class="count">%d</span>
               <span class="word">%s</span></div>
               <div><p class="claim"><a href="/bet/%d">%s</a>%s</p>
               <p class="meta"><span class="cat">%s</span> &middot; by %d</p></div></li>"""
            % (
                r["votes"],
                "vote" if r["votes"] == 1 else "votes",
                r["id"],
                e(r["claim"]),
                status_stamp(r),
                subject_line(r),
                r["horizon"],
            )
            for r in rows
        )

    body = """%(banner)s
<h2>Your desk</h2>
<form method="post" action="/desk">
  %(csrf)s
  <label class="field"><span class="name">Pen name</span>
    <input type="text" name="pseudo" maxlength="32" required value="%(pseudo)s"></label>
  <p class="hint">Want to be incognito? Pick a stupid one &mdash; we don't care, and
     nothing here checks. It is a label for your bets, not your name.</p>
  <label class="tick"><input type="checkbox" name="show_pseudo" value="1"%(show)s>
    Show my pen name beside my bets</label>
  <p class="hint">Unticked, every bet of yours reads as an unsigned hand. Your address is
     never shown to anyone, either way.</p>
  <label class="tick"><input type="checkbox" name="yearly_letter" value="1"%(yearly)s>
    Post me one letter a year, to see how the future turned out</label>
  <p class="hint">One letter, every twelve months: your bets, the ones you found
     interesting, and which of them have come due. Address on file: %(email)s</p>
  <div class="deeds"><button type="submit">Save the desk</button></div>
</form>

<h2>Bets you wrote (%(nmine)d)</h2>
%(mine)s

<h2>Bets you found interesting (%(nback)d)</h2>
%(backed)s

<h2 id="copies">Your copies</h2>
<p class="lede">Nothing here is kept for you anywhere but on paper and on your own
   machine. Take what you want to keep.</p>
<div class="filters">
  <div class="row"><span class="label">your own bets</span>
    <a href="/print?mine=1">print sheet</a><a href="/export.txt?mine=1">plain text</a></div>
  <div class="row"><span class="label">bets you backed</span>
    <a href="/print?backed=1">print sheet</a><a href="/export.txt?backed=1">plain text</a></div>
  <div class="row"><span class="label">the whole ledger</span>
    <a href="/print">print sheet</a><a href="/export.txt">plain text</a></div>
</div>
<p class="hint">A print sheet opens ready for your printer &mdash; the notebook furniture
   drops away and only the bets are on the page. Any search or subject you are looking
   at on the ledger can be printed the same way, from the foot of the ledger itself.</p>""" % {
        "banner": banner,
        "csrf": csrf_field(csrf),
        "pseudo": e(user["pseudo"]),
        "show": " checked" if user["show_pseudo"] else "",
        "yearly": " checked" if user["yearly_letter"] else "",
        "email": e(user["email"]),
        "nmine": len(mine),
        "mine": brief(mine, "Nothing yet. The ledger is waiting."),
        "nback": len(backed),
        "backed": brief(backed, "You have not marked anything interesting yet."),
    }
    return layout("Your desk", body, user)


def keep_page(user, csrf, counts, bets, hands, query="", note="", error=""):
    """The moderation desk: every entry, struck or standing, and the hands
    that wrote them."""
    banner = ""
    if note:
        banner = '<div class="notice plain">%s</div>' % e(note)
    if error:
        banner += '<div class="notice">%s</div>' % e(error)

    def deed(label, deed_name, bet_id, why_box=False, danger=False):
        """One button, and nothing clever: no script has to run for the
        moderation desk to work."""
        reason = (
            '<input type="text" name="why" maxlength="200" placeholder="why, for the record">'
            if why_box else '<input type="hidden" name="why" value="">'
        )
        return (
            '<form method="post" action="/keep" class="inline">%s'
            '<input type="hidden" name="deed" value="%s">'
            '<input type="hidden" name="bet" value="%d">%s'
            '<button type="submit" class="%s">%s</button></form>'
            % (csrf_field(csrf), deed_name, bet_id, reason, "danger" if danger else "", label)
        )

    entries = []
    for b in bets:
        struck = bool(b["removed_at"])
        deeds = (
            deed("Put it back", "restore", b["id"])
            if struck
            else deed("Strike it", "strike", b["id"], why_box=True)
        )
        # Burning cannot be undone, so it is asked twice - on a page of its
        # own, not in a dialog that needs script to appear.
        deeds += deed("Burn it", "burn", b["id"], danger=True)
        entries.append(
            """<li class="entry%(cls)s">
  <div class="tally"><span class="count">%(votes)d</span><span class="word">%(word)s</span></div>
  <div>
    <p class="claim"><a href="/bet/%(id)d">%(claim)s</a>%(mark)s</p>
    <p class="meta"><span class="cat">%(cat)s</span> &middot; by %(year)d &middot;
       written by %(who)s, %(when)s &middot; entry %(id)d</p>
    %(why)s
    <div class="deeds no-print">%(deeds)s</div>
  </div>
</li>""" % {
                "cls": " struck" if struck else "",
                "votes": b["votes"],
                "word": "vote" if b["votes"] == 1 else "votes",
                "id": b["id"],
                "claim": e(b["claim"]),
                "mark": ' <span class="stamp">struck out</span>' if struck else status_stamp(b),
                "cat": subject_line(b),
                "year": b["horizon"],
                "who": e(db.byline(b)),
                "when": date_of(b["created_at"]),
                "why": '<p class="hint">Struck %s%s</p>' % (
                    date_of(b["removed_at"]),
                    " &mdash; %s" % e(b["removed_why"]) if b["removed_why"] else "",
                ) if struck else "",
                "deeds": deeds,
            }
        )

    rows = []
    for h in hands:
        rows.append(
            """<tr><td>%(pseudo)s</td><td class="n">%(standing)d</td>
               <td class="n">%(struck)d</td><td>%(since)s</td><td>%(deed)s</td></tr>"""
            % {
                "pseudo": e(h["pseudo"]),
                "standing": h["standing"],
                "struck": h["struck"],
                "since": date_of(h["created_at"]),
                "deed": (
                    '<form method="post" action="/keep" class="inline">%s'
                    '<input type="hidden" name="deed" value="strike-hand">'
                    '<input type="hidden" name="hand" value="%d">'
                    '<input type="hidden" name="why" value="everything by this hand">'
                    '<button type="submit">Strike all</button></form>'
                    % (csrf_field(csrf), h["id"])
                ) if h["standing"] else '<span class="hint">nothing standing</span>',
            }
        )

    body = """%(banner)s
<h2>Keeping the ledger</h2>
<p class="lede">%(bets)d standing, %(struck)d struck out, %(people)d %(hands)s,
   %(votes)d %(marks)s. The <a href="/house">house rules</a> say what belongs
   here; this is where that is enforced.</p>

<form class="search" method="get" action="/keep">
  <input type="search" name="q" value="%(query)s"
         placeholder="find an entry &mdash; a word, a name, a year">
  <button type="submit">look</button>
</form>

<h2>Every entry (%(n)d)</h2>
<p class="hint">Striking rules a line through an entry: it leaves the ledger
   and can be put back. Burning takes the page out altogether.</p>
<ol class="ledger">%(entries)s</ol>

<h2>The hands (%(people)d)</h2>
<table class="hands">
  <tr><th>pen name</th><th class="n">standing</th><th class="n">struck</th>
      <th>since</th><th></th></tr>
  %(rows)s
</table>""" % {
        "banner": banner,
        "bets": counts["bets"],
        "struck": counts["struck"],
        "people": counts["people"],
        "hands": "hand" if counts["people"] == 1 else "hands",
        "votes": counts["votes"],
        "marks": "mark" if counts["votes"] == 1 else "marks",
        "query": e(query),
        "n": len(bets),
        "entries": entries and "".join(entries) or '<p class="hint">Nothing to show.</p>',
        "rows": "".join(rows),
    }
    return layout("Keeping the ledger", body, user)


def burn_page(user, csrf, bet):
    """Asked twice, because there is no third time."""
    body = """<div class="notice">
  <p>Entry %(id)d would be taken out of the book altogether &mdash; the bet,
     its reasoning and every mark on it. There is no getting it back.</p>
</div>
<div class="bet-sheet">
  <p class="claim">%(claim)s</p>
  <p class="colophon">%(cat)s &middot; to be judged by %(year)d &middot;
     written by %(who)s on %(when)s</p>
  %(because)s
</div>
<p class="lede">If it only needs to leave the ledger, strike it instead:
   that can be undone.</p>
<div class="deeds">
  <form method="post" action="/keep" class="inline">%(csrf)s
    <input type="hidden" name="deed" value="burn-for-good">
    <input type="hidden" name="bet" value="%(id)d">
    <input type="hidden" name="why" value="">
    <button type="submit" class="danger">Yes, burn entry %(id)d</button></form>
  <a class="button" href="/keep">Leave it alone</a>
</div>""" % {
        "id": bet["id"],
        "claim": e(bet["claim"]),
        "cat": subject_line(bet),
        "year": bet["horizon"],
        "who": e(db.byline(bet)),
        "when": date_of(bet["created_at"]),
        "because": '<div class="because">%s</div>' % e(bet["reasoning"].strip())
                   if bet["reasoning"].strip() else "",
        "csrf": csrf_field(csrf),
    }
    return layout("Burn an entry", body, user)


def subjects_page(user, counts):
    """Every subject, as a way in. Plain links, so a reader can browse by
    subject and a crawler can find every corner of the ledger."""
    seen = dict(counts)
    rows = "".join(
        """<li><a href="/subject/%s">%s</a>
             <span class="n">%d %s</span></li>"""
        % (e(db.subject_slug(c)), e(c), seen.get(c, 0),
           "bet" if seen.get(c, 0) == 1 else "bets")
        for c in db.CATEGORIES
    )
    body = """<h2>The subjects</h2>
<p class="lede">Twelve of them, fixed. A bet may sit under as many as three,
   so the same entry can be found from more than one of these.</p>
<ul class="subject-list">%s</ul>
<div class="deeds">
  <a class="button" href="/">The whole ledger</a>
  <a class="button" href="/propose">Propose a bet</a>
</div>""" % rows
    return layout(
        "The subjects", body, user,
        description="Every subject the notebook keeps bets under, from education "
                    "and work to law, climate and war.",
        path="/subjects",
    )


def subject_page(bets, counts, user, subject, status, sort, csrf):
    """One subject's slice of the ledger, at an address of its own."""
    if bets:
        ledger = '<ol class="ledger">%s</ol>' % "".join(entry(b, user, csrf) for b in bets)
    else:
        ledger = ('<p class="lede">Nothing is filed under this one yet. '
                  '<a href="/propose">Write the first.</a></p>')
    body = """%(filters)s
<h2>%(subject)s <span class="hint">(%(n)d %(word)s)</span></h2>
%(ledger)s
<div class="deeds">
  <a class="button" href="/subjects">All the subjects</a>
  <a class="button" href="/">The whole ledger</a>
  <a class="button" href="/propose">Propose a bet</a>
</div>""" % {
        "filters": filter_bar(counts, "", subject, status, sort),
        "subject": e(subject),
        "n": len(bets),
        "word": "bet" if len(bets) == 1 else "bets",
        "ledger": ledger,
    }
    return layout(
        subject, body, user,
        description="Bets on what artificial intelligence will mean for %s, "
                    "each with a year by which it should be judged." % subject,
        path="/subject/%s" % db.subject_slug(subject),
    )


def house_page(user=None):
    """What the place is for, and what it is not for."""
    body = """<h2>The house rules</h2>

<p class="lede">This is a book of wagers on what the world will look like with
   artificial intelligence in it. It is open to anyone, and it asks one thing
   in return: that what you write down is a bet, not a banner.</p>

<h2>A bet is not a wish</h2>
<p>Writing that something <i>will</i> happen is not arguing that it
   <i>should</i>. The notebook takes no side on whether any future here is a
   good one. People who expect very different things, and who want very
   different things, are meant to be able to write in the same book without
   it becoming an argument about who is on whose side.</p>
<p>The useful question here is never &ldquo;is this the future I want?&rdquo;
   It is &ldquo;would I put money on it?&rdquo; &mdash; and, in a few years,
   &ldquo;was I right?&rdquo;</p>

<h2>What makes a good entry</h2>
<ul class="rules-list">
  <li><b>Something that can be settled.</b> State it so that in ten years a
      stranger could tell whether you were right, without having to ask what
      you meant.</li>
  <li><b>A year to judge it by.</b> A bet with no horizon is an opinion.</li>
  <li><b>Your reasoning, and what would change your mind.</b> The reasoning is
      often worth more than the claim.</li>
  <li><b>Your own verdict, when the time comes.</b> The hand that wrote a bet
      is the one that settles it &mdash; including when it turns out wrong.
      Being wrong on the page is the point of keeping the book.</li>
</ul>

<h2>Marking one interesting</h2>
<p>Anyone may mark a bet interesting, with no account and no name &mdash; it
   is one click, and it says &ldquo;this one is worth watching&rdquo;, not
   &ldquo;I agree with this&rdquo;.</p>
<p>A mark with no name behind it is counted by the address it came from: one
   per entry, so clearing your cookies and marking again does nothing, and a
   house or an office with one connection has one mark between them on any
   given bet. Sign in and the mark is yours instead &mdash; one each, kept for
   good, wherever you read from &mdash; and anything you marked before signing
   in comes with you.</p>

<h2>Writing one</h2>
<p>You need not sign in first. Write the bet; the last step asks for an
   address and posts you a key, and the bet goes into the ledger when you open
   it &mdash; in your hand, under whatever pen name you pick. A bet has an
   author because somebody has to settle it later. If the key is never opened,
   the bet is never written, and nothing of it is shown to anyone.</p>

<h2>What gets struck out</h2>
<p>Very little, and reluctantly. The ledger is kept by hand, and a line is
   ruled through an entry when it is:</p>
<ul class="rules-list">
  <li>not a bet at all &mdash; advertising, or a slogan with a year stapled
      to it;</li>
  <li>aimed at a private person, rather than at the world;</li>
  <li>unsettleable on purpose, written so that no outcome could ever count
      against it;</li>
  <li>against the law, or an attempt to use the book to reach somebody who
      does not want to be reached.</li>
</ul>
<p>A struck entry is ruled through rather than torn out, and it can be put
   back: a judgement made by one person at one moment is exactly the sort of
   thing that ought to be reversible. A bet is <b>not</b> struck for being
   unpopular, uncomfortable, or probably wrong. Most of these bets will be
   wrong. That is what a book of wagers looks like.</p>

<h2>Your name and your address</h2>
<p>You sign with a pen name, which you may change or hide at any time. Nothing
   here checks it against anything: if you would rather be nobody, pick a
   stupid one. Your address is never shown to anyone, and is kept for one
   reason: to post you a key when you sign in, and the yearly letter if you
   asked for one.</p>

<div class="deeds">
  <a class="button" href="/">Back to the ledger</a>
  <a class="button" href="/propose">Propose a bet</a>
</div>"""
    return layout("The house rules", body, user, path="/house",
                  description="What the notebook is for, in plain words: a bet is what "
                              "you expect and not what you want, and the house takes no "
                              "side on whether any future here is a good one.")


def print_page(bets, heading, subheading):
    entries = "".join(
        """<li class="entry"><div class="tally"><span class="count">%d</span>
           <span class="word">%s</span></div>
           <div><p class="claim">%s%s</p>
           <p class="meta"><span class="cat">%s</span> &middot; by %d &middot; %s, %s</p>
           %s</div></li>"""
        % (
            b["votes"],
            "vote" if b["votes"] == 1 else "votes",
            e(b["claim"]),
            status_stamp(b),
            subject_line(b),
            b["horizon"],
            e(db.byline(b)),
            date_of(b["created_at"]),
            '<p class="because">%s</p>' % e(b["reasoning"].strip()) if b["reasoning"].strip() else "",
        )
        for b in bets
    )
    body = """<h2>%(heading)s</h2>
<p class="lede">%(sub)s</p>
<p class="printed-note">Printed %(today)s from the Future with AI betting notebook.</p>
<div class="deeds no-print">
  <button type="button" id="print-this">Print this sheet</button>
  <a class="button" href="/">Back to the ledger</a>
</div>
<ol class="ledger">%(entries)s</ol>""" % {
        "heading": e(heading),
        "sub": e(subheading),
        "today": db.now().strftime("%d %B %Y"),
        "entries": entries or '<p class="lede">Nothing to print.</p>',
    }
    return layout("Print", body, None, wide_footer=False)


def message_page(title, text, user=None, link="/"):
    body = """<h2>%s</h2>
<p class="lede">%s</p>
<div class="deeds"><a class="button" href="%s">Back to the ledger</a></div>""" % (
        e(title),
        e(text),
        e(link),
    )
    return layout(title, body, user)
