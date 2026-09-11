# The Future with AI — Betting Notebook

Take your bet on what you think the future will look like; get reminded in a few
years to observe what happened.

People write a bet down, say by when it should be judged, and vote on the ones
worth watching. Once a year the notebook writes back to tell you what has come
due.

A working prototype: **Python standard library only** — no `pip install`, no
build step, no network. Storage is one SQLite file; pages are server-rendered
HTML; mail is written to a local outbox folder.

## Run it

```sh
python3 app.py seed        # optional: twelve example bets by three example hands
python3 app.py             # http://localhost:8420
```

Sign in with any address. Nothing is posted to the internet: the key you are
"mailed" is printed in the terminal, written to `data/outbox/`, **and** shown on
the page, so you can click straight through. Passwords do not exist here.

```sh
python3 app.py --port 9000                  # somewhere else
python3 app.py send-letters                 # post the yearly letters that are due
python3 app.py send-letters --force --email you@example.org   # see one now
```

## What it does

| | |
|---|---|
| **Propose** | a claim, the reasoning behind it, a subject, and the year by which it should be judged |
| **Vote** | one "interesting" mark per bet, toggleable; the ledger sorts by it. No account needed — a signed-in vote is tied to your user, an anonymous one to a browser cookie, and neither is strongly deduplicated beyond that |
| **Subjects** | twelve categories — education, politics & governance, work & economy, information & trust, science & technology, health & medicine, art & culture, everyday life, war & security, climate & environment, law & rights, love & friendship |
| **Search** | one box for words, a box beside it for the subject; both combine with standing and order |
| **Sign in** | email only. A one-shot key, valid an hour. You are given a pen name you can change |
| **Pen name** | shown or hidden, as a standing preference at your desk or per bet. Your address is never shown either way |
| **Print** | *your copies*, at your desk: your own bets, the ones you backed, or the whole ledger — as a print sheet or plain text. Any search or subject you are looking at prints the same way from the foot of the ledger |
| **Settle** | the author of a bet can record how it turned out, with a line on why |
| **Yearly letter** | opt in at your desk: one letter a year with your bets, the ones you backed, and which have come due |
| **House rules** | `/house` — what the place is for, in plain words: a bet is what you *expect*, not what you *want*, and the notebook takes no side on whether any future here is a good one |
| **Keeping it** | `/keep` — the moderation desk, for whoever is named in `NOTEBOOK_KEEPERS` |

Taking a copy is something you do in your own space rather than in the public
hall: **your copies** lives at your desk, and the foot of the ledger will print
whatever you are currently looking at — search, subject, standing and order
included. Print sheets drop the notebook furniture and leave only the bets on
the page.

## Layout

```
app.py                 the server, the routes, the CLI, the example ledger
db.py                  schema and every query
render.py              every page, as plain HTML strings
mail.py                letters: the login key and the once-a-year letter
test_notebook.py       the tests
static/notebook.css    the whole look — paper, ink, and the print rules
data/                  SQLite file + outbox   (git-ignored, safe to delete)
Dockerfile, fly.toml   how it is deployed
```

## Keeping the ledger

Moderation lives at `/keep`, and only for the addresses named here:

```sh
export NOTEBOOK_KEEPERS="you@yourdomain,someone.else@yourdomain"
```

There is deliberately no way to become a keeper from inside the notebook —
the list is read from the environment and nowhere else. To anyone not on it,
`/keep` is a blank page rather than a locked door: a stranger has no business
learning that the notebook has a keeper at all.

A keeper can **strike** an entry (a line ruled through it — it leaves the
ledger, keeps a reason, and can be put back), **burn** one (gone for good,
asked twice on a page of its own), or strike **everything by one hand** when
somebody turns out to be a spammer. A keeper's own hand cannot be struck from
the desk. What belongs in the book, and what does not, is written out for
everyone at `/house`.

## Tests

```sh
python3 test_notebook.py        # all of them
python3 test_notebook.py -v     # and what each one is for
```

Standard library only, like the rest: each test starts a real notebook on a
free port with its own throwaway ledger and talks to it over HTTP, so the
socket and header layers are covered rather than mocked. They run on every
push, and nothing reaches the live notebook that has not passed them —
see `.github/workflows/`.

## Sending mail for real

The outbox is a stand-in. Point it at a real server with environment
variables — no code changes:

```sh
export SMTP_HOST=smtp.example.org SMTP_PORT=587 \
       SMTP_USER=notebook SMTP_PASS=... SMTP_FROM="notebook@yourdomain"
export NOTEBOOK_URL=https://yourdomain          # used in the links inside letters
python3 app.py send-letters
```

The yearly letters are not on a timer. Run `send-letters` from cron once a day;
it only writes to people whose twelve months are up.

## Before this is more than a prototype

It listens on `127.0.0.1` only, and is built for one machine and a handful of
people. It now carries CSRF tokens on every form (a double-submit cookie),
rate limiting on the sign-in form (five keys per address, twenty per visitor,
each per fifteen minutes — see `db.rate_limited`), length caps on everything
that is written down, `Secure` cookies and a content security policy when it
is served over https, and a body it refuses to read past 64KB.

An anonymous vote is still a cookie, and clearing it votes again - the
deliberate trade for letting people weigh in without an account. It is no
longer free, though: one address may be handed only a few new anonymous hands
a day (`app.ANON_HANDS_PER_IP`), so a broom is tedious rather than a ballot
box. Someone who already holds a hand may change their mind as often as they
like.

Still wanting, before it is more than a prototype: a real WSGI server rather
than `ThreadingHTTPServer`, and an anonymous identity that survives a cleared
cookie without asking anyone to sign in.
