#!/usr/bin/env python3
"""The Future with AI betting notebook.

    python3 app.py                      run the notebook on http://localhost:8420
    python3 app.py --port 9000          run it somewhere else
    python3 app.py seed                 fill the ledger with a few example bets
    python3 app.py send-letters [--force] [--email you@example.org]
                                        post the once-a-year letters that are due

Standard library only: no install, no network, nothing to configure.
"""

import argparse
import os
import posixpath
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse, quote

import db
import mail
import render

ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(ROOT, "static")
COOKIE = "notebook_session"
BASE_URL = os.environ.get("NOTEBOOK_URL", "http://localhost:8420")


class Notebook(BaseHTTPRequestHandler):
    server_version = "Notebook/1.0"
    protocol_version = "HTTP/1.1"

    # --- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s  %s\n" % (self.log_date_time_string(), fmt % args))

    def reply(self, html, code=200, cookie=None, kill_cookie=False):
        payload = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if cookie:
            self.send_header(
                "Set-Cookie",
                "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                % (COOKIE, cookie, db.SESSION_DAYS * 86400),
            )
        if kill_cookie:
            self.send_header("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def send_text(self, text, filename=None):
        payload = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if filename:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % filename)
        self.end_headers()
        self.wfile.write(payload)

    def go(self, where, cookie=None, kill_cookie=False):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        if cookie:
            self.send_header(
                "Set-Cookie",
                "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d"
                % (COOKIE, cookie, db.SESSION_DAYS * 86400),
            )
        if kill_cookie:
            self.send_header("Set-Cookie", "%s=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" % COOKIE)
        self.end_headers()

    def form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def session_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        jar.load(raw)
        return jar[COOKIE].value if COOKIE in jar else None

    def serve_static(self, name):
        safe = posixpath.normpath("/" + name).lstrip("/")
        path = os.path.join(STATIC, safe)
        if not os.path.isfile(path) or not os.path.abspath(path).startswith(STATIC):
            return self.reply(render.message_page("Nothing here", "No such file."), 404)
        kind = "text/css" if path.endswith(".css") else "application/octet-stream"
        with open(path, "rb") as fh:
            payload = fh.read()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # --- routing ----------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        args = {k: v[0].strip() for k, v in parse_qs(url.query).items()}
        conn = db.connect()
        try:
            user = db.session_user(conn, self.session_token())

            if path == "/":
                return self.page_index(conn, user, args)
            if path == "/static" or path.startswith("/static/"):
                return self.serve_static(path[len("/static/"):])
            if path.startswith("/bet/"):
                return self.page_bet(conn, user, path.split("/")[2])
            if path == "/propose":
                if not user:
                    return self.go("/enter")
                return self.reply(render.propose_page(user))
            if path == "/enter":
                return self.reply(render.enter_page())
            if path.startswith("/enter/"):
                return self.claim_key(conn, path.split("/", 2)[2])
            if path == "/leave":
                token = self.session_token()
                if token:
                    db.end_session(conn, token)
                return self.go("/", kill_cookie=True)
            if path == "/desk":
                if not user:
                    return self.go("/enter")
                return self.reply(self.desk(conn, user))
            if path == "/print":
                return self.page_print(conn, user, args)
            if path == "/export.txt":
                return self.page_export(conn, user, args)

            return self.reply(
                render.message_page("A blank page", "There is nothing written at %s." % path),
                404,
            )
        finally:
            conn.close()

    def do_POST(self):
        url = urlparse(self.path)
        path = url.path.rstrip("/") or "/"
        conn = db.connect()
        try:
            user = db.session_user(conn, self.session_token())
            form = self.form()

            if path == "/enter":
                return self.post_enter(conn, form)
            if path == "/propose":
                return self.post_propose(conn, user, form)
            if path == "/desk":
                return self.post_desk(conn, user, form)
            if path.startswith("/bet/") and path.endswith("/vote"):
                return self.post_vote(conn, user, path.split("/")[2])
            if path.startswith("/bet/") and path.endswith("/resolve"):
                return self.post_resolve(conn, user, path.split("/")[2], form)

            return self.reply(render.message_page("A blank page", "Nothing accepts that."), 404)
        finally:
            conn.close()

    # --- pages ------------------------------------------------------------

    def page_index(self, conn, user, args):
        query = args.get("q", "")
        category = args.get("category", "")
        status = args.get("status", "")
        sort = args.get("sort", "interesting")
        bets = db.list_bets(conn, user["id"] if user else 0, query, category, status, sort)
        note = ""
        if args.get("welcome"):
            note = (
                '<div class="notice plain">The notebook is open to you. You are writing as '
                "<b>%s</b> &mdash; change the name, or hide it, at <a href='/desk'>your desk</a>."
                "</div>" % render.e(user["pseudo"] if user else "")
            )
        return self.reply(
            render.index(bets, db.category_counts(conn), user, query, category, status, sort, note)
        )

    def page_bet(self, conn, user, raw_id):
        if not raw_id.isdigit():
            return self.reply(render.message_page("A blank page", "No such entry."), 404)
        bet = db.get_bet(conn, int(raw_id), user["id"] if user else 0)
        if bet is None:
            return self.reply(
                render.message_page("A torn page", "That entry is not in the ledger."), 404
            )
        return self.reply(render.bet_page(bet, user))

    def selection(self, conn, user, args):
        """The set of bets a print or export request is asking for."""
        viewer = user["id"] if user else 0
        if args.get("bet", "").isdigit():
            bet = db.get_bet(conn, int(args["bet"]), viewer)
            rows = [bet] if bet else []
            return rows, "One bet", "Entry %s of the ledger." % args["bet"]
        if args.get("mine") and user:
            rows = db.list_bets(conn, viewer, sort="newest")
            rows = [b for b in rows if b["user_id"] == user["id"]]
            return rows, "Your own bets", "Written by %s." % user["pseudo"]
        if args.get("backed") and user:
            rows = db.list_bets(conn, viewer, sort="interesting")
            rows = [b for b in rows if b["voted"] and b["user_id"] != user["id"]]
            return rows, "Bets you found interesting", "Marked by %s." % user["pseudo"]

        query, category = args.get("q", ""), args.get("category", "")
        status, sort = args.get("status", ""), args.get("sort", "interesting")
        rows = db.list_bets(conn, viewer, query, category, status, sort)
        heading = "The ledger" if not category else "The ledger: %s" % category
        bits = []
        if query:
            bits.append('matching "%s"' % query)
        if status:
            bits.append(db.STATUSES.get(status, status))
        sub = "%d %s, %s%s." % (
            len(rows),
            "bet" if len(rows) == 1 else "bets",
            {"interesting": "most interesting first", "newest": "newest first",
             "horizon": "soonest horizon first", "oldest": "oldest first"}.get(sort, ""),
            (", " + ", ".join(bits)) if bits else "",
        )
        return rows, heading, sub

    def page_print(self, conn, user, args):
        rows, heading, sub = self.selection(conn, user, args)
        return self.reply(render.print_page(rows, heading, sub))

    def page_export(self, conn, user, args):
        rows, heading, sub = self.selection(conn, user, args)
        lines = [
            "THE FUTURE WITH AI - BETTING NOTEBOOK",
            heading,
            sub,
            "Taken on %s from %s" % (db.now().strftime("%d %B %Y"), BASE_URL),
            "=" * 68,
            "",
        ]
        for b in rows:
            lines += [
                "[%d %s]  %s" % (b["votes"], "vote" if b["votes"] == 1 else "votes", b["claim"]),
                "    %s | by %d | %s | %s"
                % (b["category"], b["horizon"], db.byline(b), db.STATUSES[b["status"]]),
            ]
            if b["reasoning"].strip():
                for para in b["reasoning"].strip().splitlines():
                    lines.append("    %s" % para.strip())
            if b["status"] != "open" and b["verdict"].strip():
                lines.append("    VERDICT: %s" % b["verdict"].strip())
            lines += ["    %s/bet/%d" % (BASE_URL, b["id"]), ""]
        return self.send_text("\n".join(lines), filename="betting-notebook.txt")

    def desk(self, conn, user, note="", error=""):
        mine = db.list_bets(conn, user["id"], sort="newest")
        backed = [b for b in mine if b["voted"] and b["user_id"] != user["id"]]
        own = [b for b in mine if b["user_id"] == user["id"]]
        return render.desk_page(user, own, backed, note, error)

    # --- actions ----------------------------------------------------------

    def post_enter(self, conn, form):
        email = form.get("email", "").strip().lower()
        if not mail.looks_like_email(email):
            return self.reply(render.enter_page(error="That does not look like an address."), 400)
        token = db.new_login_token(conn, email)
        url = "%s/enter/%s" % (BASE_URL, quote(token))
        mail.send_login_link(email, url)
        return self.reply(render.enter_page(sent_to=email, link=url))

    def claim_key(self, conn, token):
        email = db.spend_login_token(conn, token)
        if not email:
            return self.reply(
                render.enter_page(error="That key is spent, or too old. Ask for another."), 400
            )
        user = db.user_by_email(conn, email) or db.create_user(conn, email)
        return self.go("/?welcome=1", cookie=db.new_session(conn, user["id"]))

    def post_propose(self, conn, user, form):
        if not user:
            return self.go("/enter")
        claim = form.get("claim", "").strip()
        reasoning = form.get("reasoning", "").strip()
        category = form.get("category", "").strip()
        horizon = form.get("horizon", "").strip()
        anonymous = bool(form.get("anonymous"))
        values = {
            "claim": claim, "reasoning": reasoning, "category": category,
            "horizon": horizon, "anonymous": anonymous,
        }
        year = db.now().year

        if len(claim) < 12:
            return self.reply(
                render.propose_page(user, values, "A bet needs to be a whole claim."), 400
            )
        if category not in db.CATEGORIES:
            return self.reply(render.propose_page(user, values, "Pick a subject."), 400)
        if not horizon.isdigit() or not year <= int(horizon) <= year + 75:
            return self.reply(
                render.propose_page(
                    user, values, "The horizon must be a year between %d and %d." % (year, year + 75)
                ),
                400,
            )
        bet_id = db.create_bet(
            conn, user["id"], claim, reasoning[:4000], category, int(horizon), anonymous
        )
        db.toggle_vote(conn, bet_id, user["id"])  # you back your own bet
        return self.go("/bet/%d" % bet_id)

    def post_vote(self, conn, user, raw_id):
        if not user:
            return self.go("/enter")
        if not raw_id.isdigit() or db.get_bet(conn, int(raw_id)) is None:
            return self.reply(render.message_page("A torn page", "No such entry."), 404)
        db.toggle_vote(conn, int(raw_id), user["id"])
        back = self.headers.get("Referer")
        return self.go(back if back and urlparse(back).netloc == urlparse(BASE_URL).netloc
                       else "/bet/%s" % raw_id)

    def post_resolve(self, conn, user, raw_id, form):
        if not user or not raw_id.isdigit():
            return self.go("/enter")
        bet = db.get_bet(conn, int(raw_id), user["id"])
        if bet is None or bet["user_id"] != user["id"]:
            return self.reply(
                render.message_page("Not your call", "Only the hand that wrote a bet may settle it."),
                403,
            )
        status = form.get("status", "open")
        if status not in db.STATUSES:
            status = "open"
        db.resolve_bet(conn, int(raw_id), user["id"], status, form.get("verdict", "").strip()[:2000])
        return self.go("/bet/%s" % raw_id)

    def post_desk(self, conn, user, form):
        if not user:
            return self.go("/enter")
        pseudo = form.get("pseudo", "").strip()
        show = bool(form.get("show_pseudo"))
        yearly = bool(form.get("yearly_letter"))
        if not 2 <= len(pseudo) <= 32:
            return self.reply(self.desk(conn, user, error="A pen name wants 2 to 32 letters."), 400)
        if db.pseudo_taken(conn, pseudo, user["id"]):
            return self.reply(self.desk(conn, user, error="Somebody writes under that name already."), 400)
        db.update_user(conn, user["id"], pseudo, show, yearly)
        return self.reply(self.desk(conn, db.user_by_id(conn, user["id"]), note="Desk saved."))


# --- example ledger -------------------------------------------------------

SEED = [
    ("information & trust", 2031, "By 2031, most people under thirty will assume a photograph or video is synthetic until something proves otherwise.",
     "Verification will move from the image to the chain of custody around it. I would count this settled if a major survey finds under half of that age group treat an unsourced image as evidence of anything."),
    ("education", 2030, "By 2030, at least one national school system will have made oral examination its default form of assessment.",
     "Written homework stops measuring what it used to measure. The cheapest fix is to make the student talk. I expect a small, centralised country to move first."),
    ("politics & governance", 2032, "By 2032, a candidate will win a national election in a G20 country having campaigned mostly through synthetic media of themselves.",
     "Not a deepfake scandal - a normalised, disclosed practice: the candidate's likeness giving a thousand local speeches at once."),
    ("work & economy", 2029, "By 2029, 'wrote it myself' will be a paid premium in at least one creative market, the way handmade is for furniture.",
     "Scarcity moves to whatever the machine cannot flood. The first market is probably literary translation or illustration."),
    ("everyday life", 2028, "By 2028, more than half of people I know will speak to a machine each day and to fewer than three humans.",
     "A bet on the shape of loneliness, not on the technology. I would settle it honestly by asking twenty friends."),
    ("health & medicine", 2033, "By 2033, refusing an AI second opinion will be grounds for a malpractice claim somewhere in the OECD.",
     "The standard of care ratchets up and never comes back down. One court decision is enough to settle this."),
    ("information & trust", 2027, "By 2027, at least one major encyclopedia or news archive will publish a version certified as written before 2023.",
     "A pre-contamination corpus becomes valuable in the way pre-1945 steel became valuable for building radiation detectors."),
    ("art & culture", 2035, "By 2035, a work made largely by a machine will be in a permanent collection of a top-ten museum, credited to the machine.",
     "The interesting part is the credit line, not the artwork. Attribution is where the argument actually lives."),
    ("law & rights", 2034, "By 2034, at least three countries will grant people a legal right to a human decision-maker on appeal.",
     "The GDPR already gestures at this. I am betting it hardens into something with teeth and gets used."),
    ("love & friendship", 2030, "By 2030, meeting a partner through a machine that already knew you both will be less remarkable than meeting through friends.",
     "Matchmaking was always about who holds the information about you. That has quietly changed hands."),
    ("science & technology", 2031, "By 2031, a paper whose central hypothesis was generated by a machine will win a major scientific prize.",
     "The prize committee's wording will be the tell: whether the machine is named or thanked."),
    ("climate & environment", 2029, "By 2029, data centre electricity will be a named issue in a national election campaign in Europe.",
     "It becomes political the moment it competes visibly with heating a house."),
]


def seed(force=False):
    conn = db.init()
    if not force and conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0]:
        print("The ledger already has entries; nothing seeded. Use --force to add anyway.")
        return
    people = []
    for address in ("cassandra@example.org", "the.archivist@example.org", "m.wager@example.org"):
        people.append(db.user_by_email(conn, address) or db.create_user(conn, address))
    conn.execute("UPDATE users SET show_pseudo = 0 WHERE email = ?", ("m.wager@example.org",))
    conn.commit()

    for i, (category, horizon, claim, reasoning) in enumerate(SEED):
        author = people[i % len(people)]
        bet_id = db.create_bet(conn, author["id"], claim, reasoning, category, horizon, i % 7 == 3)
        for voter in people[: (i % 3) + 1]:
            db.toggle_vote(conn, bet_id, voter["id"])
    print("Wrote %d example bets by %d example hands." % (len(SEED), len(people)))
    conn.close()


def serve(port):
    db.init()
    server = ThreadingHTTPServer(("127.0.0.1", port), Notebook)
    url = BASE_URL if str(port) in BASE_URL else "http://localhost:%d" % port
    print("\n  The Future with AI betting notebook")
    print("  open  %s" % url)
    print("  ledger at %s" % db.DB_PATH)
    print("  letters land in %s\n" % mail.OUTBOX)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Closing the notebook.\n")
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="The Future with AI betting notebook")
    parser.add_argument("command", nargs="?", default="serve",
                        choices=["serve", "seed", "send-letters"])
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8420)))
    parser.add_argument("--force", action="store_true",
                        help="seed even if the ledger is full; send letters even if not due")
    parser.add_argument("--email", help="send the yearly letter to one address only")
    args = parser.parse_args()

    if args.command == "seed":
        return seed(args.force)
    if args.command == "send-letters":
        conn = db.init()
        n = mail.send_yearly(conn, BASE_URL, force=args.force, only_email=args.email)
        print("Posted %d letter%s." % (n, "" if n == 1 else "s"))
        return conn.close()
    return serve(args.port)


if __name__ == "__main__":
    main()
