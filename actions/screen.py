"""What is on screen right now, read with the macOS Accessibility API ("screen state").

    screen = read_screen(pid, "Microsoft Word")
    screen.words()   -> "Word open, start screen"            (for the Progress panel)
    screen.brief()   -> "Microsoft Word: start screen (...)"  (short context for the Brain)
    screen.typeable  -> False: the cursor is not in a place for dictated words

Used everywhere the app needs to know where the person is (see ui/app.py):
  - the current app always comes from here, never from the last command;
  - nothing is typed unless a text box, document or email box has the cursor;
  - a search box is not a place for dictation (only for "search for ...");
  - choices on screen (Word's start screen) are offered as a choice card.

Only names, roles and labels are read (never window contents or pixels), and
every call has a short timeout, so a busy app cannot stall Cowork.
Measured on this Mac (Word, Mail): 1-8 ms a read (the very first read up to 0.2 s).

What each app looks like to Accessibility (measured 2026-11-14):
  Word start screen   window identifier "DocStage" ("Open new and recent files"),
                      a "Blank Document" button, a search box (AXSearchField)
  Word document       focus is the "Document Pane" split group (AXLayoutArea inside)
  Mail new email      Mail.toField, Mail.ccField, Mail.subjectField, and a web area
                      described "message body"; the viewer is Mail.messageViewer.*
  browsers            the address bar is an AXTextField ("Address and search bar")
"""

import time
from dataclasses import dataclass, field as _field

TIMEOUT_S = 0.25
MAIL_FIELDS = {"Mail.toField": "to", "Mail.ccField": "cc", "Mail.bccField": "bcc", "Mail.subjectField": "subject"}
FIELD_WORDS = {"to": "To", "cc": "Cc", "bcc": "Bcc", "subject": "Subject", "body": "the message"}
BROWSERS = ("Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge", "Brave Browser")
TEXT_ROLES = ("AXTextArea", "AXTextField")


def short(app: str) -> str:
    return app.replace("Microsoft ", "").replace("Google ", "")


@dataclass
class Screen:
    app: str = ""               # the app the person is using (never Cowork, Finder, Terminal)
    pid: int | None = None
    window: str = ""            # its focused window's title
    place: str = ""             # "start screen", "document", "new email", "inbox", "browser", "" (unknown)
    focus: str = ""             # what has the cursor, in words: "the To box", "a search box", "the document"
    field: str = ""             # Mail: to/cc/subject/body; browsers: address
    typeable: bool = False      # dictated words may be typed here
    search: bool = False        # the cursor is in a search box
    choices: list = _field(default_factory=list)   # what can be chosen on screen (Word: Blank document, ...)
    took_ms: float = 0.0
    readable: bool = True       # False: Accessibility gave nothing (no permission, or the app is busy)

    def words(self) -> str:
        """For the Progress panel: "Word open, start screen", "Mail open, new email, cursor in To"."""
        if not self.app:
            return "No app open to work in"
        name = short(self.app)
        if not self.readable:
            return f"{name} open (couldn't read its screen)"
        parts = [f"{name} open"]
        if self.place == "document":
            return "Blank document ready" if self.window.startswith("Document") else f"{name} document ready"
        if self.place:
            parts.append(self.place)
        if self.field in FIELD_WORDS:
            parts.append(f"cursor in {FIELD_WORDS[self.field]}")
        elif self.field == "address":
            parts.append("cursor in the address bar")
        elif self.search:
            parts.append("cursor in a search box")
        elif self.focus and self.typeable:
            parts.append(f"cursor in {self.focus}")
        return ", ".join(parts)

    def brief(self) -> str:
        """One short line for the Brain: which app, what it shows, where the cursor is."""
        if not self.app:
            return "No app is open to work in."
        text = f"{self.app}"
        if self.place:
            text += f", {self.place}"
        if self.choices:
            text += f" (choices: {', '.join(self.choices)})"
        if self.focus:
            text += f"; cursor in {self.focus}" + ("" if self.typeable or self.search else " (nothing to type into)")
        else:
            text += "; nothing to type into"
        return text + "."

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("app", "window", "place", "focus", "field", "typeable",
                                              "search", "choices", "took_ms", "readable")} | {"words": self.words()}


def _attr(element, name):
    import ApplicationServices as AS
    try:
        error, value = AS.AXUIElementCopyAttributeValue(element, name, None)
    except Exception:
        return None
    return value if error == 0 else None


def find(root, test, most: int = 400, depth: int = 12):
    """The first element under `root` (breadth first) for which test(element) is true."""
    queue, seen = [(root, 0)], 0
    while queue and seen < most:
        element, level = queue.pop(0)
        seen += 1
        try:
            if test(element):
                return element
        except Exception:
            pass
        if level < depth:
            queue.extend((child, level + 1) for child in (_attr(element, "AXChildren") or []))
    return None


def read_screen(pid: int | None, app: str) -> Screen:
    start = time.perf_counter()
    screen = Screen(app=app or "", pid=pid)
    if not pid or not app:
        return screen
    try:
        import ApplicationServices as AS
        root = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(root, TIMEOUT_S)
        window = _attr(root, "AXFocusedWindow")
        focused = _attr(root, "AXFocusedUIElement")
        if window is None and focused is None:
            screen.readable = False
            return screen
        screen.window = str(_attr(window, "AXTitle") or "") if window is not None else ""
        window_id = str(_attr(window, "AXIdentifier") or "") if window is not None else ""
        role = str(_attr(focused, "AXRole") or "") if focused is not None else ""
        subrole = str(_attr(focused, "AXSubrole") or "") if focused is not None else ""
        ident = str(_attr(focused, "AXIdentifier") or "") if focused is not None else ""
        about = str(_attr(focused, "AXDescription") or "") if focused is not None else ""

        # The kind of box with the cursor, for any app.
        if subrole == "AXSearchField":
            screen.focus, screen.search = "a search box", True
        elif role in TEXT_ROLES:
            screen.focus, screen.typeable = ("a text box" if role == "AXTextField" else "a text area"), True
        elif role == "AXWebArea" and _attr(focused, "AXEditableAncestor") is not None:
            screen.focus, screen.typeable = "a text area", True

        # What some apps are showing (see the notes at the top).
        if app == "Microsoft Word":
            if window_id == "DocStage":
                screen.place, screen.choices = "start screen", ["Blank document", "Open a recent file"]
                if not screen.search:
                    screen.focus, screen.typeable = "", False   # the template list: not a place to type
            elif ident == "Document Pane" or role == "AXLayoutArea":
                screen.place, screen.focus, screen.typeable = "document", "the document", True
        elif app == "Mail":
            if ident in MAIL_FIELDS:
                screen.place, screen.field = "new email", MAIL_FIELDS[ident]
                screen.focus, screen.typeable = f"the {FIELD_WORDS[screen.field]} box", True
            elif role == "AXWebArea" and about == "message body":
                screen.place, screen.field, screen.focus, screen.typeable = "new email", "body", "the message", True
            elif window is not None and find(window, lambda e: _attr(e, "AXIdentifier") == "Mail.toField", depth=2):
                screen.place = "new email"
            elif window_id.startswith("Mail.messageViewer"):
                screen.place = "inbox"
        elif app in BROWSERS:
            screen.place = "browser"
            if role == "AXTextField" and ("address" in about.lower() or "url" in ident.lower() or
                                          "address" in str(_attr(focused, "AXTitle") or "").lower()):
                screen.field, screen.focus, screen.typeable = "address", "the address bar", True
        elif app in ("TextEdit", "Pages") and role == "AXTextArea":
            screen.place, screen.focus = "document", "the document"
    except Exception:
        screen.readable = False
    finally:
        screen.took_ms = round((time.perf_counter() - start) * 1000, 1)
    return screen


def mail_fields(pid: int) -> dict:
    """The boxes of Mail's front new-email window: {"to": element, "cc": ..., "subject": ..., "body": ...}."""
    import ApplicationServices as AS
    root = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetMessagingTimeout(root, TIMEOUT_S)
    window = _attr(root, "AXFocusedWindow")
    found = {}
    if window is None:
        return found
    for child in _attr(window, "AXChildren") or []:
        ident = str(_attr(child, "AXIdentifier") or "")
        if ident in MAIL_FIELDS:
            found[MAIL_FIELDS[ident]] = child
    body = find(window, lambda e: _attr(e, "AXRole") == "AXWebArea" and _attr(e, "AXDescription") == "message body")
    if body is not None:
        found["body"] = body
    return found


def value_of(element) -> str:
    """A box's text, including Mail's address "chips" (measured: once an address is a chip, the To box's
    own value is just a placeholder character and the address is in a small text box inside it)."""
    value = _attr(element, "AXValue")
    parts = [value.replace("\ufffc", " ")] if isinstance(value, str) else []
    for child in _attr(element, "AXChildren") or []:
        inner = _attr(child, "AXValue")
        if isinstance(inner, str) and inner.strip():
            parts.append(inner)
    return ", ".join(p.strip() for p in parts if p.strip())


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m actions.screen        (reads the app you are using, every second)
    from actions.mac import FrontApp
    front = FrontApp()
    for _ in range(30):
        front.update()
        s = read_screen(front.pid, front.name)
        print(f"{s.took_ms:5.1f} ms  {s.words():<55} | {s.brief()}")
        time.sleep(1)
