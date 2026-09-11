"""Every page in the notebook, written out as plain HTML strings."""

from html import escape
from urllib.parse import urlencode

import db

TAGLINE = (
    "Take your bet on what you think the future will look like,\n"
    "    get reminded in a few years to observe what happened"
)


def e(value):
    return escape("" if value is None else str(value), quote=True)


def csrf_field(csrf):
    return '<input type="hidden" name="csrf" value="%s">' % e(csrf)


def qs(**parts):
    clean = {k: v for k, v in parts.items() if v not in (None, "", 0)}
    return ("?" + urlencode(clean)) if clean else ""


def date_of(value):
    stamp = db.parse(value)
    return stamp.strftime("%d %B %Y") if stamp else ""


def layout(title, body, user=None, wide_footer=True):
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
        room = '<a href="/enter">sign in</a>'
    room += '<a href="/house">house rules</a>'
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s &middot; The Future with AI Betting Notebook</title>
<link rel="stylesheet" href="/static/notebook.css">
<link rel="icon" href="data:,">
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


def status_stamp(bet):
    if bet["status"] == "open":
        return ""
    quiet = " quiet" if bet["status"] == "unclear" else ""
    return ' <span class="stamp%s">%s</span>' % (quiet, e(db.STATUSES[bet["status"]]))


def entry(bet, user, csrf, with_reasoning=True):
    because = ""
    if with_reasoning and bet["reasoning"].strip():
        because = '<p class="because">%s</p>' % e(bet["reasoning"].strip())
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
        "cat": e(bet["category"]),
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
    return layout("The ledger", body, user)


def bet_page(bet, user, csrf, note=""):
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

    because = (
        '<div class="because">%s</div>' % e(bet["reasoning"].strip())
        if bet["reasoning"].strip()
        else '<p class="hint">No reasoning was written down.</p>'
    )

    body = """%(note)s
<div class="bet-sheet">
  <p class="claim">%(claim)s%(stamp)s</p>
  <p class="colophon">%(cat)s &middot; to be judged by %(year)d &middot;
     written by %(who)s on %(when)s</p>
  %(because)s
  <dl class="record">
    <dt>Found interesting by</dt><dd>%(votes)d %(word)s</dd>
    <dt>Entry number</dt><dd>%(id)d</dd>
  </dl>
  <div class="deeds no-print">
    %(votebtn)s
    %(print)s
    <a class="button" href="/">Back to the ledger</a>
  </div>
  %(verdict)s
  %(resolve)s
</div>""" % {
        "note": note,
        "claim": e(bet["claim"]),
        "stamp": status_stamp(bet),
        "cat": e(bet["category"]),
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
        "print": '<a class="button" href="/print?bet=%d">Print this bet</a>' % bet["id"]
        if user
        else "",
        "verdict": verdict,
        "resolve": resolve,
    }
    return layout(bet["claim"][:60], body, user)


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
    options = "".join(
        '<option%s>%s</option>' % (" selected" if values.get("category") == c else "", e(c))
        for c in db.CATEGORIES
    )
    note = '<div class="notice">%s</div>' % e(error) if error else ""
    lede = FIRST_TIME_RULES if first_time else """<p class="lede">State it so that in ten years a stranger could tell whether you were right.
   A bet is not a banner: what you expect, not what you want &mdash;
   the <a href="/house">house rules</a> put it at more length.</p>"""
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
  <label class="field"><span class="name">Subject</span>
    <select name="category">%(options)s</select></label>
  <label class="field"><span class="name">Judged by the year</span>
    <input type="number" name="horizon" min="%(min)d" max="%(max)d" value="%(horizon)s" required></label>
  <label class="tick"><input type="checkbox" name="anonymous" value="1"%(anon)s>
    Sign this one with no name, whatever my desk says</label>
  <div class="deeds"><button type="submit">Write it into the ledger</button></div>
</form>""" % {
        "note": note,
        "lede": lede,
        "csrf": csrf_field(csrf),
        "claim": e(values.get("claim", "")),
        "reasoning": e(values.get("reasoning", "")),
        "options": options,
        "min": year,
        "max": year + 75,
        "horizon": e(values.get("horizon", year + 5)),
        "anon": " checked" if values.get("anonymous") else "",
    }
    return layout("Propose a bet", body, user)


def enter_page(csrf, error="", sent_to="", link=""):
    if sent_to:
        shortcut = (
            '<p class="hint">This prototype posts nothing to the internet. Your key was'
            ' written to <span class="mono">data/outbox/</span> and printed in the terminal:'
            '<br><a href="%s">%s</a></p>' % (e(link), e(link))
            if link
            else ""
        )
        body = """<div class="notice plain">
  <p>A key has been posted to <b>%s</b>. It opens the notebook once, within the hour.</p>
  %s
</div>""" % (e(sent_to), shortcut)
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
                e(r["category"]),
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
                "cat": e(b["category"]),
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
        "cat": e(bet["category"]),
        "year": bet["horizon"],
        "who": e(db.byline(bet)),
        "when": date_of(bet["created_at"]),
        "because": '<div class="because">%s</div>' % e(bet["reasoning"].strip())
                   if bet["reasoning"].strip() else "",
        "csrf": csrf_field(csrf),
    }
    return layout("Burn an entry", body, user)


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
   &ldquo;I agree with this&rdquo;. Writing a bet of your own needs a pen
   name, because a bet has an author and someone has to settle it later.</p>

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
<p>You sign with a pen name, which you may change or hide at any time. Your
   address is never shown to anyone, and is kept for one reason: to post you
   a key when you sign in, and the yearly letter if you asked for one.</p>

<div class="deeds">
  <a class="button" href="/">Back to the ledger</a>
  <a class="button" href="/propose">Propose a bet</a>
</div>"""
    return layout("The house rules", body, user)


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
            e(b["category"]),
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
