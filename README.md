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
| **Vote** | one "interesting" mark per person per bet, toggleable; the ledger sorts by it |
| **Subjects** | twelve categories — education, politics & governance, work & economy, information & trust, science & technology, health & medicine, art & culture, everyday life, war & security, climate & environment, law & rights, love & friendship |
| **Search** | one box for words, a box beside it for the subject; both combine with standing and order |
| **Sign in** | email only. A one-shot key, valid an hour. You are given a pen name you can change |
| **Pen name** | shown or hidden, as a standing preference at your desk or per bet. Your address is never shown either way |
| **Print** | *your copies*, at your desk: your own bets, the ones you backed, or the whole ledger — as a print sheet or plain text. Any search or subject you are looking at prints the same way from the foot of the ledger |
| **Settle** | the author of a bet can record how it turned out, with a line on why |
| **Yearly letter** | opt in at your desk: one letter a year with your bets, the ones you backed, and which have come due |

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
static/notebook.css    the whole look — paper, ink, and the print rules
data/                  SQLite file + outbox   (git-ignored, safe to delete)
```

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
people. Public deployment would want, at least: a real WSGI server behind TLS,
CSRF tokens on the forms (today it leans on `SameSite=Lax` cookies), rate
limiting on the sign-in form, and a moderation path for bets.
