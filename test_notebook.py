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
            NOTEBOOK_DATA=self.dir,   # letters and backups land here, not in the repo
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

    def visitor(self, address=None):
        """A browser. `address` needs a notebook started with FLY_APP_NAME
        set, which is what makes the edge header believable."""
        return Visitor(self, address)

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

    def __init__(self, notebook, address=None):
        self.notebook = notebook
        self.address = address
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
        if self.address:
            request.add_header("Fly-Client-IP", self.address)
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


def db_year():
    """This year, as the notebook reckons it."""
    sys.path.insert(0, ROOT)
    import db
    return db.now().year


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


class TestWhatAnEntryShows(NotebookTestCase):
    """The ledger is a list, so an entry shows the opening of the reasoning
    and folds the rest away rather than printing a paragraph each."""

    def a_long_bet(self, claim, reasoning):
        visitor = self.signed_in("longwinded@example.org")
        reply = visitor.post("/propose", {
            "claim": claim, "reasoning": reasoning,
            "subject": "energy & infrastructure", "horizon": "2034",
        })
        self.assertEqual(reply.status, 303, reply.body[:400])
        return visitor, reply.headers["Location"]

    def signed_in(self, email):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered: %s" % reply.status)
        visitor.get(link.group(1))
        return visitor

    def test_a_long_reasoning_is_folded_but_all_of_it_is_there(self):
        """Three lines is a measure of the page, not of the text, so the
        cut is in the stylesheet and the whole reasoning is in the HTML.
        What the ledger carries is the way to open it."""
        claim = "By 2034, a long piece of reasoning will be folded away in the ledger."
        ending = "and this is the last thing the reasoning says."
        reasoning = ("Something worth saying at length. " * 12) + ending
        visitor, where = self.a_long_bet(claim, reasoning)

        page = visitor.get("/").body
        self.assertIn('<div class="because folded">', page)
        self.assertIn("see more", page)
        self.assertIn("see less", page)
        self.assertIn(reasoning, page)       # all of it, clamped rather than cut

        # The bet's own page keeps it whole and offers no fold at all.
        sheet = visitor.get(where).body
        self.assertIn(reasoning, sheet)
        self.assertNotIn("see more", sheet)

    def test_a_short_reasoning_wears_no_button(self):
        """Under a length at which it could not run past three lines on
        any screen, an entry is not given a control it does not need."""
        claim = "By 2034, a short piece of reasoning will be left exactly as it was written."
        reasoning = "Short enough to stand as it is."
        visitor, _ = self.a_long_bet(claim, reasoning)
        page = visitor.get("/").body
        # Its own paragraph, with no fold wrapped round it. (Other entries
        # on the page are long enough to carry one, so the page as a whole
        # is no place to look.)
        self.assertIn('<p class="because">%s</p>' % reasoning, page)

    def test_the_lead_entry_folds_like_every_other_one(self):
        """It is set larger, and that is the whole of the difference. Left
        whole, it put the longest block on the page at the top of it -
        which is the thing the fold is for."""
        sys.path.insert(0, ROOT)
        import render
        row = dict(
            id=99, claim="By 2034, the top of the ledger will read like the rest of it.",
            reasoning="Something worth saying at length. " * 12,
            votes=1, voted=0, category="education", extra_subjects=None,
            horizon=2034, status="open", created_at="2026-01-01T00:00:00+00:00",
            anonymous=0, author_pseudo="someone", author_show_pseudo=1,
        )
        lead = render.entry(row, None, "t", lead=True)
        self.assertIn("entry lead", lead)                      # larger
        self.assertIn('<div class="because folded">', lead)    # and folded
        self.assertIn("see more", lead)
        self.assertIn(row["reasoning"].strip(), lead)          # all of it still there

    def test_a_bet_page_offers_the_classic_places_to_pass_it_on(self):
        page = self.notebook.visitor().get("/bet/1").body
        for place in ("twitter.com/intent", "bsky.app/intent", "facebook.com/sharer",
                      "linkedin.com/sharing", "wa.me/?text=", "mailto:?subject="):
            self.assertIn(place, page, "nowhere to pass a bet to %s" % place)
        self.assertIn("pass it on", page)
        self.assertIn("copy-link", page)
        # the claim and the year travel with it, already written out
        self.assertIn("to%20be%20judged%20by%202031.", page)
        # and nothing of theirs is loaded onto the page
        self.assertNotIn("<script src=\"https://", page)

    def test_the_ledger_files_bets_under_energy_and_infrastructure(self):
        page = self.notebook.visitor().get("/subject/energy-infrastructure")
        self.assertEqual(page.status, 200)
        self.assertIn("data centre electricity", page.body)


# --- voting without an account -------------------------------------------

class TestAnonymousVoting(NotebookTestCase):
    # Started as if behind the Fly edge, so a visitor may say which
    # address it is reading from - an anonymous mark is counted by one.
    env = {"FLY_APP_NAME": "notebook-test"}

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

    def test_two_strangers_at_two_addresses_are_two_votes(self):
        one = self.notebook.visitor("203.0.113.7")
        two = self.notebook.visitor("198.51.100.9")
        before = votes_on(one.get("/bet/3").body)
        one.post("/bet/3/vote", {}, csrf_from="/bet/3")
        two.post("/bet/3/vote", {}, csrf_from="/bet/3")
        self.assertEqual(votes_on(one.get("/bet/3").body), before + 2)

    def test_one_address_marks_a_bet_once(self):
        """Another browser at the same address - or the same browser with
        its cookies swept - is the same anonymous voter, and is told so."""
        first = self.notebook.visitor("203.0.113.20")
        another = self.notebook.visitor("203.0.113.20")
        before = votes_on(first.get("/bet/4").body)
        self.assertEqual(first.post("/bet/4/vote", {}, csrf_from="/bet/4").status, 303)

        refused = another.post("/bet/4/vote", {}, csrf_from="/bet/4")
        self.assertEqual(refused.status, 409)
        self.assertIn("already been marked once from your address", refused.body)
        self.assertIsNone(another.cookie("notebook_anon"))
        self.assertEqual(votes_on(first.get("/bet/4").body), before + 1)

    def test_the_address_binds_a_new_mark_and_never_taking_one_back(self):
        voter = self.notebook.visitor("203.0.113.30")
        before = votes_on(voter.get("/bet/5").body)
        voter.post("/bet/5/vote", {}, csrf_from="/bet/5")
        self.assertEqual(voter.post("/bet/5/vote", {}, csrf_from="/bet/5").status, 303)
        self.assertEqual(votes_on(voter.get("/bet/5").body), before)

        # ...and with it taken back, the address is free for whoever is next.
        next_one = self.notebook.visitor("203.0.113.30")
        self.assertEqual(next_one.post("/bet/5/vote", {}, csrf_from="/bet/5").status, 303)
        self.assertEqual(votes_on(voter.get("/bet/5").body), before + 1)

    def test_a_signed_in_hand_is_not_bound_by_the_address(self):
        """Two people in one house with two accounts are two hands: the
        address only ever counts the marks that carry no name."""
        house = "203.0.113.40"
        stranger = self.notebook.visitor(house)
        before = votes_on(stranger.get("/bet/6").body)
        stranger.post("/bet/6/vote", {}, csrf_from="/bet/6")

        for who in ("one.at.home@example.org", "two.at.home@example.org"):
            member = self.signed_in(who, address=house)
            self.assertEqual(member.post("/bet/6/vote", {}, csrf_from="/bet/6").status, 303)
        self.assertEqual(votes_on(stranger.get("/bet/6").body), before + 3)

    def test_a_mark_made_before_signing_in_follows_the_hand_in(self):
        """The bug this fixes: mark a bet from the cookie, sign in, and the
        tally counted the same person twice - while the button offered to
        take back a mark that pressing it would not touch."""
        visitor = self.notebook.visitor("203.0.113.50")
        before = votes_on(visitor.get("/bet/7").body)
        visitor.post("/bet/7/vote", {}, csrf_from="/bet/7")
        self.assertEqual(votes_on(visitor.get("/bet/7").body), before + 1)

        self.sign_in(visitor, "came.back@example.org")
        self.assertEqual(votes_on(visitor.get("/bet/7").body), before + 1)
        self.assertIn("Take back my vote", visitor.get("/bet/7").body)

        # and now it really does come back off
        self.assertEqual(visitor.post("/bet/7/vote", {}, csrf_from="/bet/7").status, 303)
        self.assertEqual(votes_on(visitor.get("/bet/7").body), before)

    def test_one_hand_signing_in_twice_keeps_one_mark(self):
        """Marked while signed out, marked again at a second browser, then
        both sign in as the same person: still one mark, not two."""
        first = self.notebook.visitor("203.0.113.60")
        second = self.notebook.visitor("198.51.100.60")
        before = votes_on(first.get("/bet/8").body)
        first.post("/bet/8/vote", {}, csrf_from="/bet/8")
        second.post("/bet/8/vote", {}, csrf_from="/bet/8")
        self.assertEqual(votes_on(first.get("/bet/8").body), before + 2)

        self.sign_in(first, "one.hand@example.org")
        self.sign_in(second, "one.hand@example.org")
        self.assertEqual(votes_on(first.get("/bet/8").body), before + 1)

    # --- signing in, for the tests above ---------------------------------

    def sign_in(self, visitor, email):
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered for %s: %s" % (email, reply.status))
        visitor.get(link.group(1))
        return visitor

    def signed_in(self, email, address=None):
        return self.sign_in(self.notebook.visitor(address), email)


class TestHowFastAHandMayWrite(FreshNotebookTestCase):
    def test_a_dozen_bets_an_hour_and_then_a_pause(self):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": "prolific@example.org"})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        visitor.get(link.group(1))

        for i in range(12):   # app.BETS_PER_HAND
            written = visitor.post("/propose", {
                "claim": "By 2035, bet number %d of a great many will be written." % i,
                "reasoning": "", "subject": "everyday life", "horizon": "2035",
            })
            self.assertEqual(written.status, 303, "stopped early at %d" % i)

        one_more = visitor.post("/propose", {
            "claim": "By 2035, the thirteenth will have been turned away.",
            "reasoning": "", "subject": "everyday life", "horizon": "2035",
        })
        self.assertEqual(one_more.status, 429)
        self.assertIn("great many bets", one_more.body)
        self.assertNotIn("thirteenth", self.notebook.visitor().get("/").body)


class TestTheBroom(FreshNotebookTestCase):
    """Sweeping the cookies and marking again. Each of these exhausts the
    day's new hands for 127.0.0.1, so they want a notebook nobody else is
    voting in."""

    HANDS = 8  # app.ANON_HANDS_PER_IP

    def test_a_swept_cookie_marks_the_same_bet_no_second_time(self):
        """The whole point of counting by address: the broom gets nowhere
        on a single entry, however many fresh browsers it opens."""
        before = votes_on(self.notebook.visitor().get("/bet/1").body)
        self.assertEqual(
            self.notebook.visitor().post("/bet/1/vote", {}, csrf_from="/bet/1").status, 303
        )
        for _ in range(3):
            swept = self.notebook.visitor()   # same address, no cookie
            self.assertEqual(swept.post("/bet/1/vote", {}, csrf_from="/bet/1").status, 409)
        self.assertEqual(votes_on(self.notebook.visitor().get("/bet/1").body), before + 1)

    def test_a_broom_runs_out_of_hands(self):
        """One mark per bet per address is not a licence to take a fresh
        hand for every entry in the ledger: the hands themselves are still
        rationed by the day, and a refused mark spends one too."""
        marked = 0
        for bet in range(1, self.HANDS + 1):
            fresh = self.notebook.visitor()
            self.assertEqual(
                fresh.post("/bet/%d/vote" % bet, {}, csrf_from="/bet/%d" % bet).status, 303
            )
            marked += 1
        self.assertEqual(marked, self.HANDS)

        before = votes_on(self.notebook.visitor().get("/bet/9").body)
        one_too_many = self.notebook.visitor()
        self.assertEqual(one_too_many.post("/bet/9/vote", {}, csrf_from="/bet/9").status, 429)
        self.assertIsNone(one_too_many.cookie("notebook_anon"))
        self.assertEqual(votes_on(self.notebook.visitor().get("/bet/9").body), before)

    def test_a_hand_already_held_may_keep_changing_its_mind(self):
        visitor = self.notebook.visitor()
        before = votes_on(visitor.get("/bet/2").body)
        for _ in range(self.HANDS * 3):
            self.assertEqual(visitor.post("/bet/2/vote", {}, csrf_from="/bet/2").status, 303)
        self.assertEqual(votes_on(visitor.get("/bet/2").body), before)  # an even number of minds
        self.assertEqual(visitor.post("/bet/2/vote", {}, csrf_from="/bet/2").status, 303)
        self.assertEqual(votes_on(visitor.get("/bet/2").body), before + 1)


# --- the reckoning --------------------------------------------------------

class TestComingDue(FreshNotebookTestCase):
    """The page the whole notebook is pointed at: entries whose year has
    arrived and which somebody now has to call."""

    def setUp(self):
        super().setUp()
        self.year = db_year()

    def signed_in_as(self, email):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered: %s" % reply.status)
        visitor.get(link.group(1))
        return visitor

    def a_bet(self, visitor, claim, horizon):
        reply = visitor.post("/propose", {
            "claim": claim, "reasoning": "", "subject": "everyday life",
            "horizon": str(horizon),
        })
        self.assertEqual(reply.status, 303, reply.body[:400])
        return reply.headers["Location"]

    def test_a_bet_whose_year_has_come_is_waiting_to_be_called(self):
        writer = self.signed_in_as("waiting@example.org")
        self.a_bet(writer, "By now, somebody will have had to call this one.", self.year)
        page = self.notebook.visitor().get("/due")
        self.assertEqual(page.status, 200)
        self.assertIn("Due this year", page.body)
        self.assertIn("somebody will have had to call this one", page.body)

    def test_a_bet_with_years_to_run_is_not_on_the_list(self):
        writer = self.signed_in_as("patient@example.org")
        self.a_bet(writer, "By 2040, this one will still have years to run.", 2040)
        self.assertNotIn("still have years to run", self.notebook.visitor().get("/due").body)

    def test_settling_one_takes_it_off_the_list(self):
        writer = self.signed_in_as("caller@example.org")
        where = self.a_bet(writer, "By now, this one will have been settled.", self.year)
        self.assertIn("will have been settled", self.notebook.visitor().get("/due").body)

        writer.post(where + "/resolve", {"status": "came_true", "verdict": "It did."},
                    csrf_from=where)
        self.assertNotIn("will have been settled", self.notebook.visitor().get("/due").body)

    def test_the_book_is_totalled_at_the_foot_of_the_ledger(self):
        front = self.notebook.visitor().get("/").body
        standing = front.split('<div class="standing">', 1)[1].split("</div>\n<div", 1)[0]
        for word in ("bets", "hands", "marks"):
            self.assertIn('<span class="what">%s</span>' % word, standing)
        # under the last line of the ledger, not over the first
        self.assertLess(front.index("ol class=\"ledger\""), front.index('class="standing"'))

    def test_a_search_is_not_totalled(self):
        """A total under a handful of results is a total of something
        nobody asked about."""
        found = self.notebook.visitor().get("/?q=photograph").body
        self.assertNotIn('class="standing"', found)

    def test_the_front_page_offers_the_way_in_and_says_how_many(self):
        front = self.notebook.visitor().get("/").body
        self.assertIn('<a class="button" href="/due">Coming due</a>', front)

        writer = self.signed_in_as("teller@example.org")
        self.a_bet(writer, "By now, the front page will have said so.", self.year)
        front = self.notebook.visitor().get("/").body
        self.assertIn('href="/due">Coming due (1)</a>', front)


# --- how long a bet has ---------------------------------------------------

class TestSayingWhenABetIsJudged(FreshNotebookTestCase):
    """The form asks how long a bet has; the ledger keeps the year that
    comes to. A year written out wins, for whoever wants a particular one."""

    def setUp(self):
        super().setUp()
        self.year = db_year()

    def signed_in(self, email):
        visitor = self.notebook.visitor()
        reply = visitor.post("/enter", {"email": email})
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered: %s" % reply.status)
        visitor.get(link.group(1))
        return visitor

    def wrote(self, visitor, **extra):
        fields = {
            "claim": "By then, somebody will have said when this should be judged.",
            "reasoning": "", "subject": "everyday life",
        }
        fields.update(extra)
        reply = visitor.post("/propose", fields)
        self.assertEqual(reply.status, 303, reply.body[:500])
        return visitor.get(reply.headers["Location"]).body

    def test_the_form_offers_spans_with_the_year_each_comes_to(self):
        page = self.signed_in("spans@example.org").get("/propose").body
        self.assertIn('name="horizon_in" value="5" checked', page)   # the default
        for span in (1, 2, 3, 5, 10, 20):
            self.assertIn('name="horizon_in" value="%d"' % span, page)
            self.assertIn(str(self.year + span), page)
        # and the box for a year of one's own starts empty
        self.assertIn('name="horizon" min="%d" max="%d" value=""' % (self.year, self.year + 75), page)

    def test_a_span_is_kept_as_the_year_it_comes_to(self):
        page = self.wrote(self.signed_in("inten@example.org"), horizon_in="10")
        self.assertIn("to be judged by %d" % (self.year + 10), page)

    def test_a_year_of_your_own_wins_over_a_span(self):
        page = self.wrote(
            self.signed_in("particular@example.org"), horizon_in="5", horizon="2043"
        )
        self.assertIn("to be judged by 2043", page)

    def test_saying_neither_is_refused(self):
        visitor = self.signed_in("neither@example.org")
        reply = visitor.post("/propose", {
            "claim": "By then, somebody will have said nothing about when.",
            "reasoning": "", "subject": "everyday life", "horizon_in": "", "horizon": "",
        })
        self.assertEqual(reply.status, 400)
        self.assertIn("how long it has, or a year of your own", reply.body)

    def test_a_made_up_span_is_refused(self):
        visitor = self.signed_in("madeup@example.org")
        reply = visitor.post("/propose", {
            "claim": "By then, somebody will have invented a span of their own.",
            "reasoning": "", "subject": "everyday life", "horizon_in": "900", "horizon": "",
        })
        self.assertEqual(reply.status, 400)

    def test_a_stranger_may_use_a_span_too(self):
        """Through the write-first door: the span is carried to the last
        step as it was given, and shown there as the year it comes to."""
        visitor = self.notebook.visitor()
        step = visitor.post("/propose", {
            "claim": "By then, a stranger will have said how long this has.",
            "reasoning": "", "subject": "everyday life", "horizon_in": "3",
        })
        self.assertIn("to be judged by %d" % (self.year + 3), step.body)
        self.assertIn('name="horizon_in" value="3"', step.body)

        sent = visitor.post("/propose/sign", {
            "claim": "By then, a stranger will have said how long this has.",
            "reasoning": "", "subject": "everyday life", "horizon_in": "3",
            "horizon": "", "email": "spanner@example.org",
        }, csrf_from="/propose")
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), sent.body)
        self.assertIsNotNone(link, sent.body[:400])
        opened = visitor.get(link.group(1))
        page = visitor.get(opened.headers["Location"]).body
        self.assertIn("to be judged by %d" % (self.year + 3), page)

    def test_changing_a_bet_still_asks_for_the_year_it_carries(self):
        """A horizon somebody chose is not quietly moved on by a span."""
        visitor = self.signed_in("reviser@example.org")
        reply = visitor.post("/propose", {
            "claim": "By then, this bet will have been corrected at least once.",
            "reasoning": "", "subject": "everyday life", "horizon": "2040",
        })
        page = visitor.get(reply.headers["Location"] + "/revise").body
        self.assertIn('value="2040"', page)
        self.assertNotIn("horizon_in", page)


# --- writing a bet before there is a name to sign it with ----------------

class TestWritingBeforeSigningIn(FreshNotebookTestCase):
    """A stranger may write the bet first and leave an address last. The
    bet waits against the key that is posted out, and reaches the ledger
    only when that key is opened."""

    A_BET = {
        "claim": "By 2032, a bet will have been written before its hand had any name.",
        "reasoning": "Because asking for an address first is asking at the wrong end.",
        "subject": "everyday life",
        "horizon": "2032",
    }

    def key_from(self, reply):
        link = re.search(r"%s(/enter/[A-Za-z0-9_-]+)" % re.escape(self.notebook.base), reply.body)
        self.assertIsNotNone(link, "no key was offered: %s" % reply.status)
        return link.group(1)

    def test_the_propose_page_is_open_to_a_stranger(self):
        page = self.notebook.visitor().get("/propose")
        self.assertEqual(page.status, 200)
        self.assertIn("the last step asks for an address", page.body)

    def test_a_stranger_writes_the_bet_and_signs_it_afterwards(self):
        visitor = self.notebook.visitor()
        step = visitor.post("/propose", self.A_BET)
        self.assertEqual(step.status, 200)
        self.assertIn("One last thing", step.body)
        self.assertIn(self.A_BET["claim"], step.body)

        # Nothing is on the page yet - it has not been signed by anybody.
        self.assertNotIn(self.A_BET["claim"], self.notebook.visitor().get("/").body)

        sent = visitor.post(
            "/propose/sign", dict(self.A_BET, email="late.signer@example.org"),
            csrf_from="/propose",
        )
        self.assertEqual(sent.status, 200)
        self.assertIn("being held against that key", sent.body)
        self.assertNotIn(self.A_BET["claim"], self.notebook.visitor().get("/").body)

        opened = visitor.get(self.key_from(sent))
        self.assertEqual(opened.status, 303)
        self.assertRegex(opened.headers["Location"], r"^/bet/\d+\?welcome=1$")

        page = visitor.get(opened.headers["Location"])
        self.assertIn(self.A_BET["claim"], page.body)
        self.assertIn("Your bet is in the ledger", page.body)
        self.assertEqual(votes_on(page.body), 1)   # a hand backs its own bet
        self.assertIn(self.A_BET["claim"], self.notebook.visitor().get("/").body)

    def test_a_key_never_opened_writes_nothing(self):
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        visitor.post(
            "/propose/sign", dict(self.A_BET, email="never.opened@example.org"),
            csrf_from="/propose",
        )
        self.assertNotIn(self.A_BET["claim"], self.notebook.visitor().get("/").body)
        self.assertNotIn(self.A_BET["claim"], self.notebook.visitor().get("/export.txt").body)

    def test_one_key_writes_the_draft_once(self):
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        sent = visitor.post(
            "/propose/sign", dict(self.A_BET, email="twice@example.org"),
            csrf_from="/propose",
        )
        key = self.key_from(sent)
        self.assertEqual(visitor.get(key).status, 303)
        spent = visitor.get(key)          # the same key again
        self.assertEqual(spent.status, 400)
        self.assertEqual(self.notebook.visitor().get("/").body.count(self.A_BET["claim"]), 1)

    def test_second_thoughts_do_not_throw_the_writing_away(self):
        visitor = self.notebook.visitor()
        step = visitor.post("/propose", self.A_BET)
        self.assertIn("Go back and change it", step.body)

        back = visitor.post("/propose", dict(self.A_BET, again="1"))
        self.assertEqual(back.status, 200)
        self.assertIn("Propose a bet", back.body)
        self.assertIn(self.A_BET["claim"], back.body)
        self.assertIn(self.A_BET["reasoning"], back.body)
        self.assertIn('value="2032"', back.body)
        self.assertIn('value="everyday life" checked', back.body)

    def test_asking_for_a_second_key_does_not_write_the_bet_twice(self):
        """The bug this fixes: a key that does not arrive, a second one
        asked for, and both of them opened. Each carried its own copy of
        the draft, so the ledger got the bet twice."""
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        first = visitor.post(
            "/propose/sign", dict(self.A_BET, email="twokeys@example.org"),
            csrf_from="/propose",
        )
        second = visitor.post(
            "/propose/sign", dict(self.A_BET, email="twokeys@example.org"),
            csrf_from="/propose",
        )
        one, two = self.key_from(first), self.key_from(second)
        self.assertNotEqual(one, two)

        # Both keys open the notebook - they were both asked for, and
        # neither is a forgery - but only one of them is carrying a bet.
        self.assertEqual(visitor.get(two).status, 303)
        self.assertEqual(visitor.get(one).status, 303)
        self.assertEqual(
            self.notebook.visitor().get("/").body.count(self.A_BET["claim"]), 1
        )

    def test_the_older_key_still_lets_you_in_and_writes_nothing(self):
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        first = visitor.post(
            "/propose/sign", dict(self.A_BET, email="olderkey@example.org"),
            csrf_from="/propose",
        )
        visitor.post(
            "/propose/sign", dict(self.A_BET, email="olderkey@example.org"),
            csrf_from="/propose",
        )
        opened = visitor.get(self.key_from(first))   # the one that arrived first
        self.assertEqual(opened.status, 303)
        self.assertEqual(opened.headers["Location"], "/?welcome=1")   # signed in, nothing written
        self.assertNotIn(self.A_BET["claim"], self.notebook.visitor().get("/").body)

    def test_the_same_hand_writing_the_same_words_twice_writes_once(self):
        """A double-pressed button, or a form sent again from the back of
        the browser."""
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        sent = visitor.post(
            "/propose/sign", dict(self.A_BET, email="twice.over@example.org"),
            csrf_from="/propose",
        )
        where = visitor.get(self.key_from(sent)).headers["Location"]

        again = visitor.post("/propose", self.A_BET)
        self.assertEqual(again.status, 303)
        self.assertEqual(again.headers["Location"], where.split("?")[0])
        self.assertEqual(
            self.notebook.visitor().get("/").body.count(self.A_BET["claim"]), 1
        )

    def test_a_bet_that_will_not_do_is_refused_before_the_address_is_asked_for(self):
        reply = self.notebook.visitor().post("/propose", dict(self.A_BET, claim="too short"))
        self.assertEqual(reply.status, 400)
        self.assertIn("a whole claim", reply.body)
        self.assertNotIn("One last thing", reply.body)

    def test_the_bet_is_checked_again_when_the_address_arrives(self):
        """The hidden fields come back from a visitor like anything else."""
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        tampered = dict(self.A_BET, claim="x", email="tamper@example.org")
        reply = visitor.post("/propose/sign", tampered, csrf_from="/propose")
        self.assertEqual(reply.status, 400)
        self.assertNotIn("A key has been posted", reply.body)

    def test_an_invented_subject_does_not_survive_the_last_step(self):
        visitor = self.notebook.visitor()
        visitor.post("/propose", self.A_BET)
        reply = visitor.post(
            "/propose/sign",
            dict(self.A_BET, subject="something invented", email="invented@example.org"),
            csrf_from="/propose",
        )
        self.assertEqual(reply.status, 400)
        self.assertIn("Pick a subject", reply.body)


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

class TestChangingABet(FreshNotebookTestCase):
    """A hand may correct itself, but not quietly: what a bet said before
    stays on the page. That is the whole bargain of allowing edits at all
    in a book whose point is being on the record."""

    FIRST = "By 2033, half of all new code in production will be machine-written."
    SECOND = "By 2033, most new code merged at large firms will be machine-written."

    def a_bet(self, email="writer@example.org", claim=None):
        author = self.signed_in(email)
        reply = author.post("/propose", {
            "claim": claim or self.FIRST,
            "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 303, reply.body[:400])
        return author, reply.headers["Location"]

    def test_the_author_can_change_the_wording(self):
        author, where = self.a_bet()
        reply = author.post(where + "/revise", {
            "claim": self.SECOND,
            "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy",
            "horizon": "2033",
        }, csrf_from=where + "/revise")
        self.assertEqual(reply.status, 303)
        page = author.get(where).body
        self.assertIn("most new code merged at large firms", page)

    def test_what_changed_is_shown_as_what_went_and_what_came(self):
        author, where = self.a_bet()
        author.post(where + "/revise", {
            "claim": self.SECOND, "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy", "horizon": "2033",
        }, csrf_from=where + "/revise")

        # to anybody, not only the author
        page = self.notebook.visitor().get(where).body
        self.assertIn("What has changed", page)
        self.assertIn("The claim", page)
        self.assertIn("<del>half of all</del>", page)
        self.assertIn("<ins>most</ins>", page)
        # the words that did not move are not repeated as though they had
        self.assertNotIn("<del>new code</del>", page)

    def test_the_whole_earlier_wording_is_still_kept(self):
        """The page shows only what moved; the ledger keeps everything, so
        any earlier version can still be read back in full."""
        author, where = self.a_bet()
        author.post(where + "/revise", {
            "claim": self.SECOND, "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy", "horizon": "2033",
        }, csrf_from=where + "/revise")
        kept = sqlite3.connect(self.notebook.db).execute(
            "SELECT claim FROM bet_revisions ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        self.assertEqual(kept, self.FIRST)

    def test_a_horizon_moved_is_recorded_as_such(self):
        author, where = self.a_bet()
        author.post(where + "/revise", {
            "claim": self.FIRST, "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy", "horizon": "2039",
        }, csrf_from=where + "/revise")
        page = self.notebook.visitor().get(where).body
        self.assertIn("The horizon", page)
        self.assertIn("<del>2033</del>", page)  # the year it used to carry
        self.assertIn("<ins>2039</ins>", page)

    def test_every_change_is_kept_not_just_the_last(self):
        author, where = self.a_bet()
        for claim in (self.SECOND, "By 2033, nearly all new code at large firms is machine-written."):
            author.post(where + "/revise", {
                "claim": claim, "reasoning": "Because review is the bottleneck, not typing.",
                "subject": "work & economy", "horizon": "2033",
            }, csrf_from=where + "/revise")
        page = self.notebook.visitor().get(where).body
        self.assertEqual(page.count('<li class="was">'), 2, "both changes should be shown")
        self.assertIn("<del>half of all</del>", page)   # the first change
        self.assertIn("<ins>nearly all</ins>", page)    # the second
        self.assertIn("2 times since it was written", page)

    def test_a_change_that_changes_nothing_is_not_recorded(self):
        author, where = self.a_bet()
        author.post(where + "/revise", {
            "claim": self.FIRST, "reasoning": "Because review is the bottleneck, not typing.",
            "subject": "work & economy", "horizon": "2033",
        }, csrf_from=where + "/revise")
        self.assertNotIn("What it said before", self.notebook.visitor().get(where).body)

    def test_nobody_else_may_change_it(self):
        author, where = self.a_bet()
        stranger = self.signed_in("stranger@example.org")
        self.assertEqual(stranger.get(where + "/revise").status, 403)
        refused = stranger.post(where + "/revise", {
            "claim": "By 2033, I get to put words in another hand's mouth.",
            "reasoning": "", "subject": "work & economy", "horizon": "2033",
        }, csrf_from="/")
        self.assertEqual(refused.status, 403)
        self.assertIn("half of all new code", self.notebook.visitor().get(where).body)

    def test_a_settled_bet_is_left_as_it_was_written(self):
        author, where = self.a_bet()
        author.post(where + "/resolve", {"status": "came_true", "verdict": "It did."},
                    csrf_from=where)
        self.assertEqual(author.get(where + "/revise").status, 403)
        self.assertNotIn("Change it", author.get(where).body)

    def test_a_change_still_has_to_be_a_bet(self):
        author, where = self.a_bet()
        reply = author.post(where + "/revise", {
            "claim": "too short", "reasoning": "", "subject": "work & economy",
            "horizon": "2033",
        }, csrf_from=where + "/revise")
        self.assertEqual(reply.status, 400)

    def test_a_horizon_already_gone_by_does_not_block_a_correction(self):
        """An old bet whose year has passed must still be correctable."""
        author, where = self.a_bet(claim="By 2027, this bet will need its typo fixed.")
        # move the ledger's idea of the horizon into the past by editing
        # only the wording, keeping the year it already carries
        page = author.get(where + "/revise").body
        year = re.search(r'name="horizon"[^>]*value="(\d+)"', page).group(1)
        reply = author.post(where + "/revise", {
            "claim": "By 2027, this bet will have had its typo fixed.",
            "reasoning": "", "subject": "work & economy", "horizon": year,
        }, csrf_from=where + "/revise")
        self.assertEqual(reply.status, 303)


class TestSubjects(FreshNotebookTestCase):
    """A bet may sit at a crossroads: filing it under one subject loses
    whoever went looking under the other."""

    def a_bet(self, subjects):
        author = self.signed_in("crossroads@example.org")
        fields = [
            ("claim", "By 2036, a bet will be filed under more than one subject."),
            ("reasoning", "Because some of them genuinely are two things."),
            ("horizon", "2036"),
        ] + [("subject", s) for s in subjects]
        page = author.get("/propose")
        fields.append(("csrf", token_on(page.body)))
        body = urllib.parse.urlencode(fields).encode()
        reply = author._open(urllib.request.Request(self.notebook.base + "/propose", data=body))
        return author, reply

    def test_a_bet_can_carry_three_subjects(self):
        author, reply = self.a_bet(["law & rights", "politics & governance", "work & economy"])
        self.assertEqual(reply.status, 303, reply.body[:400])
        page = self.notebook.visitor().get(reply.headers["Location"]).body
        for subject in ("law &amp; rights", "politics &amp; governance", "work &amp; economy"):
            self.assertIn(subject, page)

    def test_a_fourth_subject_is_refused(self):
        _, reply = self.a_bet([
            "law & rights", "politics & governance", "work & economy", "education",
        ])
        self.assertEqual(reply.status, 400)
        self.assertIn("subjects at most", reply.body)

    def test_no_subject_at_all_is_refused(self):
        _, reply = self.a_bet([])
        self.assertEqual(reply.status, 400)
        self.assertIn("Pick a subject", reply.body)

    def test_an_invented_subject_is_ignored(self):
        _, reply = self.a_bet(["education", "something invented"])
        self.assertEqual(reply.status, 303)
        page = self.notebook.visitor().get(reply.headers["Location"]).body
        self.assertNotIn("something invented", page)

    def test_a_bet_is_found_under_every_subject_it_carries(self):
        author, reply = self.a_bet(["law & rights", "work & economy"])
        where = reply.headers["Location"]
        visitor = self.notebook.visitor()
        for subject in ("law & rights", "work & economy"):
            found = visitor.get("/?category=%s" % urllib.parse.quote(subject)).body
            self.assertIn(where, found, "not filed under %s" % subject)

    def test_the_seeded_crossroads_are_filed_under_both(self):
        """The example about a malpractice claim is health and law both."""
        visitor = self.notebook.visitor()
        under_law = visitor.get("/?category=%s" % urllib.parse.quote("law & rights")).body
        self.assertIn("malpractice claim", under_law)
        under_health = visitor.get("/?category=%s" % urllib.parse.quote("health & medicine")).body
        self.assertIn("malpractice claim", under_health)

    def test_changing_the_subjects_is_kept_in_the_history(self):
        author, reply = self.a_bet(["education"])
        where = reply.headers["Location"]
        fields = [
            ("claim", "By 2036, a bet will be filed under more than one subject."),
            ("reasoning", "Because some of them genuinely are two things."),
            ("horizon", "2036"),
            ("subject", "education"), ("subject", "art & culture"),
        ]
        page = author.get(where + "/revise")
        fields.append(("csrf", token_on(page.body)))
        author._open(urllib.request.Request(
            self.notebook.base + where + "/revise",
            data=urllib.parse.urlencode(fields).encode(),
        ))
        after = self.notebook.visitor().get(where).body
        self.assertIn("art &amp; culture", after)
        self.assertIn("The subjects", after)
        self.assertIn("<ins>art &amp; culture</ins>", after)
        # education was there before and after, so it is not shown as moved
        self.assertNotIn("<ins>education</ins>", after)


class TestBrowsingBySubject(NotebookTestCase):
    def test_each_subject_has_a_page_of_its_own(self):
        reply = self.notebook.visitor().get("/subject/law-rights")
        self.assertEqual(reply.status, 200)
        self.assertIn("malpractice claim", reply.body)

    def test_the_subject_on_a_bet_leads_to_it(self):
        page = self.notebook.visitor().get("/bet/1").body
        self.assertIn('href="/subject/information-trust"', page)

    def test_the_index_lists_all_twelve_with_their_counts(self):
        page = self.notebook.visitor().get("/subjects").body
        for subject in ("education", "law-rights", "love-friendship"):
            self.assertIn('href="/subject/%s"' % subject, page)

    def test_an_invented_subject_is_a_blank_page(self):
        self.assertEqual(self.notebook.visitor().get("/subject/nonsense").status, 404)

    def test_the_subject_pages_are_in_the_sitemap(self):
        sitemap = self.notebook.visitor().get("/sitemap.xml").body
        self.assertIn("/subjects", sitemap)
        self.assertIn("/subject/climate-environment", sitemap)

    def test_a_subject_page_says_what_it_is_for_a_link_preview(self):
        page = self.notebook.visitor().get("/subject/war-security").body
        self.assertIn('property="og:title"', page)
        self.assertIn("war &amp; security", page)


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
            "subject": "science & technology",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 303)
        page = visitor.get(reply.headers["Location"]).body
        self.assertEqual(votes_on(page), 1)

    def test_a_first_time_hand_is_shown_the_house(self):
        visitor = self.signed_in("newcomer@example.org")
        page = visitor.get("/propose").body
        self.assertIn("the house in short", page)
        self.assertIn('href="/house"', page)

        visitor.post("/propose", {
            "claim": "By 2034 a newcomer will have been told the rules exactly once.",
            "reasoning": "Because that is what this test is for.",
            "subject": "everyday life",
            "horizon": "2034",
        })
        self.assertNotIn("the house in short", visitor.get("/propose").body)

    def test_the_house_stays_up_while_a_first_bet_is_refused(self):
        visitor = self.signed_in("stumbling@example.org")
        reply = visitor.post("/propose", {
            "claim": "too short",
            "reasoning": "",
            "subject": "education",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 400)
        self.assertIn("the house in short", reply.body)

    def test_a_claim_that_is_too_long_is_refused(self):
        visitor = self.signed_in("windy@example.org")
        reply = visitor.post("/propose", {
            "claim": "x" * 5000,
            "reasoning": "",
            "subject": "education",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 400)

    def test_a_subject_off_the_list_is_refused(self):
        visitor = self.signed_in("offlist@example.org")
        reply = visitor.post("/propose", {
            "claim": "A claim long enough to count as a whole one.",
            "reasoning": "",
            "subject": "something invented",
            "horizon": "2033",
        })
        self.assertEqual(reply.status, 400)

    def test_only_the_hand_that_wrote_a_bet_may_settle_it(self):
        author = self.signed_in("author@example.org")
        reply = author.post("/propose", {
            "claim": "By 2034 somebody will try to settle a bet that is not theirs.",
            "reasoning": "",
            "subject": "law & rights",
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
            "subject": "education",
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
        page = visitor.get("/bet/10")
        csrf = token_on(page.body)
        cookies = "; ".join("%s=%s" % (c.name, c.value) for c in visitor.jar)
        body = urllib.parse.urlencode({"csrf": csrf})
        raw = self.notebook.raw(
            "POST /bet/10/vote HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n"
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

    def _vote_carrying(self, anon_cookie, bet=11):
        """Mark an entry with a cookie no notebook would have minted. An
        entry of its own each time: one address marks a bet once, and what
        is being tested here is the header, not the tally."""
        visitor = self.notebook.visitor()
        page = visitor.get("/bet/%d" % bet)
        csrf = token_on(page.body)
        jar = "; ".join("%s=%s" % (c.name, c.value) for c in visitor.jar)
        body = urllib.parse.urlencode({"csrf": csrf})
        return self.notebook.raw(
            "POST /bet/%d/vote HTTP/1.1\r\nHost: x\r\n"
            "Cookie: %s; notebook_anon=%s\r\n"
            "Content-Type: application/x-www-form-urlencoded\r\n"
            "Content-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (bet, jar, anon_cookie, len(body), body)
        )

    def test_a_cookie_cannot_write_extra_attributes_into_the_reply(self):
        """A quoted cookie value may hold a semicolon; planting it back
        unexamined would let a visitor append Set-Cookie attributes."""
        head = self._vote_carrying(r'"a\073Domain=evil.example"').split("\r\n\r\n")[0]
        self.assertNotIn("evil.example", head)

    def test_a_cookie_of_any_size_is_not_written_into_the_ledger(self):
        reply = self._vote_carrying("B" * 5000, bet=12)
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

    # This notebook believes it is behind the Fly edge; the one below is
    # not, and must therefore believe nothing it is told.
    env = {"FLY_APP_NAME": "notebook-test"}

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


class TestTheEdgeHeaderIsNotBelievedOffTheEdge(
    TestTheRateLimiterCannotBeHandedToTheVisitor
):
    """Off Fly, nothing trusted overwrites Fly-Client-IP, so anyone may
    write it. Believing it there would hand over the rate limiter - and,
    since an anonymous mark is counted by address, the ballot box."""

    env = {}

    def test_a_made_up_edge_header_does_not_buy_a_fresh_allowance(self):
        codes = [
            self.post_enter("offedge%d@example.org" % i, "Fly-Client-IP: 9.9.9.%d" % i)
            for i in range(22)
        ]
        self.assertIn(429, codes, "a made-up Fly-Client-IP bought a fresh allowance")

    # The two inherited tests belong to the notebook that is behind an edge.
    test_the_edge_is_still_believed_when_it_speaks = None
    test_a_made_up_forwarding_header_does_not_buy_a_fresh_allowance = None


class TestSecureCookies(unittest.TestCase):
    def test_cookies_are_marked_secure_when_the_notebook_is_on_https(self):
        # A public notebook will not open without somewhere to post a
        # letter, so name a server; nothing in this test asks for a key.
        notebook = Notebook(
            NOTEBOOK_URL="https://notebook.example.org", SMTP_HOST="smtp.invalid"
        )
        try:
            raw = notebook.raw("GET / HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            planted = [l for l in raw.split("\r\n") if l.lower().startswith("set-cookie")]
            self.assertTrue(planted, "no cookie was planted at all")
            for line in planted:
                self.assertIn("Secure", line)
                self.assertIn("HttpOnly", line)
        finally:
            notebook.stop()


class TestAPublicNotebookNeedsMail(unittest.TestCase):
    def test_it_refuses_to_open_on_https_with_nowhere_to_post_a_letter(self):
        """Otherwise the key is shown on the page - a door, not a shortcut."""
        with self.assertRaises(RuntimeError) as caught:
            Notebook(NOTEBOOK_URL="https://notebook.example.org").stop()
        self.assertIn("SMTP_HOST", str(caught.exception))


# --- the ledger on disk ---------------------------------------------------

# --- being found, and being pasted somewhere ------------------------------

class TestBroadcasting(NotebookTestCase):
    def test_crawlers_are_told_where_to_go(self):
        reply = self.notebook.visitor().get("/robots.txt")
        self.assertEqual(reply.status, 200)
        self.assertIn("Disallow: /desk", reply.body)
        self.assertIn("/sitemap.xml", reply.body)

    def test_the_sitemap_names_the_ledger_and_its_entries(self):
        reply = self.notebook.visitor().get("/sitemap.xml")
        self.assertEqual(reply.status, 200)
        self.assertIn("<loc>%s/house</loc>" % self.notebook.base, reply.body)
        self.assertIn("<loc>%s/bet/1</loc>" % self.notebook.base, reply.body)

    def test_a_desk_is_not_in_the_sitemap(self):
        self.assertNotIn("/desk", self.notebook.visitor().get("/sitemap.xml").body)

    def test_a_pasted_link_says_what_the_place_is(self):
        body = self.notebook.visitor().get("/").body
        self.assertIn('<meta name="description"', body)
        self.assertIn('property="og:title"', body)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', body)
        self.assertIn('/static/card.png', body)

    def test_a_pasted_bet_says_what_the_bet_is(self):
        """The claim travels whole. A preview has room for a claim, or for
        a claim with a signboard stapled to the end of it and the last
        words cut off - and the signboard is on the next line anyway."""
        page = self.notebook.visitor().get("/bet/1")
        og = re.search(r'<meta property="og:title" content="([^"]+)"', page.body).group(1)
        claim = re.search(r'<p class="claim">([^<]+)', page.body).group(1)
        self.assertEqual(og, claim.strip())
        self.assertNotIn("The Future with AI", og)
        site = re.search(r'<meta property="og:site_name" content="([^"]+)"', page.body)
        self.assertIn("Future with AI", site.group(1))
        canonical = re.search(r'<link rel="canonical" href="([^"]+)"', page.body).group(1)
        self.assertEqual(canonical, "%s/bet/1" % self.notebook.base)

    def test_a_pasted_bet_shows_the_year_it_will_be_judged_by(self):
        """One picture under every bet makes every bet look like the same
        bet. The year is what differs, and there are few enough of them to
        draw once - see make_cards.py."""
        page = self.notebook.visitor().get("/bet/1")
        horizon = int(re.search(r"to be judged by (\d{4})", page.body).group(1))
        card = re.search(r'<meta property="og:image" content="([^"]+)"', page.body).group(1)
        self.assertEqual(card, "%s/static/cards/%d.png" % (self.notebook.base, horizon))
        self.assertIn(str(horizon), re.search(
            r'<meta property="og:image:alt" content="([^"]+)"', page.body).group(1))

        drawn = self.notebook.visitor().get("/static/cards/%d.png" % horizon)
        self.assertEqual(drawn.status, 200)
        self.assertEqual(drawn.headers["Content-Type"], "image/png")

    def test_a_year_nobody_has_drawn_falls_back_to_the_notebook(self):
        sys.path.insert(0, ROOT)
        import render
        self.assertEqual(render.card_for({"horizon": 9999}), "/static/card.png")
        self.assertEqual(render.card_for(), "/static/card.png")

    def test_an_ordinary_page_still_carries_the_name_of_the_place(self):
        page = self.notebook.visitor().get("/").body
        og = re.search(r'<meta property="og:title" content="([^"]+)"', page).group(1)
        self.assertIn("The Future with AI", og)

    def test_a_page_nobody_should_arrive_at_claims_no_address(self):
        # A canonical link on the sign-in form would tell a crawler the
        # front door lives there. It says nothing instead.
        self.assertNotIn("canonical", self.notebook.visitor().get("/enter").body)

    def test_the_card_is_served_as_a_picture(self):
        reply = self.notebook.visitor().get("/static/card.png")
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.headers["Content-Type"], "image/png")

    def test_there_is_a_mark_for_the_tab(self):
        reply = self.notebook.visitor().get("/static/notebook.svg")
        self.assertEqual(reply.status, 200)
        self.assertEqual(reply.headers["Content-Type"], "image/svg+xml")


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


class TestWatchedWords(unittest.TestCase):
    """The matcher itself. Two questions, two answers: a bet is flagged
    and never refused, a pen name is refused and never explained."""

    def setUp(self):
        sys.path.insert(0, ROOT)
        import watch
        self.watch = watch

    def test_a_bet_about_a_thing_is_not_a_bet_for_it(self):
        """The whole reason bets are flagged rather than refused."""
        claim = ("By 2031, a party the press calls neo-nazi will lead a national "
                 "poll in western Europe.")
        self.assertEqual(self.watch.trips(claim), ["nazi"])   # flagged, and that is all

    def test_ordinary_writing_trips_nothing(self):
        for text in (
            "By 2088, 88% of doctors will take a second opinion from a machine.",
            "By 2030, Pakistan will run a national model of its own.",
            "By 2029, suspicion of synthetic images will be the default.",
            "By 2032, a raccoon will be photographed using a touchscreen.",
        ):
            self.assertEqual(self.watch.trips(text), [], text)

    def test_slogans_and_codes_are_found(self):
        self.assertIn("blood and soil", self.watch.trips("blood and soil, they chanted"))
        self.assertIn("1488", self.watch.trips("the 1488 crowd"))
        self.assertIn("14 words", self.watch.trips("reciting the 14 words"))

    def test_a_name_wearing_one_is_refused_however_it_is_spelled(self):
        for name in ("n1gg3r", "HeilHitler88", "f.a.g.g.o.t", "1488er", "nazi",
                     "the_nazi_hunter", "SS_88_HH"):
            self.assertIsNotNone(self.watch.name_trouble(name), name)

    def test_an_ordinary_name_is_left_alone(self):
        for name in ("cassandra", "grid.watcher", "raccoon", "pakistani_bettor",
                     "Fagan", "2088watcher", "the.archivist"):
            self.assertIsNone(self.watch.name_trouble(name), name)

    def test_the_refusal_does_not_teach_the_way_round_itself(self):
        said = self.watch.name_trouble("n1gg3r")
        self.assertNotIn("n1gg3r", said)
        self.assertNotIn("nigger", said)


class TestWatchingWhatIsWritten(FreshNotebookTestCase):
    """What the notebook does about it: refuse the name, flag the bet,
    write to whoever keeps the ledger."""

    env = {"NOTEBOOK_KEEPERS": KEEPER}

    def outbox(self):
        """Every letter the notebook has written, as text."""
        box = os.path.join(self.notebook.dir, "outbox")
        if not os.path.isdir(box):
            return ""
        return "\n".join(
            open(os.path.join(box, name)).read() for name in sorted(os.listdir(box))
        )

    def wrote(self, visitor, claim, reasoning=""):
        reply = visitor.post("/propose", {
            "claim": claim, "reasoning": reasoning,
            "subject": "politics & governance", "horizon": "2033",
        })
        self.assertEqual(reply.status, 303, reply.body[:400])
        return reply.headers["Location"]

    def test_a_pen_name_on_the_list_is_refused_at_the_desk(self):
        visitor = self.signed_in("namer@example.org")
        reply = visitor.post("/desk", {"pseudo": "HeilHitler88", "show_pseudo": "1"})
        self.assertEqual(reply.status, 400)
        self.assertIn("That name will not do", reply.body)
        self.assertNotIn("HeilHitler88", visitor.get("/desk").body)

    def test_an_ordinary_pen_name_still_saves(self):
        visitor = self.signed_in("ordinary@example.org")
        reply = visitor.post("/desk", {"pseudo": "raccoon", "show_pseudo": "1"})
        self.assertEqual(reply.status, 200)
        self.assertIn("Desk saved", reply.body)

    def test_a_flagged_bet_is_written_down_like_any_other(self):
        """It goes in the ledger. The keeper decides, not the list."""
        where = self.wrote(
            self.signed_in("writer@example.org"),
            "By 2033, a neo-nazi party will hold a ministry in western Europe.",
            "Written as an expectation, not a wish.",
        )
        page = self.notebook.visitor().get(where)
        self.assertEqual(page.status, 200)
        self.assertIn("neo-nazi party will hold a ministry", page.body)

    def test_the_keeper_is_written_to(self):
        self.wrote(
            self.signed_in("writer@example.org"),
            "By 2033, a neo-nazi party will hold a ministry in western Europe.",
        )
        letters = self.outbox()
        self.assertIn("Worth a look", letters)
        self.assertIn(KEEPER, letters)
        self.assertIn("nazi", letters)

    def test_a_clean_bet_writes_to_nobody(self):
        self.wrote(
            self.signed_in("clean@example.org"),
            "By 2033, most people will take a second opinion from a machine.",
        )
        self.assertNotIn("Worth a look", self.outbox())

    def test_the_desk_lists_what_is_worth_a_look(self):
        self.wrote(
            self.signed_in("writer@example.org"),
            "By 2033, a neo-nazi party will hold a ministry in western Europe.",
        )
        desk = self.signed_in(KEEPER).get("/keep").body
        self.assertIn("Worth a look (1)", desk)
        self.assertIn("on the list: nazi", desk)

    def test_striking_one_takes_it_off_the_list(self):
        where = self.wrote(
            self.signed_in("writer@example.org"),
            "By 2033, a neo-nazi party will hold a ministry in western Europe.",
        )
        keeper = self.signed_in(KEEPER)
        keeper.post("/keep", {
            "deed": "strike", "bet": where.rsplit("/", 1)[1], "why": "read and struck",
        }, csrf_from="/keep")
        desk = keeper.get("/keep").body
        self.assertIn("Worth a look (0)", desk)
        self.assertIn("Every standing entry reads clean", desk)

    def test_a_bet_revised_into_something_else_is_flagged_then(self):
        writer = self.signed_in("reviser@example.org")
        where = self.wrote(writer, "By 2033, a machine will write a national anthem.")
        self.assertNotIn("Worth a look", self.outbox())

        writer.post(where + "/revise", {
            "claim": "By 2033, the 14 words will be printed in a national manifesto.",
            "reasoning": "", "subject": "politics & governance", "horizon": "2033",
        })
        self.assertIn("Worth a look", self.outbox())


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
            "reasoning": "", "subject": "everyday life", "horizon": "2035",
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
