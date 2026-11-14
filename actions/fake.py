"""Fake actions: nothing really happens on the Mac. Every call is recorded, so
the whole conversation can be tested without touching real apps."""

from actions.base import ActionResult, Permission


class FakeActions:
    def __init__(self, apps=("Safari", "Mail", "Notes", "Music"), targets=(), sites=None, contacts=None):
        import config
        self.apps, self.targets = list(apps), list(targets)
        self.sites = list(config.SITES) if sites is None else list(sites)
        self.contacts = ["Anna", "Mom", "Doctor Lee"] if contacts is None else list(contacts)
        self.done: list[tuple] = []     # (command, args) in order
        self.undone: list[str] = []

    def context(self) -> dict:
        found = {"app": list(self.apps), "target": list(self.targets), "site": list(self.sites)}
        if self.contacts:
            found["to"] = list(self.contacts)
        return found

    def run(self, command: str, args: dict) -> ActionResult:
        args = {k: v for k, v in args.items() if v}
        self.done.append((command, args))
        say = "From Anna. Subject: Lunch on Sunday. Are you free at noon?" if command == "read_latest_email" else ""
        if command == "compose_email":
            return ActionResult(True, detail=f"[fake] email to {args.get('to', '?')} ready in Mail (not sent)",
                                undo=lambda: self.undone.append(command))
        return ActionResult(True, say=say, detail=f"[fake] {command} {args or ''}".strip(),
                            undo=lambda: self.undone.append(command))

    def permissions(self) -> list[Permission]:
        return [Permission("Fake actions", True, "testing without touching the Mac")]
