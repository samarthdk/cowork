"""Spoken email addresses -> real ones.

    email_address("sam at gmail dot com")           -> "sam@gmail.com"
    email_address("s a m at gmail dot com")         -> "sam@gmail.com"
    email_address("sam dot lee at cornell dot edu") -> "sam.lee@cornell.edu"
    email_address("Sam@gmail.com.")                 -> "sam@gmail.com"
    email_address("open mail")                      -> None

Addresses are easy to mishear, so Cowork always asks "Did you mean ...?" before
typing one (ui/app.py).
"""

import re

SYMBOLS = {"at": "@", "dot": ".", "period": ".", "point": ".", "underscore": "_", "dash": "-", "hyphen": "-",
           "minus": "-", "plus": "+"}
NUMBERS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
           "seven": "7", "eight": "8", "nine": "9"}
JOINED = {"g mail": "gmail", "hot mail": "hotmail", "out look": "outlook", "i cloud": "icloud", "ya hoo": "yahoo"}
FILLER = {"to", "is", "it's", "its", "the", "address", "email", "my", "send", "it", "please", "and", "type"}
ADDRESS = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")


def email_address(text: str) -> str | None:
    said = (text or "").lower().strip()
    written = re.search(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}", said)
    if written:   # the recognizer already wrote it as an address
        address = written.group(0).rstrip(".")
        return address if ADDRESS.match(address) else None
    for spaced, joined in JOINED.items():
        said = said.replace(spaced, joined)
    tokens = re.sub(r"[^a-z0-9.@_+-]+", " ", said).split()
    tokens = [t.strip(".") for t in tokens if t.strip(".")]
    if "at" not in tokens:
        return None
    where = len(tokens) - 1 - tokens[::-1].index("at")   # the last "at": the one before the domain
    before, after = tokens[:where], tokens[where + 1:]
    # The name before "at": spelled letters/numbers and "dot"/"underscore" joiners, back to the first other word.
    name = []
    for token in reversed(before):
        if token in FILLER and not name:
            continue
        if token in FILLER:
            break
        if name and len(token) > 1 and token not in SYMBOLS and token not in NUMBERS and name[0] not in SYMBOLS:
            break   # an ordinary word before the name ("email sam at ..."): not part of it
        name.insert(0, token)
    domain = [t for t in after if t not in FILLER]
    if not name or not domain or not any(t in ("dot", "period", "point") or "." in t for t in domain):
        return None
    address = "".join(SYMBOLS.get(t, NUMBERS.get(t, t)) for t in name) + "@" + \
              "".join(SYMBOLS.get(t, NUMBERS.get(t, t)) for t in domain)
    return address if ADDRESS.match(address) else None
