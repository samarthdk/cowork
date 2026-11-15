"""Dictation polish: spoken punctuation, editing words, and numbers, times and dates written sensibly.

    tidy("see you at three thirty pm period")      -> ("See you at 3:30 PM.", True)   True: numbers were written
    tidy("hello comma how are you question mark")  -> ("Hello, how are you?", False)
    editing("delete that")  -> "delete"      editing("period") -> "."      editing("open mail") -> None

When words were turned into numbers (the second value), Cowork shows the result and asks before typing:
"three thirty" could have been meant as words.
"""

import re

MARKS = {"period": ".", "full stop": ".", "comma": ",", "question mark": "?", "exclamation mark": "!",
         "exclamation point": "!", "colon": ":", "semicolon": ";"}
BREAKS = {"new line": "\n", "next line": "\n", "new paragraph": "\n\n", "next paragraph": "\n\n"}
DELETE = ("delete that", "scratch that", "delete the last sentence", "remove that", "undo typing")
ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
        "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
            "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
            "twentieth": 20, "thirtieth": 30}
MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december")


def _plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z ]", " ", (text or "").lower()).split())


def editing(text: str) -> str | None:
    """A whole request that only edits the dictation: "delete" (remove the last dictated sentence),
    a punctuation mark, or a line break. None for anything else."""
    said = _plain(text)
    if said in DELETE:
        return "delete"
    if said in MARKS:
        return MARKS[said]
    if said in BREAKS:
        return BREAKS[said]
    return None


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _numbers(text: str) -> tuple[str, bool]:
    """Number words written as digits where it reads better: dates, times, and numbers above ten."""
    changed = False

    def ordinal_value(word: str) -> int | None:
        if word in ORDINALS:
            return ORDINALS[word]
        for tens, value in TENS.items():   # "twenty first"
            if word.startswith(tens + " ") and word.split(" ", 1)[1] in ORDINALS:
                return value + ORDINALS[word.split(" ", 1)[1]]
        return None

    # dates: "november fifteenth", "the twenty first of march"
    month = "|".join(MONTHS)
    ordinal_words = "|".join(sorted(list(ORDINALS) + [f"{t} {o}" for t in TENS for o in ORDINALS if ORDINALS[o] < 10],
                                    key=len, reverse=True))

    def month_first(match):
        nonlocal changed
        value = ordinal_value(match.group(2).lower())
        if value is None:
            return match.group(0)
        changed = True
        return f"{match.group(1).capitalize()} {_ordinal(value)}"

    text = re.sub(rf"\b({month})\s+(?:the\s+)?({ordinal_words})\b", month_first, text, flags=re.IGNORECASE)

    def day_first(match):
        nonlocal changed
        value = ordinal_value(match.group(1).lower())
        if value is None:
            return match.group(0)
        changed = True
        return f"{match.group(2).capitalize()} {_ordinal(value)}"

    text = re.sub(rf"\bthe\s+({ordinal_words})\s+of\s+({month})\b", day_first, text, flags=re.IGNORECASE)

    # times: "three thirty pm", "ten o'clock", "seven fifteen in the morning"
    hours = "|".join(list(ONES)[1:13])
    minutes = "|".join(sorted([f"{t} {o}" for t in ("twenty", "thirty", "forty", "fifty") for o in list(ONES)[1:10]]
                              + list(TENS)[:4] + ["fifteen", "ten", "oh five", "o five"], key=len, reverse=True))

    def minute_value(words: str) -> int:
        words = words.lower().replace("oh ", "").replace("o ", "")
        parts = words.split()
        return TENS.get(parts[0], ONES.get(parts[0], 0)) + (ONES.get(parts[1], 0) if len(parts) > 1 else 0)

    def clock(match):
        nonlocal changed
        hour = ONES[match.group(1).lower()]
        minute = minute_value(match.group(2)) if match.group(2) else 0
        half = (match.group(3) or "").lower().replace(".", "").replace(" ", "")
        suffix = " AM" if half in ("am", "inthemorning") else " PM" if half in ("pm", "intheafternoon", "intheevening") else ""
        if not match.group(2) and not suffix and not match.group(4):
            return match.group(0)   # "three" alone stays a word
        changed = True
        return f"{hour}:{minute:02d}{suffix}"

    text = re.sub(rf"\b({hours})(?:\s+({minutes}))?(?:\s+(a\.?\s?m\.?|p\.?\s?m\.?|in the morning|in the afternoon|in the evening))?(\s+o'?\s?clock)?\b",
                  clock, text, flags=re.IGNORECASE)

    # numbers above ten: "twenty five" -> 25, "one hundred" -> 100
    def number(match):
        nonlocal changed
        words = match.group(0).lower().split()
        total, current = 0, 0
        for word in words:
            if word in ONES:
                current += ONES[word]
            elif word in TENS:
                current += TENS[word]
            elif word == "hundred":
                current = max(current, 1) * 100
            elif word == "thousand":
                total += max(current, 1) * 1000
                current = 0
        value = total + current
        if value <= 10:
            return match.group(0)
        changed = True
        return str(value)

    words = "|".join(sorted(list(ONES) + list(TENS) + ["hundred", "thousand"], key=len, reverse=True))
    text = re.sub(rf"\b(?:{words})(?:\s+(?:and\s+)?(?:{words}))*\b", number, text, flags=re.IGNORECASE)
    return text, changed


def tidy(text: str, numbers: bool = True) -> tuple[str, bool]:
    """Dictated words ready to type: spoken punctuation and line breaks as marks, numbers/times/dates
    written sensibly (numbers=False: left as words), a capital at the start of each sentence.
    (text, numbers_were_written)."""
    # Email addresses, web addresses and file names are typed exactly as they are (never "Sam@gmail. Com").
    kept = []

    def keep(match):
        kept.append(match.group(0))
        return f" \x00{len(kept) - 1}\x00 "

    out = " " + re.sub(r"\S+@\S+\.\w+|\b\w+://\S+|\bwww\.\S+|\b[\w-]+(?:\.[\w-]+)+\b(?<!\.)(?=\s|$)",
                       lambda m: keep(m) if ("@" in m.group(0) or "://" in m.group(0) or not m.group(0)[0].isdigit()
                                            and re.search(r"\.[a-z]{2,}$", m.group(0).lower())) else m.group(0),
                       (text or "").strip()) + " "
    for spoken, mark in sorted(MARKS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"[\s,.]*\b{spoken}\b[\s,.]*", f"{mark} ", out, flags=re.IGNORECASE)
    for spoken, mark in sorted(BREAKS.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf"[\s,]*\b{spoken}\b[\s,.]*", mark, out, flags=re.IGNORECASE)
    out, changed = _numbers(out) if numbers else (out, False)
    out = re.sub(r"[ \t]+([,.?!:;])", r"\1", out)          # no space before a mark
    out = re.sub(r"([,.?!:;])(?=[A-Za-z])", r"\1 ", out)    # one space after it
    out = re.sub(r"[ \t]{2,}", " ", out).strip(" \t")
    out = re.sub(r"(^|[.?!]\s+|\n)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    out = re.sub(r"\s?\x00(\d+)\x00\s?", lambda m: " " + kept[int(m.group(1))] + " ", out)
    out = re.sub(r" +([,.?!:;])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip(" \t")
    return out, changed
