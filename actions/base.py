"""The actions interface: how Second Voice does things on the Mac.

    actions.context()            -> what really exists right now, e.g.
                                    {"app": ["Safari", "Mail", ...], "target": ["Send", "Reply", ...]}
                                    The Brain can only choose from these lists.
    actions.run(command, args)   -> ActionResult (never raises for ordinary failures)
    actions.permissions()        -> what macOS has and has not allowed, with how to fix it

Safe or risky is decided in the profile (see repair/profile.py is_risky), not here:
risky commands are always confirmed with the person before run() is called.
"""

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass
class ActionResult:
    ok: bool
    say: str = ""                 # something to read aloud afterwards (e.g. the email)
    detail: str = ""              # what happened, for the screen
    problem: str = ""             # why it failed, in plain words
    undo: Callable | None = None  # best-effort way to take it back (used by the correction window)


@dataclass
class Permission:
    name: str
    ok: bool
    needed_for: str
    how_to_fix: str = ""


class Actions(Protocol):
    def context(self) -> dict: ...
    def run(self, command: str, args: dict) -> ActionResult: ...
    def permissions(self) -> list[Permission]: ...
