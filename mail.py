"""Letters.

In the prototype nothing leaves the machine: every letter is dropped into
data/outbox/ as a plain text file and echoed to the console. Set SMTP_HOST
(and optionally SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM) to post them for
real instead.
"""

import os
import re
import smtplib
from email.message import EmailMessage
from datetime import timedelta

import db

OUTBOX = os.path.join(db.DATA_DIR, "outbox")
SENDER = os.environ.get("SMTP_FROM", "notebook@future-with-ai.local")


def looks_like_email(value):
    return bool(re.match(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$", (value or "").strip()))


def send(to, subject, body):
    os.makedirs(OUTBOX, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", ("%s-%s" % (to, subject)).lower())[:60]
    path = os.path.join(OUTBOX, "%s-%s.txt" % (db.now().strftime("%Y%m%d-%H%M%S"), slug))
    letter = "To: %s\nFrom: %s\nSubject: %s\n\n%s\n" % (to, SENDER, subject, body)
    with open(path, "w") as fh:
        fh.write(letter)

    host = os.environ.get("SMTP_HOST")
    if host:
        msg = EmailMessage()
        msg["To"], msg["From"], msg["Subject"] = to, SENDER, subject
        msg.set_content(body)
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as smtp:
            smtp.starttls()
            user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)
        print("[mail] sent to %s via %s  (copy: %s)" % (to, host, path))
    else:
        print("\n" + "-" * 68)
        print(letter.rstrip())
        print("-" * 68)
        print("[mail] written to %s\n" % path)
    return path


def send_login_link(email, url):
    send(
        email,
        "Your key to the notebook",
        "Somebody (you, we hope) asked to open the Future with AI betting\n"
        "notebook with this address.\n\n"
        "    %s\n\n"
        "The link works once, and only for the next hour.\n\n"
        "If it was not you, ignore this letter; nothing has been opened." % url,
    )


# --- the once-a-year letter ----------------------------------------------

def letter_is_due(user, force=False):
    if not user["yearly_letter"]:
        return False
    if force:
        return True
    last = db.parse(user["last_letter_at"]) or db.parse(user["created_at"])
    if last is None:
        return True
    return db.now() - last >= timedelta(days=365)


def compose_yearly(conn, user, base_url):
    year = db.now().year
    mine = conn.execute(
        "SELECT * FROM bets WHERE user_id = ? ORDER BY horizon", (user["id"],)
    ).fetchall()
    watched = conn.execute(
        """SELECT b.* FROM bets b JOIN votes v ON v.bet_id = b.id
           WHERE v.user_id = ? AND b.user_id <> ? ORDER BY b.horizon""",
        (user["id"], user["id"]),
    ).fetchall()
    ripe = [b for b in list(mine) + list(watched) if b["horizon"] <= year and b["status"] == "open"]

    lines = [
        "A year has gone by, and the notebook would like a word.",
        "",
        "It is %d. Here is what you wrote down, and what you thought worth"
        % year,
        "watching. Read it before you look at the news, if you can.",
        "",
    ]

    if ripe:
        lines += ["THE ONES THAT HAVE COME DUE", ""]
        for b in ripe:
            lines += ["  * %s" % b["claim"], "    by %d - %s/bet/%d" % (b["horizon"], base_url, b["id"]), ""]
        lines += ["Say how they turned out on the page for each bet.", ""]

    if mine:
        lines += ["YOUR OWN BETS", ""]
        for b in mine:
            lines += [
                "  * %s" % b["claim"],
                "    %s, by %d - %s" % (b["category"], b["horizon"], db.STATUSES[b["status"]]),
                "",
            ]

    if watched:
        lines += ["BETS YOU FOUND INTERESTING", ""]
        for b in watched:
            lines += ["  * %s (by %d)" % (b["claim"], b["horizon"]), ""]

    lines += [
        "The whole notebook is still at %s" % base_url,
        "",
        "To stop these yearly letters, visit %s/desk and untick the box." % base_url,
    ]
    return "\n".join(lines)


def send_yearly(conn, base_url, force=False, only_email=None):
    """Post the annual letter to everyone it is due for. Returns how many went."""
    users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    sent = 0
    for user in users:
        if only_email and user["email"] != only_email.lower().strip():
            continue
        if not letter_is_due(user, force):
            continue
        send(
            user["email"],
            "The notebook, one year on (%d)" % db.now().year,
            compose_yearly(conn, user, base_url),
        )
        with conn:
            conn.execute(
                "UPDATE users SET last_letter_at = ? WHERE id = ?", (db.stamp(), user["id"])
            )
        sent += 1
    return sent
