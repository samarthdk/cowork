"""Second check before trusting "high" confidence.

Problem it fixes: after learning "aim thur stee cud eye haf sum wah tur" -> water,
the model said HIGH -> water for "cud eye haf sum pep see", because most words
matched the learned example. Partial word overlap must not be enough.

How it works: the heard words are lined up, in order, with the words of the
chosen phrase and of each learned example for it (near-identical words only).
Then the parts that don't line up are looked at:
  - a part that was swapped ("pep see" where the example has "wah tur") must be
    similar to what it replaced, otherwise it CONTRADICTS the choice;
  - extra heard words the example doesn't have: one short word is fine
    ("... pleez"), more is unexplained and CONTRADICTS;
  - words of the example that weren't said (dropped) are fine.
If at least one text fully SUPPORTS the choice, it stands. Otherwise, if one
CONTRADICTS it, confidence is lowered to "low" (when unsure, ask). If nothing
lines up at all, this check stays out of it and the model's answer stands.
"""

from rapidfuzz import fuzz

from repair.profile import find_command
from repair.store import normalize

WORD_MATCH = 85        # two words count as the same word at this similarity
PART_SIMILAR = 60      # a swapped part is "similar" at this similarity
MIN_SHARED_WORDS = 2   # fewer shared words than this: unrelated, no opinion
MAX_EXTRA_WORDS = 1    # extra heard words allowed without explanation


def _align(heard: list[str], other: list[str]) -> list[tuple[int, int]]:
    """Longest in-order matching of words (like a diff). Returns index pairs."""
    n, m = len(heard), len(other)
    best = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            same = fuzz.ratio(heard[i], other[j]) >= WORD_MATCH
            best[i][j] = max(best[i + 1][j], best[i][j + 1], best[i + 1][j + 1] + 1 if same else 0)
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if fuzz.ratio(heard[i], other[j]) >= WORD_MATCH and best[i][j] == best[i + 1][j + 1] + 1:
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def compare(heard: str, text: str) -> tuple[str, str]:
    """('supports' | 'contradicts' | 'unrelated', reason) for one text."""
    h, o = normalize(heard).split(), normalize(text).split()
    pairs = _align(h, o)
    if len(pairs) < MIN_SHARED_WORDS:
        return "unrelated", ""
    # The gaps between lined-up words, including before the first and after the last.
    anchors = [(-1, -1)] + pairs + [(len(h), len(o))]
    extra, problems = 0, []
    for (hi, oi), (hj, oj) in zip(anchors, anchors[1:]):
        h_gap, o_gap = h[hi + 1:hj], o[oi + 1:oj]
        if h_gap and o_gap:  # swapped part
            if fuzz.ratio(" ".join(h_gap), " ".join(o_gap)) < PART_SIMILAR:
                problems.append(f"'{' '.join(h_gap)}' instead of '{' '.join(o_gap)}'")
        elif h_gap:          # extra words
            extra += len(h_gap)
    if extra > MAX_EXTRA_WORDS:
        problems.append(f"{extra} extra words")
    if problems:
        return "contradicts", f"only partly matches '{text}': " + ", ".join(problems)
    return "supports", ""


def check(heard: str, choice: str, profile: dict) -> tuple[bool, str]:
    """(ok to keep high confidence, reason if not)."""
    # A phrase is compared with its own words and its learned examples. A command
    # is compared only with learned examples: nobody says "reply_email", and the
    # other words they say are the details (which app, what message).
    texts = [] if find_command(profile, choice) else [choice]
    texts += [e["heard"] for e in profile.get("examples", []) if e["meant"] == choice]
    reasons = []
    for text in texts:
        verdict, reason = compare(heard, text)
        if verdict == "supports":
            return True, ""
        if verdict == "contradicts":
            reasons.append(reason)
    if reasons:
        return False, reasons[0]
    return True, ""


# ---- Evidence: how well do the heard words back up a choice? ----------------

WORD_SIMILAR = 75   # two words "sound alike" at this similarity (looser than WORD_MATCH)


def _coverage(heard_words: list[str], text_words: list[str]) -> tuple[float, float]:
    """(share of the heard words found in the text, share of the text's words
    that were heard), both weighted by word length so that short filler words
    count for little. Each text word can be used once."""
    if not heard_words or not text_words:
        return 0.0, 0.0
    free = list(text_words)
    heard_hit = text_hit = 0
    for word in sorted(heard_words, key=len, reverse=True):
        best = max(free, key=lambda t: fuzz.ratio(word, t), default=None)
        if best is not None and fuzz.ratio(word, best) >= WORD_SIMILAR:
            heard_hit += len(word)
            text_hit += len(best)
            free.remove(best)
    return heard_hit / sum(map(len, heard_words)), text_hit / sum(map(len, text_words))


def support_texts(choice: str, profile: dict) -> list[str]:
    """What a choice 'sounds like': a phrase's own words; for a command, the
    words the person uses for it ("said_as" in the profile, otherwise the
    command's name); and for both, everything this person was heard saying
    when they meant it."""
    command = find_command(profile, choice)
    texts = (list(command.get("said_as", [])) or [choice.replace("_", " ")]) if command else [choice]
    if command and command.get("echo"):
        texts.append("")   # its detail is the person's own words (a question, words to type): they count alone
    return texts + [e["heard"] for e in profile.get("examples", []) if e["meant"] == choice]


def evidence(heard: str, choice: str, profile: dict, args: dict | None = None) -> float:
    """0..1: the best agreement between what was heard and any text for this
    choice. 1 = every heard word is in the text and every word of the text was
    heard; 0 = nothing in common."""
    heard_words = normalize(heard).split()
    extra = normalize(" ".join(v for v in (args or {}).values() if v)).split()
    best = 0.0
    for text in support_texts(choice, profile):
        heard_cov, text_cov = _coverage(heard_words, normalize(text).split() + extra)
        best = max(best, min(heard_cov, text_cov))
    return best
