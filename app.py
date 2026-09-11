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
import re
import secrets
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
ANON_COOKIE = "notebook_anon"
ANON_COOKIE_DAYS = 3650  # a voter without an account is remembered by this cookie alone
# A visitor with no cookie is about to be handed a fresh anonymous hand.
# The cookie is still the whole of their identity, but handing them out
# without limit makes clearing cookies a free ballot box: one address may
# take a small number of new hands a day, which is plenty for a household
# behind one address and tedious for anyone voting with a broom.
ANON_HANDS_PER_IP = 8
ANON_HANDS_WINDOW_MINUTES = 24 * 60
CSRF_COOKIE = "notebook_csrf"
CSRF_COOKIE_DAYS = 30
BASE_URL = os.environ.get("NOTEBOOK_URL", "http://localhost:8420")

# Over https the cookies must not be allowed onto a plain connection.
SECURE_COOKIES = BASE_URL.startswith("https://")
# Set this only when a proxy you trust is in front and rewrites the
# forwarding header itself. Fly is recognised without it.
TRUST_FORWARDED = os.environ.get("NOTEBOOK_TRUST_FORWARDED") == "1"
is_keeper = db.is_keeper  # who may keep the ledger; see NOTEBOOK_KEEPERS
# Nobody needs to post more than a long bet; refuse the rest unread.
MAX_BODY_BYTES = 64 * 1024
MAX_CLAIM = 240
MAX_REASONING = 4000
MAX_VERDICT = 2000


# A path we are willing to copy into a Location header: no spaces, no
# control characters, nothing but the ordinary furniture of a URL.
SAFE_PATH = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@/?%-]*$")
# The shape of a token we minted ourselves - secrets.token_urlsafe and
# nothing else. A cookie that does not look like this was not ours.
OUR_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def set_cookie(name, value, max_age):
    # Whatever a caller believes, only our own alphabet reaches the header:
    # a cookie read back from a visitor can carry quoted semicolons, and
    # those would write extra attributes into the reply.
    if value and not OUR_TOKEN.match(value):
        raise ValueError("refusing to plant a cookie shaped like %r" % value[:40])
    bits = ["%s=%s" % (name, value), "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=%d" % max_age]
    if SECURE_COOKIES:
        bits.append("Secure")
    return "; ".join(bits)


class Notebook(BaseHTTPRequestHandler):
    server_version = "Notebook/1.0"
    sys_version = ""  # no need to announce the Python version to the world
    protocol_version = "HTTP/1.1"

    # --- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s  %s\n" % (self.log_date_time_string(), fmt % args))

    def guard_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; form-action 'self'; base-uri 'none'; "
            "frame-ancestors 'none'",
        )
        if SECURE_COOKIES:
            self.send_header("Strict-Transport-Security", "max-age=31536000")

    def cookie_headers(self, cookie=None, kill_cookie=False, anon_cookie=None):
        if cookie:
            self.send_header("Set-Cookie", set_cookie(COOKIE, cookie, db.SESSION_DAYS * 86400))
        if kill_cookie:
            self.send_header("Set-Cookie", set_cookie(COOKIE, "", 0))
        if anon_cookie:
            self.send_header(
                "Set-Cookie", set_cookie(ANON_COOKIE, anon_cookie, ANON_COOKIE_DAYS * 86400)
            )
        self._maybe_plant_csrf()

    def reply(self, html, code=200, cookie=None, kill_cookie=False, anon_cookie=None):
        payload = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.guard_headers()
        self.cookie_headers(cookie, kill_cookie, anon_cookie)
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
        self.guard_headers()
        self.end_headers()
        self.wfile.write(payload)

    def go(self, where, cookie=None, kill_cookie=False, anon_cookie=None):
        # Never let a caller write raw request data into a response header.
        if not where.startswith("/") or any(c in where for c in "\r\n"):
            where = "/"
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.guard_headers()
        self.cookie_headers(cookie, kill_cookie, anon_cookie)
        self.end_headers()

    def back_to(self, fallback):
        """Where a form should send the visitor next: the page they came
        from, but only ever as a path of our own, never the raw header."""
        referer = self.headers.get("Referer") or ""
        seen = urlparse(referer)
        if not referer or seen.netloc != urlparse(BASE_URL).netloc:
            return fallback
        where = seen.path or "/"
        if seen.query:
            where += "?" + seen.query
        # urlparse drops CR/LF/TAB, so a header cannot be split here - but
        # whatever is left of a crafted Referer has no business in a reply
        # header either, so only a plain path is allowed through.
        if not where.startswith("/") or not SAFE_PATH.match(where):
            return fallback
        return where

    def form(self):
        """The posted fields, or None if the body is missing, malformed or
        larger than anything this notebook has a use for."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def cookie(self, name):
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        jar = SimpleCookie()
        jar.load(raw)
        return jar[name].value if name in jar else None

    def session_token(self):
        return self.cookie(COOKIE)

    def anon_id(self):
        """The visitor's anon-voter cookie, or "" if they haven't got one.

        A cookie is whatever the visitor says it is, so anything that does
        not look like a token we minted is treated as no token at all -
        otherwise it lands in the ledger, and back in a reply header."""
        token = self.cookie(ANON_COOKIE) or ""
        return token if OUR_TOKEN.match(token) else ""

    def client_ip(self):
        """The visitor's address, for the purpose of rate limiting.

        A forwarding header is written by whoever is talking to us, so it
        is only worth anything when something trusted sits in front and
        overwrites it. Fly does that with Fly-Client-IP. Behind any other
        proxy, say so with NOTEBOOK_TRUST_FORWARDED=1 and the last hop of
        X-Forwarded-For is used - the entry that proxy appended, not the
        ones the visitor chose. With nothing in front, the only address
        worth believing is the one the socket came from: believing a
        header there would hand the rate limiter to the visitor."""
        edge = self.headers.get("Fly-Client-IP")
        if edge:
            return edge.strip()
        if TRUST_FORWARDED:
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[-1].strip()
        return self.client_address[0]

    def csrf_token(self):
        """A per-visitor token, minted once and replanted by every reply.

        Every form in the notebook carries it as a hidden field; every
        state-changing POST checks the field against this same cookie
        (a plain double-submit check - see check_csrf).
        """
        if not hasattr(self, "_csrf"):
            token = self.cookie(CSRF_COOKIE)
            self._csrf, self._csrf_is_new = (token or secrets.token_urlsafe(24)), not token
        return self._csrf

    def _maybe_plant_csrf(self):
        if getattr(self, "_csrf_is_new", False):
            self.send_header(
                "Set-Cookie", set_cookie(CSRF_COOKIE, self._csrf, CSRF_COOKIE_DAYS * 86400)
            )

    def check_csrf(self, form):
        return bool(self.csrf_token()) and secrets.compare_digest(
            form.get("csrf", ""), self.csrf_token()
        )

    def serve_static(self, name):
        safe = posixpath.normpath("/" + name).lstrip("/")
        path = os.path.join(STATIC, safe)
        if not os.path.isfile(path) or not os.path.abspath(path).startswith(STATIC):
            return self.reply(render.message_page("Nothing here", "No such file."), 404)
        kind = {
            ".css": "text/css",
            ".js": "text/javascript",
        }.get(os.path.splitext(path)[1], "application/octet-stream")
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
                return self.reply(render.propose_page(
                    user, self.csrf_token(), first_time=not db.has_written(conn, user["id"])
                ))
            if path == "/enter":
                return self.reply(render.enter_page(self.csrf_token()))
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
            if path == "/house":
                return self.reply(render.house_page(user))
            if path == "/keep":
                return self.page_keep(conn, user, args)
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

            if form is None:
                return self.reply(
                    render.message_page("Too much at once", "That did not arrive in one piece."),
                    413,
                )

            if not self.check_csrf(form):
                return self.reply(
                    render.message_page(
                        "That page had gone stale",
                        "Go back, reload the page, and try again.",
                        user,
                    ),
                    400,
                )

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
            if path == "/keep":
                return self.post_keep(conn, user, form)

            return self.reply(render.message_page("A blank page", "Nothing accepts that."), 404)
        finally:
            conn.close()

    # --- pages ------------------------------------------------------------

    def page_index(self, conn, user, args):
        query = args.get("q", "")
        category = args.get("category", "")
        status = args.get("status", "")
        sort = args.get("sort", "interesting")
        bets = db.list_bets(
            conn, user["id"] if user else 0, query, category, status, sort, self.anon_id()
        )
        note = ""
        if args.get("welcome"):
            note = (
                '<div class="notice plain">The notebook is open to you. You are writing as '
                "<b>%s</b> &mdash; change the name, or hide it, at <a href='/desk'>your desk</a>."
                "</div>" % render.e(user["pseudo"] if user else "")
            )
        return self.reply(
            render.index(
                bets, db.category_counts(conn), user, query, category, status, sort,
                self.csrf_token(), note,
            )
        )

    def page_bet(self, conn, user, raw_id):
        if not raw_id.isdigit():
            return self.reply(render.message_page("A blank page", "No such entry."), 404)
        # A struck entry is gone for everyone but the keeper, who may still
        # want to look at what they struck.
        bet = db.get_bet(
            conn, int(raw_id), user["id"] if user else 0, self.anon_id(), struck=is_keeper(user)
        )
        if bet is None:
            return self.reply(
                render.message_page("A torn page", "That entry is not in the ledger.", user), 404
            )
        return self.reply(render.bet_page(bet, user, self.csrf_token()))

    def selection(self, conn, user, args):
        """The set of bets a print or export request is asking for."""
        viewer = user["id"] if user else 0
        anon = self.anon_id()
        if args.get("bet", "").isdigit():
            bet = db.get_bet(conn, int(args["bet"]), viewer, anon)
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
        rows = db.list_bets(conn, viewer, query, category, status, sort, anon)
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

    # --- the moderation desk ----------------------------------------------

    def page_keep(self, conn, user, args, note="", error=""):
        if not is_keeper(user):
            return self.no_entry(user)
        query = args.get("q", "")
        return self.reply(
            render.keep_page(
                user, self.csrf_token(), db.tally(conn),
                db.list_bets(conn, user["id"], query, sort="newest", struck=True),
                db.people(conn), query, note, error,
            )
        )

    def no_entry(self, user):
        """The same answer whether or not the door exists: a stranger has
        no business learning that this notebook has a keeper at all."""
        return self.reply(
            render.message_page("A blank page", "There is nothing written at /keep.", user), 404
        )

    def post_keep(self, conn, user, form):
        if not is_keeper(user):
            return self.no_entry(user)

        deed = form.get("deed", "")
        why = form.get("why", "").strip()
        raw_id = form.get("bet", "")
        note = ""

        if deed in ("strike", "restore", "burn", "burn-for-good"):
            if not raw_id.isdigit():
                return self.page_keep(conn, user, {}, error="No such entry.")
            bet = db.get_bet(conn, int(raw_id), user["id"], struck=True)
            if bet is None:
                return self.page_keep(conn, user, {}, error="No such entry.")
            if deed == "strike":
                db.strike_bet(conn, bet["id"], why)
                note = "Entry %d is struck from the ledger%s" % (
                    bet["id"], (" - %s." % why) if why else "."
                )
            elif deed == "restore":
                db.restore_bet(conn, bet["id"])
                note = "Entry %d is back in the ledger." % bet["id"]
            elif deed == "burn":
                # Ask once more, on a page of its own.
                return self.reply(render.burn_page(user, self.csrf_token(), bet))
            else:
                db.burn_bet(conn, bet["id"])
                note = "Entry %d is gone for good." % bet["id"]

        elif deed == "strike-hand":
            raw_hand = form.get("hand", "")
            if not raw_hand.isdigit():
                return self.page_keep(conn, user, {}, error="No such hand.")
            hand = db.user_by_id(conn, int(raw_hand))
            if hand is None:
                return self.page_keep(conn, user, {}, error="No such hand.")
            if is_keeper(hand):
                return self.page_keep(
                    conn, user, {}, error="A keeper's own hand is not struck from here."
                )
            struck = db.strike_everything_by(conn, hand["id"], why)
            note = "Struck %d %s by %s." % (
                struck, "entry" if struck == 1 else "entries", hand["pseudo"]
            )
        else:
            return self.page_keep(conn, user, {}, error="That is not a thing to do.")

        return self.page_keep(conn, user, {}, note=note)

    def desk(self, conn, user, note="", error=""):
        mine = db.list_bets(conn, user["id"], sort="newest")
        backed = [b for b in mine if b["voted"] and b["user_id"] != user["id"]]
        own = [b for b in mine if b["user_id"] == user["id"]]
        return render.desk_page(user, self.csrf_token(), own, backed, note, error)

    # --- actions ----------------------------------------------------------

    def post_enter(self, conn, form):
        email = form.get("email", "").strip().lower()
        if not mail.looks_like_email(email):
            return self.reply(
                render.enter_page(self.csrf_token(), error="That does not look like an address."), 400
            )
        overbusy = (
            db.rate_limited(conn, "enter-ip:%s" % self.client_ip(), 20, 15)
            or db.rate_limited(conn, "enter-email:%s" % email, 5, 15)
        )
        if overbusy:
            return self.reply(
                render.enter_page(
                    self.csrf_token(),
                    error="Too many keys asked for. Wait a few minutes and try again.",
                ),
                429,
            )
        token = db.new_login_token(conn, email)
        url = "%s/enter/%s" % (BASE_URL, quote(token))
        sent = mail.send_login_link(email, url)
        if mail.using_real_smtp() and not sent:
            return self.reply(
                render.enter_page(
                    self.csrf_token(),
                    error="The key could not be posted just now. Please try again shortly.",
                ),
                503,
            )
        # The link is only ever shown on the page when nothing was really
        # emailed - once real mail is configured, proving you hold the
        # inbox is the whole point, so the key must go there and nowhere else.
        shortcut = None if mail.using_real_smtp() else url
        return self.reply(render.enter_page(self.csrf_token(), sent_to=email, link=shortcut))

    def claim_key(self, conn, token):
        email = db.spend_login_token(conn, token)
        if not email:
            return self.reply(
                render.enter_page(
                    self.csrf_token(), error="That key is spent, or too old. Ask for another."
                ),
                400,
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
        # Still their first: the rules stay up while they fix whatever
        # the notebook has just complained about.
        first = not db.has_written(conn, user["id"])

        if len(claim) < 12:
            return self.reply(
                render.propose_page(user, self.csrf_token(), values,
                                    "A bet needs to be a whole claim.", first),
                400,
            )
        if len(claim) > MAX_CLAIM:
            return self.reply(
                render.propose_page(
                    user, self.csrf_token(), values,
                    "A claim wants %d characters at most." % MAX_CLAIM, first,
                ),
                400,
            )
        if category not in db.CATEGORIES:
            return self.reply(
                render.propose_page(user, self.csrf_token(), values, "Pick a subject.", first), 400
            )
        if not horizon.isdigit() or not year <= int(horizon) <= year + 75:
            return self.reply(
                render.propose_page(
                    user, self.csrf_token(), values,
                    "The horizon must be a year between %d and %d." % (year, year + 75), first,
                ),
                400,
            )
        bet_id = db.create_bet(
            conn, user["id"], claim, reasoning[:MAX_REASONING], category, int(horizon), anonymous
        )
        db.toggle_vote(conn, bet_id, user["id"])  # you back your own bet
        return self.go("/bet/%d" % bet_id)

    def post_vote(self, conn, user, raw_id):
        if not raw_id.isdigit() or db.get_bet(conn, int(raw_id)) is None:
            return self.reply(render.message_page("A torn page", "No such entry."), 404)
        anon_cookie = None
        if user:
            db.toggle_vote(conn, int(raw_id), user_id=user["id"])
        else:
            anon = self.anon_id()
            if not anon:
                # Somebody who already holds a hand may toggle all day; it
                # is only the minting of a new one that is worth counting.
                if db.rate_limited(
                    conn, "anon-hand:%s" % self.client_ip(),
                    ANON_HANDS_PER_IP, ANON_HANDS_WINDOW_MINUTES,
                ):
                    return self.reply(
                        render.message_page(
                            "Marked too often",
                            "Too many new hands from one address today. Sign in and "
                            "your marks are kept for good, or come back tomorrow.",
                            link=self.back_to("/bet/%s" % raw_id),
                        ),
                        429,
                    )
                anon = secrets.token_urlsafe(16)
            db.toggle_vote(conn, int(raw_id), anon_id=anon)
            anon_cookie = anon  # (re)plant the cookie so this vote is remembered
        return self.go(self.back_to("/bet/%s" % raw_id), anon_cookie=anon_cookie)

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
        db.resolve_bet(
            conn, int(raw_id), user["id"], status, form.get("verdict", "").strip()[:MAX_VERDICT]
        )
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
    host = os.environ.get("NOTEBOOK_HOST", "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Notebook)
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
