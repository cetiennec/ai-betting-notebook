"""Storage for the Future with AI betting notebook.

A single SQLite file under data/. No ORM, no migrations framework: the schema
is created if missing and that is the whole story for a prototype.
"""

import os
import sqlite3
import secrets
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.environ.get("NOTEBOOK_DB", os.path.join(DATA_DIR, "notebook.sqlite3"))

SESSION_DAYS = 90
LOGIN_TOKEN_MINUTES = 60

# Who keeps the ledger. Named in the environment and nowhere else: there is
# deliberately no way to make yourself one of these from inside the notebook.
KEEPERS = {
    address.strip().lower()
    for address in os.environ.get("NOTEBOOK_KEEPERS", "").split(",")
    if address.strip()
}


def is_keeper(user):
    return bool(user) and (user["email"] or "").lower() in KEEPERS

CATEGORIES = [
    "education",
    "politics & governance",
    "work & economy",
    "information & trust",
    "science & technology",
    "health & medicine",
    "art & culture",
    "everyday life",
    "war & security",
    "climate & environment",
    "law & rights",
    "love & friendship",
]

STATUSES = {
    "open": "still open",
    "came_true": "came true",
    "did_not": "did not happen",
    "unclear": "impossible to call",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    pseudo        TEXT NOT NULL UNIQUE,
    show_pseudo   INTEGER NOT NULL DEFAULT 1,
    yearly_letter INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_letter_at TEXT
);

CREATE TABLE IF NOT EXISTS login_tokens (
    token      TEXT PRIMARY KEY,
    email      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    claim      TEXT NOT NULL,
    reasoning  TEXT NOT NULL DEFAULT '',
    category   TEXT NOT NULL,
    horizon    INTEGER NOT NULL,
    anonymous  INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'open',
    verdict    TEXT NOT NULL DEFAULT '',
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    removed_at TEXT,
    removed_why TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS votes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id     INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
    user_id    INTEGER REFERENCES users(id),
    anon_id    TEXT,
    created_at TEXT NOT NULL,
    CHECK ((user_id IS NULL) <> (anon_id IS NULL))
);

CREATE TABLE IF NOT EXISTS rate_limits (
    bucket     TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- A bet may sit at a crossroads: "refusing an AI second opinion will be
-- grounds for a malpractice claim" is health & medicine and law & rights
-- both. bets.category holds the first subject - it is what the bet is
-- mostly about - and this table holds the one or two after it. Reading
-- them apart keeps every older query honest; anything that filters or
-- counts by subject has to look in both.
CREATE TABLE IF NOT EXISTS bet_subjects (
    bet_id  INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    place   INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (bet_id, subject)
);

-- What a bet said before somebody changed it. One row per edit, holding
-- the wording it is replacing, so the whole history of a claim can be
-- read back. Nothing here is ever updated or deleted: a notebook whose
-- earlier pages can be rewritten is not a record of anything.
CREATE TABLE IF NOT EXISTS bet_revisions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    bet_id     INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
    claim      TEXT NOT NULL,
    reasoning  TEXT NOT NULL DEFAULT '',
    category   TEXT NOT NULL,
    subjects   TEXT NOT NULL DEFAULT '',   -- every subject it carried, '|' apart
    horizon    INTEGER NOT NULL,
    replaced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bets_category ON bets(category);
CREATE INDEX IF NOT EXISTS idx_rate_limits_bucket ON rate_limits(bucket);
CREATE INDEX IF NOT EXISTS idx_revisions_bet ON bet_revisions(bet_id);
CREATE INDEX IF NOT EXISTS idx_bet_subjects ON bet_subjects(subject);
"""

MAX_SUBJECTS = 3

# Split out from SCHEMA: these name columns (anon_id) that only exist on
# votes once _migrate() has run, so they must be created after migration.
VOTE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_votes_bet ON votes(bet_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_user ON votes(bet_id, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_votes_anon ON votes(bet_id, anon_id);
"""


def now():
    return datetime.now(timezone.utc)


def stamp(dt=None):
    return (dt or now()).replace(microsecond=0).isoformat()


def parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def backup_to(path):
    """Copy the whole ledger to `path`, safely while it is being written to.

    Not `cp`: a SQLite file copied mid-write is a file with half a
    transaction in it. The backup API takes a consistent picture of a live
    database, which is the only kind worth keeping."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    source = connect()
    copy = sqlite3.connect(path)
    try:
        with copy:
            source.backup(copy)
    finally:
        copy.close()
        source.close()
    return path


def _migrate(conn):
    """One-off shims for databases created before a schema change."""
    bet_cols = [row[1] for row in conn.execute("PRAGMA table_info(bets)").fetchall()]
    if bet_cols and "removed_at" not in bet_cols:
        with conn:
            conn.execute("ALTER TABLE bets ADD COLUMN removed_at TEXT")
            conn.execute("ALTER TABLE bets ADD COLUMN removed_why TEXT NOT NULL DEFAULT ''")

    revision_cols = [row[1] for row in conn.execute("PRAGMA table_info(bet_revisions)").fetchall()]
    if revision_cols and "subjects" not in revision_cols:
        with conn:
            conn.execute(
                "ALTER TABLE bet_revisions ADD COLUMN subjects TEXT NOT NULL DEFAULT ''"
            )

    cols = [row[1] for row in conn.execute("PRAGMA table_info(votes)").fetchall()]
    if cols and "anon_id" not in cols:
        with conn:
            conn.execute("ALTER TABLE votes RENAME TO votes_old")
            conn.executescript(SCHEMA)
            conn.execute(
                """INSERT INTO votes (bet_id, user_id, anon_id, created_at)
                   SELECT bet_id, user_id, NULL, created_at FROM votes_old"""
            )
            conn.execute("DROP TABLE votes_old")


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
    _migrate(conn)
    with conn:
        conn.executescript(VOTE_INDEXES)
    return conn


# --- people ---------------------------------------------------------------

def pseudo_from_email(conn, email):
    """A first-visit pen name: the local part, made unique."""
    base = "".join(c for c in email.split("@")[0] if c.isalnum() or c in "._-")
    base = (base or "someone")[:24]
    candidate, n = base, 1
    while conn.execute("SELECT 1 FROM users WHERE pseudo = ?", (candidate,)).fetchone():
        n += 1
        candidate = "%s%d" % (base[:20], n)
    return candidate


def user_by_email(conn, email):
    return conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()


def user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def create_user(conn, email):
    email = email.lower().strip()
    with conn:
        conn.execute(
            "INSERT INTO users (email, pseudo, created_at) VALUES (?, ?, ?)",
            (email, pseudo_from_email(conn, email), stamp()),
        )
    return user_by_email(conn, email)


def update_user(conn, user_id, pseudo, show_pseudo, yearly_letter):
    with conn:
        conn.execute(
            "UPDATE users SET pseudo = ?, show_pseudo = ?, yearly_letter = ? WHERE id = ?",
            (pseudo, 1 if show_pseudo else 0, 1 if yearly_letter else 0, user_id),
        )


def pseudo_taken(conn, pseudo, user_id):
    row = conn.execute(
        "SELECT 1 FROM users WHERE pseudo = ? AND id <> ?", (pseudo, user_id)
    ).fetchone()
    return row is not None


# --- entering the room ----------------------------------------------------

def rate_limited(conn, bucket, limit, window_minutes):
    """True (and does not count this attempt) once `bucket` has hit `limit`
    attempts within the last `window_minutes`; otherwise records this one
    and returns False. `bucket` is any string the caller wants to throttle
    on its own - typically "ip:1.2.3.4" or "email:x@y.com"."""
    cutoff = stamp(now() - timedelta(minutes=window_minutes))
    with conn:
        conn.execute("DELETE FROM rate_limits WHERE bucket = ? AND created_at < ?", (bucket, cutoff))
        count = conn.execute(
            "SELECT COUNT(*) FROM rate_limits WHERE bucket = ?", (bucket,)
        ).fetchone()[0]
        if count >= limit:
            return True
        conn.execute("INSERT INTO rate_limits (bucket, created_at) VALUES (?, ?)", (bucket, stamp()))
    return False


def new_login_token(conn, email):
    token = secrets.token_urlsafe(24)
    with conn:
        conn.execute(
            "INSERT INTO login_tokens (token, email, created_at) VALUES (?, ?, ?)",
            (token, email.lower().strip(), stamp()),
        )
    return token


def spend_login_token(conn, token):
    """Return the email a fresh token belongs to, and burn it."""
    row = conn.execute("SELECT * FROM login_tokens WHERE token = ?", (token,)).fetchone()
    if row is None or row["used_at"]:
        return None
    created = parse(row["created_at"])
    if created is None or now() - created > timedelta(minutes=LOGIN_TOKEN_MINUTES):
        return None
    with conn:
        conn.execute("UPDATE login_tokens SET used_at = ? WHERE token = ?", (stamp(), token))
    return row["email"]


def new_session(conn, user_id):
    token = secrets.token_urlsafe(24)
    with conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)",
            (token, user_id, stamp()),
        )
    return token


def session_user(conn, token):
    if not token:
        return None
    row = conn.execute(
        """SELECT u.*, s.created_at AS session_started
           FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?""",
        (token,),
    ).fetchone()
    if row is None:
        return None
    started = parse(row["session_started"])
    if started is None or now() - started > timedelta(days=SESSION_DAYS):
        end_session(conn, token)
        return None
    return row


def end_session(conn, token):
    with conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


# --- the bets themselves --------------------------------------------------

BET_SELECT = """
SELECT b.*,
       u.pseudo AS author_pseudo,
       u.show_pseudo AS author_show_pseudo,
       (SELECT group_concat(subject, '|') FROM
          (SELECT subject FROM bet_subjects WHERE bet_id = b.id ORDER BY place, subject)
       ) AS extra_subjects,
       (SELECT COUNT(*) FROM bet_revisions r WHERE r.bet_id = b.id) AS revisions,
       (SELECT COUNT(*) FROM votes v WHERE v.bet_id = b.id) AS votes,
       (SELECT COUNT(*) FROM votes v WHERE v.bet_id = b.id
          AND (v.user_id = ? OR v.anon_id = ?)) AS voted
FROM bets b JOIN users u ON u.id = b.user_id
"""

SORTS = {
    "interesting": "votes DESC, b.created_at DESC",
    "newest": "b.created_at DESC",
    "horizon": "b.horizon ASC, votes DESC",
    "oldest": "b.created_at ASC",
}


def list_bets(conn, viewer_id=0, query="", category="", status="", sort="interesting",
              viewer_anon="", struck=False):
    """The ledger. Struck entries are left out of it unless asked for by
    name, which only the moderation desk does."""
    sql = BET_SELECT
    where, args = [], [viewer_id or 0, viewer_anon or ""]
    if not struck:
        where.append("b.removed_at IS NULL")
    if query:
        where.append("(b.claim LIKE ? OR b.reasoning LIKE ? OR b.category LIKE ?)")
        like = "%%%s%%" % query
        args += [like, like, like]
    if category:
        # A bet is in a subject whether it is the main one or one of the
        # two that may sit beside it.
        where.append(
            "(b.category = ? OR EXISTS (SELECT 1 FROM bet_subjects s"
            "  WHERE s.bet_id = b.id AND s.subject = ?))"
        )
        args += [category, category]
    if status:
        where.append("b.status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SORTS.get(sort, SORTS["interesting"])
    return conn.execute(sql, args).fetchall()


def get_bet(conn, bet_id, viewer_id=0, viewer_anon="", struck=False):
    sql = BET_SELECT + " WHERE b.id = ?"
    if not struck:
        sql += " AND b.removed_at IS NULL"
    return conn.execute(sql, (viewer_id or 0, viewer_anon or "", bet_id)).fetchone()


def create_bet(conn, user_id, claim, reasoning, subjects, horizon, anonymous):
    """`subjects` is one to MAX_SUBJECTS of them; the first is the main one."""
    subjects = clean_subjects(
        subjects if isinstance(subjects, (list, tuple)) else [subjects]
    )[:MAX_SUBJECTS]   # the form refuses more; this is the last line of defence
    with conn:
        cur = conn.execute(
            """INSERT INTO bets (user_id, claim, reasoning, category, horizon, anonymous, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, claim, reasoning, subjects[0], horizon, 1 if anonymous else 0, stamp()),
        )
    set_subjects(conn, cur.lastrowid, subjects)
    return cur.lastrowid


def has_written(conn, user_id):
    """Has this hand ever written a bet? A struck one still counts - they
    have been here before, and the rules were put in front of them then."""
    row = conn.execute(
        "SELECT 1 FROM bets WHERE user_id = ? LIMIT 1", (user_id,)
    ).fetchone()
    return row is not None


def subject_slug(subject):
    """'law & rights' -> 'law-rights', for an address worth reading."""
    return "-".join(
        "".join(c for c in word if c.isalnum()) for word in subject.split()
    ).strip("-").replace("--", "-").lower()


SUBJECT_BY_SLUG = {subject_slug(c): c for c in CATEGORIES}


def clean_subjects(wanted):
    """Known subjects, no repeats, in the order they were given.

    Deliberately does not cap the count: too many is something to refuse
    and say so, not to silently trim - a notebook that quietly throws
    away the fourth thing you ticked has chosen for you."""
    kept = []
    for subject in wanted:
        subject = (subject or "").strip()
        if subject in CATEGORIES and subject not in kept:
            kept.append(subject)
    return kept


def set_subjects(conn, bet_id, subjects):
    """The first subject lives on the bet; the rest live beside it."""
    with conn:
        conn.execute("DELETE FROM bet_subjects WHERE bet_id = ?", (bet_id,))
        if subjects:
            conn.execute("UPDATE bets SET category = ? WHERE id = ?", (subjects[0], bet_id))
        for place, subject in enumerate(subjects[1:], start=1):
            conn.execute(
                "INSERT INTO bet_subjects (bet_id, subject, place) VALUES (?, ?, ?)",
                (bet_id, subject, place),
            )


def subjects_of(conn, bet_id):
    """Every subject a bet carries, the main one first."""
    row = conn.execute("SELECT category FROM bets WHERE id = ?", (bet_id,)).fetchone()
    if row is None:
        return []
    rest = conn.execute(
        "SELECT subject FROM bet_subjects WHERE bet_id = ? ORDER BY place, subject", (bet_id,)
    ).fetchall()
    return [row["category"]] + [r["subject"] for r in rest]


def subjects_on(bet):
    """The same, read off a row from BET_SELECT without asking again."""
    rest = (bet["extra_subjects"] or "").split("|") if bet["extra_subjects"] else []
    return [bet["category"]] + [s for s in rest if s]


def revise_bet(conn, bet_id, user_id, claim, reasoning, subjects, horizon):
    """Change a bet, keeping what it said before.

    The old wording is copied into bet_revisions first, so the page can
    show the whole history: a hand may correct itself, but not quietly.
    Returns False if nothing actually changed."""
    was = conn.execute(
        "SELECT * FROM bets WHERE id = ? AND user_id = ?", (bet_id, user_id)
    ).fetchone()
    if was is None:
        return False
    subjects = clean_subjects(
        subjects if isinstance(subjects, (list, tuple)) else [subjects]
    )[:MAX_SUBJECTS]   # the form refuses more; this is the last line of defence
    if not subjects:
        return False
    had = subjects_of(conn, bet_id)
    if (was["claim"], was["reasoning"], had, was["horizon"]) == (
        claim, reasoning, subjects, horizon
    ):
        return False
    with conn:
        conn.execute(
            """INSERT INTO bet_revisions
                 (bet_id, claim, reasoning, category, subjects, horizon, replaced_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (bet_id, was["claim"], was["reasoning"], was["category"], "|".join(had),
             was["horizon"], stamp()),
        )
        conn.execute(
            """UPDATE bets SET claim = ?, reasoning = ?, horizon = ?
               WHERE id = ? AND user_id = ?""",
            (claim, reasoning, horizon, bet_id, user_id),
        )
    set_subjects(conn, bet_id, subjects)
    return True


def revisions(conn, bet_id):
    """Every earlier wording, newest first."""
    return conn.execute(
        "SELECT * FROM bet_revisions WHERE bet_id = ? ORDER BY replaced_at DESC, id DESC",
        (bet_id,),
    ).fetchall()


def resolve_bet(conn, bet_id, user_id, status, verdict):
    with conn:
        conn.execute(
            """UPDATE bets SET status = ?, verdict = ?, resolved_at = ?
               WHERE id = ? AND user_id = ?""",
            (status, verdict, stamp() if status != "open" else None, bet_id, user_id),
        )


def toggle_vote(conn, bet_id, user_id=None, anon_id=None):
    """Exactly one of user_id / anon_id identifies the voter.

    Anonymous votes are not deduplicated beyond the cookie: anyone who
    clears cookies or opens another browser can vote again. That is a
    deliberate trade for letting people without an account weigh in at
    all - see README. What stops it being free is upstream, where a new
    anonymous hand is handed out (app.ANON_HANDS_PER_IP).
    """
    assert (user_id is None) != (anon_id is None)
    column, value = ("user_id", user_id) if user_id is not None else ("anon_id", anon_id)
    row = conn.execute(
        "SELECT 1 FROM votes WHERE bet_id = ? AND %s = ?" % column, (bet_id, value)
    ).fetchone()
    with conn:
        if row:
            conn.execute("DELETE FROM votes WHERE bet_id = ? AND %s = ?" % column, (bet_id, value))
        else:
            conn.execute(
                "INSERT INTO votes (bet_id, %s, created_at) VALUES (?, ?, ?)" % column,
                (bet_id, value, stamp()),
            )
    return not row


def category_counts(conn):
    """How many bets sit under each subject, counting a bet once for each
    subject it carries."""
    rows = conn.execute(
        """SELECT subject, COUNT(*) AS n FROM (
               SELECT category AS subject FROM bets WHERE removed_at IS NULL
               UNION ALL
               SELECT s.subject FROM bet_subjects s JOIN bets b ON b.id = s.bet_id
                WHERE b.removed_at IS NULL
           ) GROUP BY subject ORDER BY n DESC, subject"""
    ).fetchall()
    return [(r["subject"], r["n"]) for r in rows]


# --- the moderation desk --------------------------------------------------

def strike_bet(conn, bet_id, why):
    """Rule a line through an entry: gone from the ledger, still on the
    page. Reversible, because moderation is a judgement and judgements
    are sometimes wrong."""
    with conn:
        conn.execute(
            "UPDATE bets SET removed_at = ?, removed_why = ? WHERE id = ?",
            (stamp(), why[:500], bet_id),
        )


def restore_bet(conn, bet_id):
    with conn:
        conn.execute(
            "UPDATE bets SET removed_at = NULL, removed_why = '' WHERE id = ?", (bet_id,)
        )


def burn_bet(conn, bet_id):
    """Take the page out altogether. For the things that should not sit
    in the ledger at all; there is no getting this one back."""
    with conn:
        conn.execute("DELETE FROM votes WHERE bet_id = ?", (bet_id,))
        conn.execute("DELETE FROM bets WHERE id = ?", (bet_id,))


def strike_everything_by(conn, user_id, why):
    """One hand turned out to be a spammer: strike the lot in one go."""
    with conn:
        cur = conn.execute(
            """UPDATE bets SET removed_at = ?, removed_why = ?
               WHERE user_id = ? AND removed_at IS NULL""",
            (stamp(), why[:500], user_id),
        )
    return cur.rowcount


def people(conn):
    """Everyone, with what they have written, for the moderation desk."""
    return conn.execute(
        """SELECT u.*,
                  (SELECT COUNT(*) FROM bets b
                    WHERE b.user_id = u.id AND b.removed_at IS NULL) AS standing,
                  (SELECT COUNT(*) FROM bets b
                    WHERE b.user_id = u.id AND b.removed_at IS NOT NULL) AS struck
           FROM users u ORDER BY u.id DESC"""
    ).fetchall()


def tally(conn):
    row = conn.execute(
        """SELECT (SELECT COUNT(*) FROM bets WHERE removed_at IS NULL) AS bets,
                  (SELECT COUNT(*) FROM bets WHERE removed_at IS NOT NULL) AS struck,
                  (SELECT COUNT(*) FROM users) AS people,
                  (SELECT COUNT(*) FROM votes) AS votes"""
    ).fetchone()
    return row


def byline(bet):
    """Who to credit: the pen name, or nobody."""
    if bet["anonymous"] or not bet["author_show_pseudo"]:
        return "a hand that preferred not to sign"
    return bet["author_pseudo"]
