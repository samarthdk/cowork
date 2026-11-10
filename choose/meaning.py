"""What a hand signal means, depending on the question being asked.

The hand detector (choose/hands.py) only reports raw signals:
    RIGHT, LEFT, RIGHT_UP, RIGHT_DOWN, LEFT_UP, LEFT_DOWN
This file turns a signal into an answer. None means "no selection - ask again".

Grid squares: TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT.
"""

YES_NO = {
    "RIGHT": "YES", "RIGHT_UP": "YES", "LEFT_UP": "YES",
    "LEFT": "NO", "RIGHT_DOWN": "NO", "LEFT_DOWN": "NO",
}

GRID = {
    "LEFT_UP": "TOP_LEFT", "RIGHT_UP": "TOP_RIGHT",
    "LEFT_DOWN": "BOTTOM_LEFT", "RIGHT_DOWN": "BOTTOM_RIGHT",
    # Plain RIGHT / LEFT has no square: ask again.
}
SQUARES = ("TOP_LEFT", "TOP_RIGHT", "BOTTOM_LEFT", "BOTTOM_RIGHT")


def yes_no(signal: str | None) -> str | None:
    """'YES', 'NO', or None."""
    return YES_NO.get(signal)


def four_choice(signal: str | None) -> str | None:
    """A grid square, or None (plain RIGHT/LEFT = ask again)."""
    return GRID.get(signal)


def three_choice(signal: str | None, empty: str = "BOTTOM_RIGHT") -> str | None:
    """Like four_choice, with one square left empty. Choosing it = None."""
    if empty not in SQUARES:
        raise ValueError(f"empty must be one of {SQUARES}")
    square = GRID.get(signal)
    return None if square == empty else square


def interpret(signal: str | None, question: str, empty: str = "BOTTOM_RIGHT") -> str | None:
    """question: 'yes_no', 'four' or 'three'."""
    if question == "yes_no":
        return yes_no(signal)
    if question == "four":
        return four_choice(signal)
    if question == "three":
        return three_choice(signal, empty)
    raise ValueError("question must be 'yes_no', 'four' or 'three'")


if __name__ == "__main__":
    # Run on its own: .venv/bin/python -m choose.meaning
    print(f"{'signal':12} {'yes/no':8} {'four-choice':14} three-choice (bottom right empty)")
    for s in ("RIGHT", "LEFT", "RIGHT_UP", "RIGHT_DOWN", "LEFT_UP", "LEFT_DOWN"):
        show = lambda v: v or "ask again"  # noqa: E731
        print(f"{s:12} {show(yes_no(s)):8} {show(four_choice(s)):14} {show(three_choice(s))}")
