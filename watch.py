"""Words the notebook keeps an eye on.

Two different questions, and the notebook answers them differently.

A pen name is a label somebody chose to wear, and no pen name needs a
slur in it. Those are refused at the desk, and the refusal says only that
the name will not do. The matching there is deliberately blunt: the usual
dodges - leet spelling, dots or dashes between the letters, a number on
the end - are flattened out first. A false positive costs a visitor
nothing, because the next name is free and the notebook has already told
them to pick a stupid one.

A bet is the other question entirely. "By 2031, a party the press calls
neo-nazi will lead a national poll in western Europe" is a bet about the
world, and the house rules are explicit that nothing is struck for being
unwelcome or probably wrong. So nothing here refuses a bet, ever. What it
does is put the entry in front of the keeper, who reads it and decides.
Matching there is whole words only: a filter that cannot tell a claim
about a thing from an endorsement of it has no business holding the pen.

The list is short and kept by hand. Longer ones exist and can be pasted
in - but an imported list is mostly false positives in a place where the
keeper reads every entry anyway, and the entries it quietly refuses are
the ones nobody ever sees. What is here is the writing that cannot arrive
innocently: hate slogans, the numbers that stand in for them, and slurs
whose only use is the one.
"""

import re

# Long enough that a chance collision inside an ordinary word is not a
# worry, so these are looked for anywhere in a pen name once it has been
# flattened out - kkk88, n1gger, f-a-g-g-o-t and so on.
ANYWHERE = (
    "heil hitler",
    "sieg heil",
    "white power",
    "blood and soil",
    "gas the jews",
    "race war",
    "lynch them",
    "nigger",
    "faggot",
    "shemale",
    "tranny",
    "towelhead",
    "wetback",
    "beaner",
    "kike",
    "raghead",
    "retarded",
    "subhuman",
    "untermensch",
    "groyper",
)

# Short, or a piece of some perfectly ordinary word: paki is in Pakistan,
# spic is in suspicion, coon is in raccoon, nazi is in every honest
# sentence about the 1930s. Whole words only, in a name as in a bet.
WHOLE = (
    "nazi",
    "nazis",
    "paki",
    "spic",
    "chink",
    "gook",
    "coon",
    "kkk",
    "fag",
    "fags",
    "tard",
    "retard",
    "hitler",
)

# Numbers and shapes that mean something only to the people using them.
# A bare 88 is a year here and a percentage everywhere else, so it is
# never on its own.
CODES = (
    ("1488", r"\b1488\b"),
    ("14/88", r"\b14\s*/\s*88\b"),
    ("14 words", r"\b14\s+words\b"),
    ("88hh", r"\b88\s*hh\b|\bhh\s*88\b"),
    ("white genocide", r"\bwhite\s+genocide\b"),
    ("final solution", r"\bfinal\s+solution\b"),
)

# The shapes a word is hidden in. Numbers first, then everything that is
# not a letter comes out - which is what turns "n.1-gg3r_88" into the
# thing it was always going to be.
DISGUISES = (
    ("4", "a"), ("@", "a"), ("8", "b"), ("3", "e"), ("1", "i"), ("!", "i"),
    ("0", "o"), ("5", "s"), ("$", "s"), ("7", "t"), ("+", "t"), ("2", "z"),
)


def flatten(text):
    """A string with the usual ways of hiding a word taken out of it."""
    folded = (text or "").lower()
    for shape, letter in DISGUISES:
        folded = folded.replace(shape, letter)
    return re.sub(r"[^a-z]+", "", folded)


def _whole_word(term, text):
    """`term` as a whole word in `text`, allowing any spacing between the
    words of a term that has more than one."""
    pattern = r"\b%s\b" % r"\s+".join(re.escape(word) for word in term.split())
    return bool(re.search(pattern, text))


def trips(*parts):
    """Every watched term a piece of writing uses, as whole words.

    For bets, where this flags and never refuses. Empty means there is
    nothing here for the keeper to look at twice."""
    text = " ".join(part or "" for part in parts).lower()
    found = [term for term in ANYWHERE + WHOLE if _whole_word(term, text)]
    found += [label for label, code in CODES if re.search(code, text)]
    # In the order they are listed, without repeats, so two spellings of
    # one thing do not read as two findings.
    return sorted(set(found), key=found.index)


def name_trouble(name):
    """Why a pen name will not do, or None if it will.

    Blunt on purpose - see the note at the top of this file. The reason
    given back is the same whatever was found: a refusal that names the
    word is a refusal that teaches the way around itself."""
    # An underscore is a word character to a regular expression and a
    # space to a reader; the reader is right about what a name says.
    plain = re.sub(r"_+", " ", (name or "").lower())
    folded = flatten(name)
    if not folded:
        return None
    for term in ANYWHERE:
        if flatten(term) in folded:
            return REFUSAL
    for term in WHOLE:
        if _whole_word(term, plain) or folded == flatten(term):
            return REFUSAL
    for _, code in CODES:
        # Loosened for a name: 1488 is a whole word in a sentence and a
        # piece of "1488er" in a handle, and the handle means it too.
        loose = code.replace(r"\b", "")
        if re.search(loose, plain) or re.search(loose, plain.replace(" ", "")):
            return REFUSAL
    return None


REFUSAL = (
    "That name will not do. Pick another - nothing here checks it against "
    "anything, so it can be as stupid as you like, but not that."
)
