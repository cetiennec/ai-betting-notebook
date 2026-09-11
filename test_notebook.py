#!/usr/bin/env python3
"""The notebook's tests.

    python3 test_notebook.py            run them all
    python3 test_notebook.py -v         say what each one is

Standard library only, like the rest. Each test starts a real server on a
free port with its own throwaway ledger and talks to it over HTTP, so the
socket and header layers are exercised, not mocked.
"""

import http.cookiejar
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Notebook:
    """A notebook running in its own process, on its own ledger."""

    def __init__(self, seed=True, **env):
        self.dir = tempfile.mkdtemp(prefix="notebook-test-")
        self.db = os.path.join(self.dir, "ledger.sqlite3")
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.env = dict(os.environ)
        self.env.pop("SMTP_HOST", None)  # never post real letters from a test
        self.env.update(
            NOTEBOOK_DB=self.db,
            NOTEBOOK_HOST="127.0.0.1",
            NOTEBOOK_URL=env.pop("NOTEBOOK_URL", self.base),
        )
        self.env.update(env)

        if seed:
            subprocess.run(
                [PYTHON, "app.py", "seed"], cwd=ROOT, env=self.env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            )
        self.proc = subprocess.Popen(
            [PYTHON, "app.py", "--port", str(self.port)], cwd=ROOT, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        self._wait()

    def _wait(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("notebook died at startup:\n%s"
                                   % self.proc.stdout.read().decode("utf-8", "replace"))
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.4):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("notebook never came up on port %d" % self.port)

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- talking to it ---------------------------------------------------

    def visitor(self):
        return Visitor(self)

    def raw(self, request):
        """Send bytes exactly as written, return the raw response."""
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(request.encode("latin-1"))
        chunks = []
        try:
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                chunks.append(chunk)
        except socket.timeout:
            pass
        finally:
            s.close()
        return b"".join(chunks).decode("latin-1", "replace")


class Visitor:
    """One browser: keeps its cookies, finds the csrf token on a page."""

    def __init__(self, notebook):
        self.notebook = notebook
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), NoRedirect()
        )

    def get(self, path):
        return self._open(urllib.request.Request(self.notebook.base + path))

    def post(self, path, fields, csrf_from=None):
        """POST `fields`, carrying the csrf token from `csrf_from` (default:
        the page being posted to) unless a csrf field is already given."""
        if "csrf" not in fields:
            page = self.get(csrf_from or path)
            fields = dict(fields, csrf=token_on(page.body))
        body = urllib.parse.urlencode(fields).encode()
        request = urllib.request.Request(self.notebook.base + path, data=body)
        return self._open(request)

    def _open(self, request):
        try:
            with self.opener.open(request) as reply:
                return Reply(reply.status, reply.headers, reply.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as err:
            return Reply(err.code, err.headers, err.read().decode("utf-8", "replace"))

    def cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a 303 is a result worth asserting on, not a detour


class Reply:
    def __init__(self, status, headers, body):
        self.status, self.headers, self.body = status, headers, body


def token_on(html):
    found = re.search(r'name="csrf" value="([^"]+)"', html)
    return found.group(1) if found else ""


def votes_on(html):
    found = re.search(r"Found interesting by</dt><dd>(\d+)", html)
    return int(found.group(1)) if found else None


class NotebookTestCase(unittest.TestCase):
    """One notebook per class, since starting one costs a moment. Only for
    tests that leave the ledger as they found it."""

    env = {}
    seed = True

    @classmethod
    def setUpClass(cls):
        cls.notebook = Notebook(seed=cls.seed, **cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.notebook.stop()


class FreshNotebookTestCase(unittest.TestCase):
    """A notebook of its own for every test. For the ones that strike
    entries or burn them, where what the last test did would otherwise be
    waiting for the next one."""

    env = {}

    def setUp(self):
        self.notebook = Notebook(**self.env)

    def tearDown(self):
        self.notebook.stop()

    def signed_in(self, email):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered for %s: %s" % (email, reply.status))
        visitor.get(link.group(1))
        return visitor


# --- the ledger itself ----------------------------------------------------

class TestPages(NotebookTestCase):
    def test_the_ledger_opens(self):
        reply = self.notebook.visitor().get("/")
        self.assertEqual(reply.status, 200)
        self.assertIn("The ledger", reply.body)

    def test_a_bet_has_its_own_page(self):
        self.assertEqual(self.notebook.visitor().get("/bet/1").status, 200)

    def test_nothing_is_written_on_a_blank_page(self):
        self.assertEqual(self.notebook.visitor().get("/bet/99999").status, 404)

    def test_the_desk_wants_you_signed_in(self):
        self.assertEqual(self.notebook.visitor().get("/desk").status, 303)

    def test_search_and_print_and_plain_text(self):
        visitor = self.notebook.visitor()
        self.assertEqual(visitor.get("/?q=photograph").status, 200)
        self.assertEqual(visitor.get("/print").status, 200)
        self.assertEqual(visitor.get("/export.txt").status, 200)


# --- voting without an account -------------------------------------------

class TestAnonymousVoting(NotebookTestCase):
    def test_a_stranger_may_mark_a_bet_and_take_it_back(self):
        visitor = self.notebook.visitor()
        before = votes_on(visitor.get("/bet/1").body)

        self.assertEqual(visitor.post("/bet/1/vote", {}, csrf_from="/bet/1").status, 303)
        self.assertEqual(votes_on(visitor.get("/bet/1").body), before + 1)

        self.assertEqual(visitor.post("/bet/1/vote", {}, csrf_from="/bet/1").status, 303)
        self.assertEqual(votes_on(visitor.get("/bet/1").body), before)

    def test_the_mark_is_remembered_by_a_cookie(self):
        visitor = self.notebook.visitor()
        self.assertIsNone(visitor.cookie("notebook_anon"))
        visitor.post("/bet/2/vote", {}, csrf_from="/bet/2")
        self.assertIsNotNone(visitor.cookie("notebook_anon"))
        self.assertIn("Take back my vote", visitor.get("/bet/2").body)

    def test_two_strangers_are_two_votes(self):
        one, two = self.notebook.visitor(), self.notebook.visitor()
        before = votes_on(one.get("/bet/3").body)
        one.post("/bet/3/vote", {}, csrf_from="/bet/3")
        two.post("/bet/3/vote", {}, csrf_from="/bet/3")
        self.assertEqual(votes_on(one.get("/bet/3").body), before + 2)


# --- csrf -----------------------------------------------------------------

class TestCsrf(NotebookTestCase):
    def test_a_post_with_no_token_is_refused(self):
        visitor = self.notebook.visitor()
        visitor.get("/bet/1")  # plant the cookie
        self.assertEqual(visitor.post("/bet/1/vote", {"csrf": ""}).status, 400)

    def test_a_post_with_the_wrong_token_is_refused(self):
        visitor = self.notebook.visitor()
        visitor.get("/bet/1")
        self.assertEqual(visitor.post("/bet/1/vote", {"csrf": "not-the-token"}).status, 400)

    def test_a_refused_post_changes_nothing(self):
        visitor = self.notebook.visitor()
        before = votes_on(visitor.get("/bet/4").body)
        visitor.post("/bet/4/vote", {"csrf": "not-the-token"})
        self.assertEqual(votes_on(visitor.get("/bet/4").body), before)

    def test_every_form_carries_a_token(self):
        visitor = self.notebook.visitor()
        for path in ("/", "/bet/1", "/enter"):
            body = visitor.get(path).body
            for form in re.findall(r"<form[^>]*method=\"post\"[^>]*>.*?</form>", body, re.S):
                self.assertIn('name="csrf"', form, "a post form on %s carries no token" % path)


# --- signing in -----------------------------------------------------------

class TestSigningIn(NotebookTestCase):
    def test_a_key_arrives_and_opens_the_notebook(self):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": "reader@example.org"})
        self.assertEqual(reply.status, 200)

        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered on the page")

        opened = visitor.get(link.group(1))
        self.assertEqual(opened.status, 303)
        self.assertIsNotNone(visitor.cookie("notebook_session"))
        self.assertEqual(visitor.get("/desk").status, 200)

    def test_a_key_is_spent_once(self):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": "twice@example.org"})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertEqual(visitor.get(link.group(1)).status, 303)
        self.assertEqual(self.notebook.visitor().get(link.group(1)).status, 400)

    def test_a_shape_that_is_not_an_address_is_refused(self):
        reply = self.notebook.visitor().post("/enter", {"email": "not-an-address"})
        self.assertEqual(reply.status, 400)


class TestSignInRateLimit(NotebookTestCase):
    def test_one_address_may_only_ask_so_often(self):
        visitor = self.notebook.visitor()
        page = visitor.get("/enter")
        csrf = token_on(page.body)
        codes = [
            visitor.post("/enter", {"email": "eager@example.org", "csrf": csrf}).status
            for _ in range(6)
        ]
        self.assertEqual(codes[:5], [200] * 5)
        self.assertEqual(codes[5], 429)


class TestKeysAreNotShownWhenTheyAreReallyPosted(unittest.TestCase):
    """With real mail configured, the key must go to the inbox and nowhere
    else - showing it on the page would let anyone in as anyone."""

    def test_the_page_never_shows_the_key(self):
        # An SMTP host that refuses every connection: mail is "configured"
        # but cannot succeed, which is the riskiest moment for a leak.
        notebook = Notebook(SMTP_HOST="127.0.0.1", SMTP_PORT=str(free_port()))
        try:
            reply = notebook.visitor().post("/enter", {"email": "private@example.org"})
            self.assertNotIn("/enter/", reply.body)
            self.assertEqual(reply.status, 503)
        finally:
            notebook.stop()


# --- writing a bet --------------------------------------------------------

class TestProposing(NotebookTestCase):
    def signed_in(self, email):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        visitor.get(link.group(1))
        return visitor

    def test_a_bet_can_be_written_and_is_backed_by_its_author(self):
        visitor = self.signed_in("writer@example.org")
        reply = visitor.post("/propose", {
            "claim": "By 2033 this test will still be passing, or the notebook has changed.",
            "reasoning": "Because it is written down.",
            "category": "science & technology",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 303)
        page = visitor.get(reply.headers["Location"]).body
        self.assertEqual(votes_on(page), 1)

    def test_a_claim_that_is_too_long_is_refused(self):
        visitor = self.signed_in("windy@example.org")
        reply = visitor.post("/propose", {
            "claim": "x" * 5000,
            "reasoning": "",
            "category": "education",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 400)

    def test_a_subject_off_the_list_is_refused(self):
        visitor = self.signed_in("offlist@example.org")
        reply = visitor.post("/propose", {
            "claim": "A claim long enough to count as a whole one.",
            "reasoning": "",
            "category": "something invented",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 400)

    def test_only_the_hand_that_wrote_a_bet_may_settle_it(self):
        author = self.signed_in("author@example.org")
        reply = author.post("/propose", {
            "claim": "By 2034 somebody will try to settle a bet that is not theirs.",
            "reasoning": "",
            "category": "law & rights",
            "horizon": "2034",
        })
        where = reply.headers["Location"]

        stranger = self.signed_in("stranger@example.org")
        refused = stranger.post(where + "/resolve",
                                {"status": "came_true", "verdict": "I say so."},
                                csrf_from=where)
        self.assertEqual(refused.status, 403)
        self.assertIn("still open", author.get(where).body)

    def test_a_horizon_in_the_past_is_refused(self):
        visitor = self.signed_in("backwards@example.org")
        reply = visitor.post("/propose", {
            "claim": "A claim long enough to count as a whole one.",
            "reasoning": "",
            "category": "education",
            "horizon": "1999",
        })
        self.assertEqual(reply.status, 400)


# --- the shape of what comes back ----------------------------------------

class TestGuards(NotebookTestCase):
    def test_a_reply_carries_the_guard_headers(self):
        reply = self.notebook.visitor().get("/")
        self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", reply.headers["Content-Security-Policy"])
        self.assertEqual(reply.headers["Referrer-Policy"], "same-origin")

    def test_the_python_version_is_not_announced(self):
        reply = self.notebook.visitor().get("/")
        self.assertNotIn("Python", reply.headers.get("Server", ""))

    def test_a_body_that_is_not_a_number_is_turned_away(self):
        """A malformed Content-Length used to kill the thread mid-reply."""
        raw = self.notebook.raw(
            "POST /enter HTTP/1.1\r\nHost: x\r\nContent-Length: abc\r\n"
            "Connection: close\r\n\r\n"
        )
        self.assertTrue(raw.startswith("HTTP/1.1 413"), "got: %r" % raw[:60])

    def test_a_body_too_large_is_turned_away_unread(self):
        raw = self.notebook.raw(
            "POST /enter HTTP/1.1\r\nHost: x\r\nContent-Length: 999999999\r\n"
            "Connection: close\r\n\r\n"
        )
        self.assertTrue(raw.startswith("HTTP/1.1 413"), "got: %r" % raw[:60])

    def test_a_folded_referer_cannot_write_a_header(self):
        """The page you came from is attacker-shaped: it must never be
        copied into a reply header as it arrived."""
        visitor = self.notebook.visitor()
        page = visitor.get("/bet/1")
        csrf = token_on(page.body)
        cookies = "; ".join("%s=%s" % (c.name, c.value) for c in visitor.jar)
        body = urllib.parse.urlencode({"csrf": csrf})
        raw = self.notebook.raw(
            "POST /bet/1/vote HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
            "Cookie: %s\r\n"
            "Referer: %s/\r\n\tX-Injected: yes\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (self.notebook.port, cookies, self.notebook.base, len(body), body)
        )
        head = raw.split("\r\n\r\n")[0]
        self.assertNotIn("X-Injected", head)

    def test_nothing_is_served_from_outside_the_static_room(self):
        for path in ("/static/../app.py", "/static/../../etc/passwd", "/static/%2e%2e/app.py"):
            self.assertEqual(self.notebook.visitor().get(path).status, 404, path)

    def _vote_carrying(self, anon_cookie):
        visitor = self.notebook.visitor()
        page = visitor.get("/bet/1")
        csrf = token_on(page.body)
        jar = "; ".join("%s=%s" % (c.name, c.value) for c in visitor.jar)
        body = urllib.parse.urlencode({"csrf": csrf})
        return self.notebook.raw(
            "POST /bet/1/vote HTTP/1.1\r\nHost: x\r\n"
            "Cookie: %s; notebook_anon=%s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (jar, anon_cookie, len(body), body)
        )

    def test_a_cookie_cannot_write_extra_attributes_into_the_reply(self):
        """A quoted cookie value may hold a semicolon; planting it back
        unexamined would let a visitor append Set-Cookie attributes."""
        head = self._vote_carrying(r'"a\073Domain=evil.example"').split("\r\n\r\n")[0]
        self.assertNotIn("evil.example", head)

    def test_a_cookie_of_any_size_is_not_written_into_the_ledger(self):
        reply = self._vote_carrying("B" * 5000)
        self.assertTrue(reply.startswith("HTTP/1.1 303"), reply[:60])
        # whatever was stored, it is one of our own tokens, not their 5000
        planted = [
            line for line in reply.split("\r\n")
            if line.lower().startswith("set-cookie") and "notebook_anon" in line
        ]
        self.assertTrue(planted, "no fresh token was minted")
        self.assertNotIn("B" * 100, planted[0])


class TestTheRateLimiterCannotBeHandedToTheVisitor(NotebookTestCase):
    """X-Forwarded-For is written by whoever is talking to us, and appended
    to by each hop. Reading the first entry lets a visitor pick their own
    bucket and sign-in limits stop meaning anything."""

    def post_enter(self, email, header):
        visitor = self.notebook.visitor()
        page = visitor.get("/enter")
        csrf = token_on(page.body)
        jar = "; ".join("%s=%s" % (c.name, c.value) for c in visitor.jar)
        body = urllib.parse.urlencode({"csrf": csrf, "email": email})
        reply = self.notebook.raw(
            "POST /enter HTTP/1.1\r\nHost: x\r\nCookie: %s\r\n%s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (jar, header, len(body), body)
        )
        return int(reply.split(" ")[1])

    def test_a_made_up_forwarding_header_does_not_buy_a_fresh_allowance(self):
        # Spend the allowance, every request claiming a different address.
        # If the header were believed, each would land in a bucket of its
        # own and the limit would never be reached.
        codes = [
            self.post_enter("crowd%d@example.org" % i, "X-Forwarded-For: 9.9.9.%d" % i)
            for i in range(22)
        ]
        self.assertIn(429, codes, "a made-up X-Forwarded-For bought a fresh allowance")

    def test_the_edge_is_still_believed_when_it_speaks(self):
        """Behind Fly the real address does arrive in a header, and two
        genuinely different visitors must not share one allowance."""
        first = [
            self.post_enter("edgeone%d@example.org" % i, "Fly-Client-IP: 203.0.113.7")
            for i in range(22)
        ]
        self.assertIn(429, first, "the edge address was never used as a bucket")
        # a different visitor, arriving through the same edge, is not caught
        # in the first one's limit
        self.assertEqual(
            self.post_enter("edgetwo@example.org", "Fly-Client-IP: 203.0.113.8"), 200
        )


class TestSecureCookies(unittest.TestCase):
    def test_cookies_are_marked_secure_when_the_notebook_is_on_https(self):
        notebook = Notebook(NOTEBOOK_URL="https://notebook.example.org")
        try:
            raw = notebook.raw("GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            planted = [l for l in raw.split("\r\n") if l.lower().startswith("set-cookie")]
            self.assertTrue(planted, "no cookie was planted at all")
            for line in planted:
                self.assertIn("Secure", line)
                self.assertIn("HttpOnly", line)
        finally:
            notebook.stop()


# --- the ledger on disk ---------------------------------------------------

# --- the house rules ------------------------------------------------------

class TestHouseRules(NotebookTestCase):
    def test_the_rules_are_there_for_anyone_to_read(self):
        reply = self.notebook.visitor().get("/house")
        self.assertEqual(reply.status, 200)
        self.assertIn("A bet is not a wish", reply.body)

    def test_every_page_points_at_them(self):
        self.assertIn('href="/house"', self.notebook.visitor().get("/").body)


# --- keeping the ledger ---------------------------------------------------

KEEPER = "keeper@example.org"


class TestTheDoorIsShut(NotebookTestCase):
    """With nobody named as a keeper, the desk is not there at all."""

    def test_a_stranger_finds_nothing(self):
        self.assertEqual(self.notebook.visitor().get("/keep").status, 404)

    def test_a_signed_in_reader_finds_nothing_either(self):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": "nosy@example.org"})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        visitor.get(link.group(1))
        self.assertEqual(visitor.get("/keep").status, 404)

    def test_and_cannot_strike_anything_by_posting(self):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": "pushy@example.org"})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        visitor.get(link.group(1))
        struck = visitor.post("/keep", {"deed": "strike", "bet": "1", "why": "mine now"},
                              csrf_from="/")
        self.assertEqual(struck.status, 404)
        self.assertEqual(self.notebook.visitor().get("/bet/1").status, 200)


class TestKeepingTheLedger(FreshNotebookTestCase):
    env = {"NOTEBOOK_KEEPERS": KEEPER}

    def keeper(self):
        return self.signed_in(KEEPER)

    def test_the_keeper_is_let_in_and_nobody_else(self):
        self.assertEqual(self.keeper().get("/keep").status, 200)
        self.assertEqual(self.signed_in("reader@example.org").get("/keep").status, 404)

    def test_only_the_keeper_is_offered_the_door(self):
        self.assertIn('href="/keep"', self.keeper().get("/").body)
        self.assertNotIn('href="/keep"', self.signed_in("plain@example.org").get("/").body)

    def test_a_struck_entry_leaves_the_ledger_and_can_come_back(self):
        keeper = self.keeper()
        stranger = self.notebook.visitor()

        keeper.post("/keep", {"deed": "strike", "bet": "5", "why": "not a bet"},
                    csrf_from="/keep")
        self.assertEqual(stranger.get("/bet/5").status, 404)
        self.assertNotIn("/bet/5", stranger.get("/").body)
        # the keeper can still look at what they struck
        self.assertEqual(keeper.get("/bet/5").status, 200)

        keeper.post("/keep", {"deed": "restore", "bet": "5"}, csrf_from="/keep")
        self.assertEqual(stranger.get("/bet/5").status, 200)

    def test_a_struck_entry_is_out_of_the_counts_and_the_copies(self):
        keeper = self.keeper()
        before = self.notebook.visitor().get("/export.txt").body
        self.assertIn("oral examination", before)
        keeper.post("/keep", {"deed": "strike", "bet": "2", "why": "spam"}, csrf_from="/keep")
        after = self.notebook.visitor().get("/export.txt").body
        self.assertNotIn("oral examination", after)
        keeper.post("/keep", {"deed": "restore", "bet": "2"}, csrf_from="/keep")

    def test_the_reason_is_kept_with_the_entry(self):
        keeper = self.keeper()
        keeper.post("/keep", {"deed": "strike", "bet": "6", "why": "a slogan, not a wager"},
                    csrf_from="/keep")
        self.assertIn("a slogan, not a wager", keeper.get("/keep").body)
        keeper.post("/keep", {"deed": "restore", "bet": "6"}, csrf_from="/keep")

    def test_burning_is_asked_twice(self):
        keeper = self.keeper()
        asked = keeper.post("/keep", {"deed": "burn", "bet": "7"}, csrf_from="/keep")
        self.assertEqual(asked.status, 200)
        self.assertIn("no getting it back", asked.body)
        # nothing has happened yet
        self.assertEqual(self.notebook.visitor().get("/bet/7").status, 200)

        keeper.post("/keep", {"deed": "burn-for-good", "bet": "7"}, csrf_from="/keep")
        self.assertEqual(self.notebook.visitor().get("/bet/7").status, 404)

    def test_a_whole_hand_can_be_struck_at_once(self):
        keeper = self.keeper()
        page = keeper.get("/keep").body
        hand = re.search(r'name="hand" value="(\d+)"', page)
        self.assertIsNotNone(hand, "no hand was offered to strike")
        reply = keeper.post("/keep", {"deed": "strike-hand", "hand": hand.group(1),
                                      "why": "spam"}, csrf_from="/keep")
        self.assertEqual(reply.status, 200)
        self.assertIn("Struck", reply.body)

    def test_a_keeper_cannot_be_struck_from_the_desk(self):
        keeper = self.keeper()
        keeper.post("/propose", {
            "claim": "By 2035 the keeper will still be keeping this ledger.",
            "reasoning": "", "category": "everyday life", "horizon": "2035",
        })
        page = keeper.get("/keep").body
        rows = re.findall(r'name="hand" value="(\d+)"', page)
        # find the keeper's own row by striking each and checking the refusal
        refusals = [
            keeper.post("/keep", {"deed": "strike-hand", "hand": h, "why": "x"},
                        csrf_from="/keep").body
            for h in rows
        ]
        self.assertTrue(
            any("is not struck from here" in r for r in refusals),
            "the keeper's own hand was not protected",
        )
        # and the keeper's own bet is still standing
        self.assertIn("still be keeping this ledger", keeper.get("/").body)


class TestLedgerUpgrades(unittest.TestCase):
    """A ledger written before anonymous voting must still open."""

    OLD_SCHEMA = """
    CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
      pseudo TEXT NOT NULL UNIQUE, show_pseudo INTEGER NOT NULL DEFAULT 1,
      yearly_letter INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_letter_at TEXT);
    CREATE TABLE bets (id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL REFERENCES users(id), claim TEXT NOT NULL,
      reasoning TEXT NOT NULL DEFAULT '', category TEXT NOT NULL, horizon INTEGER NOT NULL,
      anonymous INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open',
      verdict TEXT NOT NULL DEFAULT '', resolved_at TEXT, created_at TEXT NOT NULL);
    CREATE TABLE votes (bet_id INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
      user_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
      PRIMARY KEY (bet_id, user_id));
    """

    def test_an_old_ledger_keeps_its_votes(self):
        folder = tempfile.mkdtemp(prefix="notebook-old-")
        path = os.path.join(folder, "ledger.sqlite3")
        try:
            old = sqlite3.connect(path)
            old.executescript(self.OLD_SCHEMA)
            old.execute("INSERT INTO users (email, pseudo, created_at)"
                        " VALUES ('old@example.org','old','2026-01-01T00:00:00')")
            old.execute("INSERT INTO bets (user_id, claim, category, horizon, created_at)"
                        " VALUES (1,'an old bet','education',2030,'2026-01-01T00:00:00')")
            old.execute("INSERT INTO votes (bet_id, user_id, created_at)"
                        " VALUES (1,1,'2026-01-01T00:00:00')")
            old.commit()
            old.close()

            done = subprocess.run(
                [PYTHON, "-c",
                 "import db; conn = db.init();"
                 " print(conn.execute('SELECT bet_id, user_id, anon_id FROM votes')"
                 ".fetchone()[:3])"],
                cwd=ROOT, env=dict(os.environ, NOTEBOOK_DB=path),
                capture_output=True, text=True,
            )
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("(1, 1, None)", done.stdout)
        finally:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
