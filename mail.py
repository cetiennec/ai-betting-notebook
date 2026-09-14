"""Letters.

In the prototype nothing leaves the machine: every letter is dropped into
data/outbox/ as a plain text file and echoed to the console. Set SMTP_HOST
(and optionally SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM) to post them for
real instead.

A letter from here is somebody's way in - a key that lands in a junk
folder is a person who asked to join and silently could not. Most of what
decides that is DNS and not code (see the README), but the part that is
code is here: a letter with a Date, a Message-ID of its own, a name on
the From line and a word about what kind of letter it is. Mail without
those reads as machinery, and filters treat it accordingly.
"""

import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from datetime import timedelta

import db

OUTBOX = os.path.join(db.DATA_DIR, "outbox")
SENDER = os.environ.get("SMTP_FROM", "notebook@future-with-ai.local")
# The name on the From line. A letter from a person-shaped sender is read
# as a letter; one from a bare address is read as a machine.
SENDER_NAME = os.environ.get("SMTP_FROM_NAME", "The Future with AI notebook")
SENDER_DOMAIN = SENDER.rsplit("@", 1)[-1] if "@" in SENDER else "future-with-ai.local"


def warn_the_keepers(bet_id, claim, words, where):
    """Tell whoever keeps the ledger that an entry is worth a second read.

    Not a refusal and not a strike: the notebook has written the bet down
    as it always does, and this is the note that says a person should look
    at it. If nobody is named in NOTEBOOK_KEEPERS there is nobody to tell,
    and the entry still waits on the desk at /keep."""
    if not db.KEEPERS:
        return 0
    body = (
        "Entry %d was written just now, and its wording is on the list the\n"
        "notebook watches:\n\n"
        "    %s\n\n"
        "What it says:\n\n"
        "    %s\n\n"
        "Nothing has been done to it. A bet is not struck for being unwelcome\n"
        "or probably wrong - this is only a note that somebody should read it.\n"
        "It is at %s, and the desk is at %s/keep.\n"
    ) % (bet_id, ", ".join(words), claim, where, where.rsplit("/bet/", 1)[0])
    sent = 0
    for address in sorted(db.KEEPERS):
        if send(address, "Worth a look: entry %d" % bet_id, body):
            sent += 1
    return sent


def looks_like_email(value):
    return bool(re.match(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$", (value or "").strip()))


def using_real_smtp():
    return bool(os.environ.get("SMTP_HOST"))


def letter(to, subject, body, headers=None):
    """The letter itself, headers and all.

    Date and Message-ID are not optional furniture: a message without
    them is one a filter has every reason to distrust, and neither
    smtplib nor the SMTP server is obliged to add them."""
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = formataddr((SENDER_NAME, SENDER))
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=SENDER_DOMAIN)
    # Says plainly that a machine wrote this because a person asked it to,
    # which is what keeps it out of an auto-responder's way.
    msg["Auto-Submitted"] = "auto-generated"
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(body)
    return msg


def send(to, subject, body, headers=None):
    """Write the letter to the outbox, then try to post it for real.

    Returns True if a real send succeeded (or none was configured - the
    outbox stands in for it), False if SMTP was configured but failed.
    Callers must not crash a request over a mail provider being down.
    """
    os.makedirs(OUTBOX, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", ("%s-%s" % (to, subject)).lower())[:60]
    path = os.path.join(OUTBOX, "%s-%s.txt" % (db.now().strftime("%Y%m%d-%H%M%S"), slug))
    msg = letter(to, subject, body, headers)
    with open(path, "w") as fh:
        fh.write(str(msg))

    host = os.environ.get("SMTP_HOST")
    if not host:
        print("\n" + "-" * 68)
        print(str(msg).rstrip())
        print("-" * 68)
        print("[mail] written to %s\n" % path)
        return True

    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as smtp:
            smtp.starttls()
            user, password = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
            if user:
                smtp.login(user, password or "")
            smtp.send_message(msg)
        print("[mail] sent to %s via %s  (copy: %s)" % (to, host, path))
        return True
    except (smtplib.SMTPException, OSError) as exc:
        print("[mail] FAILED to send to %s via %s: %s  (copy kept: %s)" % (to, host, exc, path),
              file=sys.stderr)
        return False


def send_login_link(email, url):
    """The key. Transactional, asked for a moment ago, and the one letter
    here that somebody is actively waiting on - so it says what it is in
    the subject line and gets to the link in two lines."""
    return send(
        email,
        "Your key to the Future with AI betting notebook",
        "Somebody - you, we hope - asked to open the Future with AI betting\n"
        "notebook with this address. Here is the key:\n\n"
        "    %s\n\n"
        "It works once, and only for the next hour.\n\n"
        "If it was not you, ignore this letter. Nothing has been opened, and\n"
        "the address is not on any list: it is kept to post a key when you\n"
        "ask for one, and the yearly letter if you ask for that." % url,
    )


# --- the once-a-year letter ----------------------------------------------

def check(address, base_url):
    """Post one test letter and say what went out with it.

    A key in a junk folder is somebody who asked to join and silently
    could not, and nothing in the notebook can see that happen. This is
    the one way to look."""
    host = os.environ.get("SMTP_HOST")
    print("\n  From        %s" % formataddr((SENDER_NAME, SENDER)))
    print("  Message-ID  <...@%s>" % SENDER_DOMAIN)
    print("  SMTP        %s" % (
        "%s:%s%s" % (host, os.environ.get("SMTP_PORT", 587),
                     " as %s" % os.environ["SMTP_USER"] if os.environ.get("SMTP_USER") else "")
        if host else "not set - nothing will leave this machine"))
    print("  Notebook    %s" % base_url)

    site = base_url.split("//")[-1].split("/")[0].split(":")[0]
    if host and SENDER_DOMAIN not in (site, site.split(".", 1)[-1]):
        print("\n  ! The From address is at %s and the notebook answers at %s."
              % (SENDER_DOMAIN, site))
        print("    A key that comes from somewhere other than the place it opens")
        print("    is the shape of a phishing letter, and is filtered like one.")

    sent = send(
        address,
        "A test letter from the Future with AI betting notebook",
        "Nothing is wrong. Somebody keeping the notebook asked it to post one\n"
        "letter, to see where it lands and what it looks like when it gets there.\n\n"
        "If this is in a junk folder, the notebook's letters are being filtered\n"
        "and the sign-in keys are too: see the README, under the mail that has to\n"
        "arrive. If it is in the inbox, the keys should be as well.\n",
    )
    print("\n  %s\n" % ("posted" if sent else "FAILED - see above"))
    return sent


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
