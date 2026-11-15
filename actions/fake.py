"""Fake actions: nothing really happens on the Mac. Every call is recorded, so
the whole conversation can be tested without touching real apps."""

from actions.base import ActionResult, Permission


class FakeActions:
    def __init__(self, apps=("Safari", "Mail", "Notes", "Music"), targets=(), sites=None, contacts=None,
                 target="Word", recent=("Google Chrome", "Notes", "Microsoft Word")):
        import config
        self.target, self.recent = target, list(recent)   # the app typing goes into (fake)
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
        if command == "open_app" and args.get("app"):
            self.target = args["app"]   # an app opened by voice becomes the dictation target
        say = "From Anna. Subject: Lunch on Sunday. Are you free at noon?" if command == "read_latest_email" else ""
        if command == "compose_email":
            return ActionResult(True, detail=f"[fake] email to {args.get('to', '?')} ready in Mail (not sent)",
                                undo=lambda: self.undone.append(command))
        return ActionResult(True, say=say, detail=f"[fake] {command} {args or ''}".strip(),
                            undo=lambda: self.undone.append(command))

    MENU = ["File > New", "File > Open\u2026", "File > Save", "File > Print\u2026", "Edit > Undo", "Edit > Find",
            "View > Zoom In", "View > Zoom Out", "View > Show Sidebar", "Message > Mark > As Unread",
            "Message > Flag", "Mailbox > Erase Deleted Items", "Insert > Table\u2026"]

    def menu_items(self) -> list[dict]:
        """A fixed, made-up menu (the real one is read with Accessibility)."""
        return [{"label": label, "kind": "menu", "element": None} for label in self.MENU]

    def press_item(self, item: dict) -> ActionResult:
        self.done.append(("press_item", {"label": item["label"]}))
        return ActionResult(True, detail=f"[fake] clicked {item['label']}")

    def front_app_name(self) -> str:
        return self.target

    def target_name(self) -> str:
        return self.target

    def recent_targets(self, count: int = 3) -> list[str]:
        return self.recent[:count]

    def set_target(self, name: str) -> bool:
        self.done.append(("set_target", {"app": name}))
        self.target = name
        return True

    def permissions(self) -> list[Permission]:
        return [Permission("Fake actions", True, "testing without touching the Mac")]
