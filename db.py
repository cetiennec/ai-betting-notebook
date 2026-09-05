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
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS votes (
    bet_id     INTEGER NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (bet_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_bets_category ON bets(category);
CREATE INDEX IF NOT EXISTS idx_votes_bet ON votes(bet_id);
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


def init():
    conn = connect()
    with conn:
        conn.executescript(SCHEMA)
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
       (SELECT COUNT(*) FROM votes v WHERE v.bet_id = b.id) AS votes,
       (SELECT COUNT(*) FROM votes v WHERE v.bet_id = b.id AND v.user_id = ?) AS voted
FROM bets b JOIN users u ON u.id = b.user_id
"""

SORTS = {
    "interesting": "votes DESC, b.created_at DESC",
    "newest": "b.created_at DESC",
    "horizon": "b.horizon ASC, votes DESC",
    "oldest": "b.created_at ASC",
}


def list_bets(conn, viewer_id=0, query="", category="", status="", sort="interesting"):
    sql = BET_SELECT
    where, args = [], [viewer_id or 0]
    if query:
        where.append("(b.claim LIKE ? OR b.reasoning LIKE ? OR b.category LIKE ?)")
        like = "%%%s%%" % query
        args += [like, like, like]
    if category:
        where.append("b.category = ?")
        args.append(category)
    if status:
        where.append("b.status = ?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + SORTS.get(sort, SORTS["interesting"])
    return conn.execute(sql, args).fetchall()


def get_bet(conn, bet_id, viewer_id=0):
    return conn.execute(BET_SELECT + " WHERE b.id = ?", (viewer_id or 0, bet_id)).fetchone()


def create_bet(conn, user_id, claim, reasoning, category, horizon, anonymous):
    with conn:
        cur = conn.execute(
            """INSERT INTO bets (user_id, claim, reasoning, category, horizon, anonymous, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, claim, reasoning, category, horizon, 1 if anonymous else 0, stamp()),
        )
    return cur.lastrowid


def resolve_bet(conn, bet_id, user_id, status, verdict):
    with conn:
        conn.execute(
            """UPDATE bets SET status = ?, verdict = ?, resolved_at = ?
               WHERE id = ? AND user_id = ?""",
            (status, verdict, stamp() if status != "open" else None, bet_id, user_id),
        )


def toggle_vote(conn, bet_id, user_id):
    row = conn.execute(
        "SELECT 1 FROM votes WHERE bet_id = ? AND user_id = ?", (bet_id, user_id)
    ).fetchone()
    with conn:
        if row:
            conn.execute("DELETE FROM votes WHERE bet_id = ? AND user_id = ?", (bet_id, user_id))
        else:
            conn.execute(
                "INSERT INTO votes (bet_id, user_id, created_at) VALUES (?, ?, ?)",
                (bet_id, user_id, stamp()),
            )
    return not row


def category_counts(conn):
    rows = conn.execute(
        "SELECT category, COUNT(*) AS n FROM bets GROUP BY category ORDER BY n DESC, category"
    ).fetchall()
    return [(r["category"], r["n"]) for r in rows]


def tally(conn):
    row = conn.execute(
        """SELECT (SELECT COUNT(*) FROM bets) AS bets,
                  (SELECT COUNT(*) FROM users) AS people,
                  (SELECT COUNT(*) FROM votes) AS votes"""
    ).fetchone()
    return row


def byline(bet):
    """Who to credit: the pen name, or nobody."""
    if bet["anonymous"] or not bet["author_show_pseudo"]:
        return "a hand that preferred not to sign"
    return bet["author_pseudo"]
