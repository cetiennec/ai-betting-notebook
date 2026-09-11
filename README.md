# The Future with AI — Betting Notebook

Take your bet on what you think the future with AI will look like; get reminded
in a few years to observe what happened.

People write a bet down, say by when it should be judged, and mark the ones
worth watching. Once a year the notebook writes back to tell you what has come
due. It keeps no opinion of its own about whether any of it is good news —
a bet here is what you *expect*, not what you *want*, and the `/house` page
says so at more length.

Live at **[thefuturewithai.org](https://thefuturewithai.org)**.

**Python standard library only** — no `pip install`, no build step, no
framework. Storage is one SQLite file; pages are server-rendered HTML strings;
mail goes out over `smtplib` or into a local outbox folder.

## Run it

```sh
python3 app.py seed        # optional: twelve example bets by three example hands
python3 app.py             # http://localhost:8420
```

Sign in with any address. With no `SMTP_HOST` set nothing leaves the machine:
the key you are "mailed" is printed in the terminal, written to `data/outbox/`,
**and** shown on the page, so you can click straight through. Passwords do not
exist here.

```sh
python3 app.py --port 9000                   # somewhere else
python3 app.py send-letters                  # post the yearly letters that are due
python3 app.py send-letters --force --email you@example.org
python3 app.py backup                        # data/backups/notebook-<when>.sqlite3
python3 test_notebook.py                     # the tests; -v to name each one
```

## What it does

| | |
|---|---|
| **Propose** | a claim, the reasoning behind it, a subject, and the year by which it should be judged. A hand writing its first bet gets the house rules in short above the form, once |
| **Mark** | one "interesting" mark per bet, toggleable; the ledger sorts by it. No account needed — a signed-in mark is tied to your user, an anonymous one to a browser cookie |
| **Change** | the hand that wrote a bet can correct it while it is open. The ledger keeps every earlier wording in full; the page shows only what moved, struck for what went and underlined for what came. A hand may correct itself, but not quietly |
| **Settle** | the author records how it turned out, with a line on why. A settled bet is left as it was written |
| **Subjects** | twelve of them, from education and work & economy to war & security and love & friendship. A bet may carry up to three: some genuinely sit at a crossroads, and filing a malpractice claim under *health & medicine* alone loses whoever went looking under *law & rights* |
| **Search** | one box for words, a box beside it for the subject; both combine with standing and order. Every subject is also a page of its own at `/subject/<name>`, listed at `/subjects` |
| **Sign in** | email only. A one-shot key, valid an hour, and a session that lasts ninety days |
| **Pen name** | shown or hidden, as a standing preference at your desk or per bet. Your address is never shown either way |
| **Print** | *your copies*, at your desk: your own bets, the ones you backed, or the whole ledger — as a print sheet or plain text |
| **Yearly letter** | opt in at your desk: one letter a year with your bets, the ones you backed, and which have come due |
| **House rules** | `/house` — what the place is for, what makes a good entry, and what gets struck out |
| **Keeping it** | `/keep` — the moderation desk, for whoever is named in `NOTEBOOK_KEEPERS` |

## Layout

```
app.py                 the server, the routes, the CLI, the example ledger
db.py                  schema and every query
render.py              every page, as plain HTML strings
mail.py                letters: the login key and the once-a-year letter
test_notebook.py       the tests
static/notebook.css    the whole look — paper, ink, and the print rules
static/notebook.js     the only script: the print button
data/                  SQLite file, outbox, backups  (git-ignored)
Dockerfile, fly.toml   how it is deployed
```

## The environment it reads

| | |
|---|---|
| `NOTEBOOK_URL` | the address it answers on. Used in the links inside letters, and `https://` turns on `Secure` cookies and HSTS |
| `NOTEBOOK_DB` | where the SQLite file lives (default `data/notebook.sqlite3`) |
| `NOTEBOOK_HOST` / `PORT` | what to listen on (default `127.0.0.1:8420`) |
| `NOTEBOOK_KEEPERS` | comma-separated addresses allowed at `/keep` |
| `NOTEBOOK_TRUST_FORWARDED` | set to `1` only behind a proxy that rewrites `X-Forwarded-For` itself. Fly is recognised without it |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` | a real mail server. Without `SMTP_HOST` letters go to `data/outbox/` |

## Keeping the ledger

Moderation lives at `/keep`, and only for the addresses in `NOTEBOOK_KEEPERS`.
There is deliberately no way to become a keeper from inside the notebook — the
list is read from the environment and nowhere else. To anyone not on it,
`/keep` is a blank page rather than a locked door: a stranger has no business
learning that the notebook has a keeper at all.

A keeper can **strike** an entry (a line ruled through it — it leaves the
ledger, keeps a reason, and can be put back), **burn** one (gone for good,
asked twice on a page of its own), or strike **everything by one hand** when
somebody turns out to be a spammer. A keeper's own hand cannot be struck from
the desk, and a keeper may never edit somebody else's words — rewriting a bet
is worse than removing it.

## Tests

Eighty-three of them, standard library only like the rest. Each starts a real
notebook on a free port with its own throwaway ledger and talks to it over
HTTP, so the socket and header layers are covered rather than mocked — several
of the things they guard against only exist down there. They run on every
push, and nothing reaches the live notebook that has not passed them; see
`.github/workflows/`.

## Sending mail for real

Point it at a real server with environment variables — no code changes. The
yearly letters are not on a timer: run `send-letters` from cron once a day and
it writes only to people whose twelve months are up. Here that cron is
`.github/workflows/letters.yml`, which knocks at the front door to wake the
machine (it stops when nobody is reading) and then runs the command over
`flyctl ssh`.

## Keeping a copy

One machine, one volume, one SQLite file, so a copy lives somewhere else.
`python3 app.py backup` uses SQLite's own backup rather than `cp` — a database
copied while it is being written to is a file with half a transaction in it.
`.github/workflows/backup.yml` runs it nightly on Fly, fetches the file, checks
it opens and passes `PRAGMA integrity_check`, and keeps it for ninety days.

## Being found, and being pasted somewhere

Every page carries a description and Open Graph tags, so an address dropped
into a chat shows what the place is; a single bet shows the claim and the
reasoning behind it rather than the notebook's name. `/robots.txt` welcomes
crawlers to the public hall and keeps them out of the desk, the sign-in form
and the printing room; `/sitemap.xml` lists the ledger, the house rules and
every entry that has not been struck.

The card a link shows is `static/card.png`, drawn from `static/card.svg`; if
you change the wording, redraw it with any SVG renderer:

```sh
rsvg-convert -w 1200 -h 630 -o static/card.png static/card.svg
```

## Where it still falls short

It is built for one machine and a handful of people, and it is honest about
which of its guarantees are real:

- **An anonymous mark is a cookie.** Clearing it marks again — the deliberate
  trade for letting people weigh in without an account. It is not free: one
  address may be handed only a few new anonymous hands a day
  (`app.ANON_HANDS_PER_IP`), so a broom is tedious rather than a ballot box.
- **It runs on `ThreadingHTTPServer`**, which is fine at this size and is not
  a real WSGI server.
- **A notebook on an `https://` address will not open without `SMTP_HOST`.**
  In the prototype the sign-in key is shown on the page, which in public is a
  door rather than a shortcut — anyone could ask for a key to an address they
  do not hold and read it off the screen. The notebook refuses to start rather
  than allow that.

What it does carry: CSRF tokens on every form (a double-submit cookie), rate
limiting on the sign-in form (five keys per address, twenty per visitor, each
per fifteen minutes — see `db.rate_limited`), length caps on everything written
down, `Secure` cookies and a content security policy over https, and a request
body it refuses to read past 64KB.
