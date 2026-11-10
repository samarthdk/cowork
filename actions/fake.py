"""Fake actions: nothing really happens on the Mac. Every call is recorded, so
the whole conversation can be tested without touching real apps."""

from actions.base import ActionResult, Permission


class FakeActions:
    def __init__(self, apps=("Safari", "Mail", "Notes", "Music"), targets=()):
        self.apps, self.targets = list(apps), list(targets)
        self.done: list[tuple] = []     # (command, args) in order
        self.undone: list[str] = []

    def context(self) -> dict:
        return {"app": list(self.apps), "target": list(self.targets)}

    def run(self, command: str, args: dict) -> ActionResult:
        args = {k: v for k, v in args.items() if v}
        self.done.append((command, args))
        say = "From Anna. Subject: Lunch on Sunday. Are you free at noon?" if command == "read_latest_email" else ""
        return ActionResult(True, say=say, detail=f"[fake] {command} {args or ''}".strip(),
                            undo=lambda: self.undone.append(command))

    def permissions(self) -> list[Permission]:
        return [Permission("Fake actions", True, "testing without touching the Mac")]
