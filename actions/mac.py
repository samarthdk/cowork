"""Real actions on the Mac.

What needs which macOS permission (System Settings > Privacy & Security):

    open an app                      nothing
    scroll, arrow keys, back/forward Accessibility  (for the app that runs Second Voice, e.g. Terminal)
    list and click on-screen buttons Accessibility
    read / reply to email            Automation > Terminal > Mail (macOS asks the first time)

Nothing here can hang: every automation call has a timeout, and a missing
permission comes back as a plain-English problem instead of an error.
Replying to email is prepared in a Mail window but NOT sent unless
config.EMAIL_REALLY_SEND is True.
"""

import subprocess
import time
from pathlib import Path

import config
from actions.base import ActionResult, Permission

ACCESSIBILITY_FIX = ("System Settings > Privacy & Security > Accessibility: turn on the app that runs "
                     "Second Voice (Terminal), then start Second Voice again.")
AUTOMATION_FIX = ("System Settings > Privacy & Security > Automation > Terminal: turn on Mail "
                  "(or click OK when macOS asks).")
PREFERRED_APPS = ["Safari", "Mail", "Notes", "Music", "Calendar", "Messages", "Photos", "Maps", "FaceTime",
                  "Reminders", "Calculator", "Weather", "News", "Clock", "Contacts", "TV", "Podcasts",
                  "Books", "Preview", "TextEdit", "System Settings"]
APP_FOLDERS = ["/Applications", "/System/Applications", "/System/Applications/Utilities",
               str(Path.home() / "Applications")]
SKIP_APPS = ("shim", "uninstall", "python", "helper", "deploy", "installer")
CLICKABLE_ROLES = {"AXButton", "AXLink", "AXMenuButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXTab"}
KEYS = {"left": 123, "right": 124, "return": 36, "[": 33, "]": 30}
COMMAND_KEY = 0x100000  # kCGEventFlagMaskCommand
SCROLL_LINES = 12


def accessibility_allowed() -> bool:
    try:
        import ApplicationServices as AS
        return bool(AS.AXIsProcessTrusted())
    except Exception:
        return False


def installed_apps() -> list[str]:
    names = set()
    for folder in APP_FOLDERS:
        for app in Path(folder).glob("*.app"):
            if not any(word in app.stem.lower() for word in SKIP_APPS):
                names.add(app.stem)
    return sorted(names)


def running_apps() -> list[str]:
    try:
        from AppKit import NSWorkspace
        return [a.localizedName() for a in NSWorkspace.sharedWorkspace().runningApplications()
                if a.activationPolicy() == 0 and a.localizedName()]
    except Exception:
        return []


def run_applescript(lines: list[str], *arguments: str) -> tuple[bool, str]:
    """Run AppleScript with a timeout. Text is passed as arguments, never pasted
    into the script. Returns (ok, output or plain-English problem)."""
    command = ["osascript"]
    for line in lines:
        command += ["-e", line]
    try:
        done = subprocess.run(command + list(arguments), capture_output=True, text=True,
                              timeout=config.ACTION_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return False, "The Mac did not answer in time (a permission question may be waiting on screen). " + AUTOMATION_FIX
    if done.returncode == 0:
        return True, done.stdout.strip()
    error = done.stderr.strip()
    if "-1743" in error or "Not authorized" in error or "not allowed" in error.lower():
        return False, "macOS has not allowed this yet. " + AUTOMATION_FIX
    if "-1728" in error or "-1719" in error or "Can’t get" in error or "Can't get" in error:
        return False, "There is no email in the inbox to use."
    return False, error or "The action failed."


class MacActions:
    def __init__(self):
        self._apps = self._choose_apps()
        self._targets: dict = {}        # label -> accessibility element, from the last context() call

    # ---- what exists ------------------------------------------------------

    def _choose_apps(self) -> list[str]:
        installed = installed_apps()
        ordered = [a for a in running_apps() if a in installed]
        ordered += [a for a in PREFERRED_APPS if a in installed and a not in ordered]
        ordered += [a for a in installed if a not in ordered]
        return ordered[:config.MAX_APPS]

    def context(self) -> dict:
        self._targets = self._on_screen()
        return {"app": list(self._apps), "target": list(self._targets)}

    def _on_screen(self) -> dict:
        """Buttons and links in the front window of the front app: label -> element.
        Empty when Accessibility is not allowed, or when the front window is
        Second Voice itself (it must not click its own buttons)."""
        if not accessibility_allowed():
            return {}
        try:
            import ApplicationServices as AS
            from AppKit import NSWorkspace
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            app = AS.AXUIElementCreateApplication(front.processIdentifier())
            AS.AXUIElementSetMessagingTimeout(app, 0.25)

            def attribute(element, name):
                error, value = AS.AXUIElementCopyAttributeValue(element, name, None)
                return value if error == 0 else None

            window = attribute(app, "AXFocusedWindow")
            if window is None or "second voice" in str(attribute(window, "AXTitle") or "").lower():
                return {}
            found, queue, visited, deadline = {}, [window], 0, time.monotonic() + 0.35
            while queue and visited < 500 and time.monotonic() < deadline and len(found) < config.MAX_TARGETS:
                element = queue.pop(0)
                visited += 1
                if attribute(element, "AXRole") in CLICKABLE_ROLES:
                    label = attribute(element, "AXTitle") or attribute(element, "AXDescription") or ""
                    label = " ".join(str(label).split())
                    if 1 < len(label) <= 40 and label not in found:
                        found[label] = element
                queue.extend(attribute(element, "AXChildren") or [])
            return found
        except Exception:
            return {}

    # ---- doing things -------------------------------------------------------

    def run(self, command: str, args: dict) -> ActionResult:
        handler = getattr(self, f"_do_{command}", None)
        if handler is None:
            return ActionResult(False, problem=f"I don't know how to do '{command}' yet.")
        try:
            return handler(args)
        except Exception as error:
            return ActionResult(False, problem=f"{type(error).__name__}: {error}")

    def _do_open_app(self, args: dict) -> ActionResult:
        app = args.get("app", "")
        if app not in self._apps:
            return ActionResult(False, problem=f"'{app}' is not an app on this Mac.")
        was_running = app in running_apps()
        done = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=config.ACTION_TIMEOUT_S)
        if done.returncode != 0:
            return ActionResult(False, problem=done.stderr.strip() or f"Could not open {app}.")

        def undo():
            if not was_running:   # only close what this action opened
                run_applescript(["on run argv", "tell application (item 1 of argv) to quit", "end run"], app)

        return ActionResult(True, detail=f"Opened {app}", undo=undo)

    def _scroll(self, lines: int) -> ActionResult:
        if not accessibility_allowed():
            return ActionResult(False, problem="Scrolling needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        import Quartz
        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return ActionResult(True, detail="Scrolled " + ("down" if lines < 0 else "up"),
                            undo=lambda: self._scroll(-lines))

    def _do_scroll_down(self, args: dict) -> ActionResult:
        return self._scroll(-SCROLL_LINES)

    def _do_scroll_up(self, args: dict) -> ActionResult:
        return self._scroll(SCROLL_LINES)

    def _key(self, key: str, command_key: bool = False, undo_key: str | None = None) -> ActionResult:
        if not accessibility_allowed():
            return ActionResult(False, problem="Pressing keys needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        import Quartz
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, KEYS[key], down)
            if command_key:
                Quartz.CGEventSetFlags(event, COMMAND_KEY)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        undo = (lambda: self._key(undo_key, command_key)) if undo_key else None
        return ActionResult(True, detail=f"Pressed {'Command + ' if command_key else ''}{key}", undo=undo)

    def _do_press_left(self, args: dict) -> ActionResult:
        return self._key("left", undo_key="right")

    def _do_press_right(self, args: dict) -> ActionResult:
        return self._key("right", undo_key="left")

    def _do_press_return(self, args: dict) -> ActionResult:
        return self._key("return")

    def _do_go_back(self, args: dict) -> ActionResult:
        return self._key("[", command_key=True, undo_key="]")

    def _do_go_forward(self, args: dict) -> ActionResult:
        return self._key("]", command_key=True, undo_key="[")

    def _do_click(self, args: dict) -> ActionResult:
        target = args.get("target", "")
        element = self._targets.get(target)
        if element is None:
            return ActionResult(False, problem=f"'{target}' is not on screen any more.")
        import ApplicationServices as AS
        error = AS.AXUIElementPerformAction(element, "AXPress")
        if error != 0:
            return ActionResult(False, problem=f"Could not click '{target}' (code {error}).")
        return ActionResult(True, detail=f"Clicked {target}")

    def _do_read_latest_email(self, args: dict) -> ActionResult:
        ok, output = run_applescript([
            'tell application "Mail"',
            "set m to first message of inbox",
            "set c to content of m",
            "if (length of c) > 500 then set c to (text 1 thru 500 of c)",
            'return (sender of m) & linefeed & (subject of m) & linefeed & c',
            "end tell"])
        if not ok:
            return ActionResult(False, problem=output)
        sender, _, rest = output.partition("\n")
        subject, _, body = rest.partition("\n")
        name = sender.split("<")[0].strip().strip('"') or "an unknown sender"   # never read the address aloud
        body = " ".join(body.split())
        return ActionResult(True, say=f"From {name}. Subject: {subject.strip()}. {body}".strip(),
                            detail=f"Read the latest email (from {name})")

    def _do_reply_email(self, args: dict) -> ActionResult:
        text = args.get("text", "").strip()
        if not text:
            return ActionResult(False, problem="There is no reply message.")
        script = ["on run argv", 'tell application "Mail"', "set m to first message of inbox",
                  "set r to reply m with opening window",
                  "delay 0.5",
                  "tell r to set content to (item 1 of argv) & linefeed & linefeed & (content of r)"]
        script += ["send r"] if config.EMAIL_REALLY_SEND else ["activate"]
        script += ["end tell", "end run"]
        ok, output = run_applescript(script, text)
        if not ok:
            return ActionResult(False, problem=output)
        if config.EMAIL_REALLY_SEND:
            return ActionResult(True, say="Your reply was sent.", detail=f"Sent reply: {text}")
        return ActionResult(True, say="Your reply is ready in Mail. It has not been sent.",
                            detail=f"Prepared (not sent) reply: {text}")

    # ---- permissions ---------------------------------------------------------

    def permissions(self) -> list[Permission]:
        found = [Permission("Accessibility", accessibility_allowed(),
                            "scrolling, arrow keys, and choosing buttons that are on screen",
                            ACCESSIBILITY_FIX)]
        ok, output = run_applescript(['tell application "Mail" to get name'])
        found.append(Permission("Automation: Mail", ok, "reading and replying to email",
                                "" if ok else output))
        return found


if __name__ == "__main__":
    # Try on its own: .venv/bin/python -m actions.mac            (shows what it can see and do)
    #                 .venv/bin/python -m actions.mac open_app Notes
    import sys
    actions = MacActions()
    if len(sys.argv) > 1:
        name = sys.argv[1]
        args = {"app": sys.argv[2]} if name == "open_app" and len(sys.argv) > 2 else \
               {"text": " ".join(sys.argv[2:])} if name == "reply_email" else \
               {"target": " ".join(sys.argv[2:])} if name == "click" else {}
        if name == "click":
            actions.context()
        print(actions.run(name, args))
    else:
        context = actions.context()
        print(f"Apps the Brain may choose from ({len(context['app'])}): {', '.join(context['app'])}")
        print(f"On screen right now ({len(context['target'])}): {', '.join(context['target']) or '(nothing, or no permission)'}")
        for p in actions.permissions():
            print(f"{'OK  ' if p.ok else 'NEED'} {p.name}: for {p.needed_for}" + ("" if p.ok else f"\n     -> {p.how_to_fix}"))
