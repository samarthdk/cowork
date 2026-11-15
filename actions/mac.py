"""Real actions on the Mac.

What needs which macOS permission (System Settings > Privacy & Security):

    open an app                      nothing
    scroll, arrow keys, back/forward Accessibility  (for the app that runs Second Voice, e.g. Terminal)
    list and click on-screen buttons Accessibility
    read / reply to email            Automation > Terminal > Mail (macOS asks the first time)
    write / send an email            Automation > Terminal > Mail
    type text, close an app          Accessibility (typing); closing needs nothing
    open a website / Google search   nothing (opens in config.BROWSER, Safari)

Typing, scrolling, keys and closing act on "the app you were using": the
front app that is not Second Voice itself (FrontApp below). So answering a
question in the Cowork window never makes the next action land in Cowork.

Nothing here can hang: every automation call has a timeout, and a missing
permission comes back as a plain-English problem instead of an error.
Replying to email is prepared in a Mail window but NOT sent unless
config.EMAIL_REALLY_SEND is True.
"""

import os
import subprocess
import threading
import time
import urllib.parse
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
KEYS = {"left": 123, "right": 124, "return": 36, "[": 33, "]": 30, "q": 12, "delete": 51, "l": 37, "n": 45, "t": 17,
        "tab": 48, "s": 1, "d": 2, "w": 13, "forward_delete": 117}
COMMAND_KEY = 0x100000  # kCGEventFlagMaskCommand
SHIFT_KEY = 0x20000     # kCGEventFlagMaskShift
SCROLL_LINES = 12
NOT_A_TARGET = ("Terminal", "iTerm2", "Finder", "loginwindow", "Python",   # never typed into (Python = Cowork)
                "LM Studio")   # runs the Brain: never typed into or closed
from actions.screen import BROWSERS, mail_fields, read_screen, value_of  # noqa: E402
DOCUMENT_APPS = ("Microsoft Word", "TextEdit", "Pages")
# Opening an app only opens it (measured: Mail reopens unsent drafts at launch, and Cowork used to press
# Command+N as well, so "open Mail" gave two drafts). A new email or document only when asked.
NO_RESTORE = ("Mail",)   # launched without reopening the windows left open last time
LAUNCH_WAIT_S = 12.0
READY_WAIT_S = 8.0       # longest wait for a new document / email window to be ready for typing
MAIL_ORDER = ("to", "cc", "subject", "body")
FIELD_NAMES = {"to": "To", "cc": "Cc", "subject": "Subject", "message": "body", "body": "body"}
NEVER_CLOSE = ("Finder", "Terminal", "loginwindow", "Dock", "Python", "LM Studio")   # LM Studio runs the Brain
NOT_A_WINDOW_OWNER = ("Window Server", "Dock", "Control Center", "SystemUIServer", "Notification Center",
                      "Spotlight", "WindowManager", "Wallpaper", "TextInputMenuAgent", "screencaptureui")
TYPE_CHUNK = 16   # characters per typing event


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


def front_windows() -> list[dict]:
    """Normal app windows on screen, front to back: [{"pid", "app", "bounds"}].
    Works without a run loop and without Screen Recording permission
    (only window owners and sizes are read, never their contents)."""
    import Quartz
    options = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    found = []
    for w in Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID) or []:
        bounds = w.get("kCGWindowBounds") or {}
        if (w.get("kCGWindowLayer") != 0 or w.get("kCGWindowOwnerName") in NOT_A_WINDOW_OWNER
                or bounds.get("Width", 0) < 120 or bounds.get("Height", 0) < 120):
            continue
        found.append({"pid": int(w["kCGWindowOwnerPID"]), "app": str(w.get("kCGWindowOwnerName", "")),
                      "bounds": dict(bounds)})
    return found


def focused_pid() -> int | None:
    """The app that has the keyboard right now, asked of the system directly (None if it can't say).
    Measured: an app's window can be on top while another app still has the keyboard; a Command+Q
    for Mail then reached Word (which offered to save its document)."""
    try:
        import ApplicationServices as AS
        error, app = AS.AXUIElementCopyAttributeValue(AS.AXUIElementCreateSystemWide(), "AXFocusedApplication", None)
        if error != 0 or app is None:
            return None
        error, pid = AS.AXUIElementGetPid(app, None)
        return int(pid) if error == 0 else None
    except Exception:
        return None


class FrontApp:
    """Remembers the app the person was last using that is not Second Voice
    (this process). Checked a few times a second in the background."""
    EVERY_S = 0.4

    def __init__(self, own_pid: int | None = None):
        self.own_pid = own_pid or os.getpid()
        self.pid, self.name = None, ""
        self.recent: list[str] = []   # app names, most recent first (for "Which app should I type into?")
        self._stop = threading.Event()
        threading.Thread(target=self._watch, daemon=True, name="front-app").start()

    def _watch(self):
        while not self._stop.is_set():
            self.update()
            self._stop.wait(self.EVERY_S)

    def update(self):
        try:
            for window in front_windows():
                if window["pid"] != self.own_pid and window["app"] not in NOT_A_TARGET:
                    self._remember(window["pid"], window["app"])
                    return
            if self.pid and not pid_alive(self.pid):   # the target was closed and nothing else is open
                self.pid, self.name = None, ""
        except Exception:
            pass

    def _remember(self, pid: int, name: str) -> None:
        self.pid, self.name = pid, name
        if name in self.recent:
            self.recent.remove(name)
        self.recent.insert(0, name)
        del self.recent[6:]

    def windows_of(self, name: str) -> list[dict]:
        return [w for w in front_windows() if w["app"] == name]

    def wait_until_front(self, name: str, seconds: float) -> bool:
        """After opening an app: wait for its window to come to the front, then make it the target."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            windows = front_windows()
            if windows and windows[0]["app"] == name:
                self._remember(windows[0]["pid"], name)
                time.sleep(0.3)   # let it finish drawing before keys arrive
                return True
            time.sleep(0.1)
        return False

    def bounds(self) -> dict | None:
        for window in front_windows():
            if window["pid"] == self.pid:
                return window["bounds"]
        return None

    def bring_forward(self) -> bool:
        """Make the remembered app the front app again. True when it is."""
        if not self.pid:
            return False
        from AppKit import NSRunningApplication
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app is None or not pid_alive(self.pid):
            return False
        if self.in_front():
            return True
        # Activate it directly first. ("open -b" sends a "reopen" event: measured, Chrome then shows
        # its "Who's using Chrome?" window, which takes the keystrokes.) "open" only as a fallback.
        app.activateWithOptions_(2)   # NSApplicationActivateIgnoringOtherApps
        end = time.monotonic() + 0.8
        while time.monotonic() < end:
            if self.in_front():
                time.sleep(0.15)
                return True
            time.sleep(0.05)
        bundle = app.bundleIdentifier()
        if bundle:
            subprocess.run(["open", "-b", bundle], capture_output=True, timeout=config.ACTION_TIMEOUT_S)
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            if self.in_front():
                time.sleep(0.15)   # let it settle before keys arrive
                return True
            time.sleep(0.05)
        return False

    def in_front(self) -> bool:
        """Its window is on top AND it has the keyboard (keys go to the app with the keyboard)."""
        windows = front_windows()
        if not windows or windows[0]["pid"] != self.pid:
            return False
        focused = focused_pid()
        return focused is None or focused == self.pid

    def stop(self):
        self._stop.set()


def _select_first_message() -> str:
    """Select the first message in Mail's message list (Inbox window) through Accessibility.
    Returns the selected row's text, or "" if it couldn't (e.g. no Accessibility permission)."""
    try:
        import ApplicationServices as AS
        found = subprocess.run(["pgrep", "-x", "Mail"], capture_output=True, text=True).stdout.split()
        if not found or not accessibility_allowed():
            return ""
        app = AS.AXUIElementCreateApplication(int(found[0]))

        def attr(element, name):
            error, value = AS.AXUIElementCopyAttributeValue(element, name, None)
            return value if error == 0 else None

        def table_in(element, depth=0):
            if attr(element, "AXRole") == "AXTable" and attr(element, "AXDescription") == "messages":
                return element
            for child in (attr(element, "AXChildren") or []) if depth < 8 else []:
                hit = table_in(child, depth + 1)
                if hit is not None:
                    return hit
            return None

        for window in attr(app, "AXWindows") or []:
            if not str(attr(window, "AXTitle") or "").startswith("Inbox"):
                continue
            table = table_in(window)
            rows = attr(table, "AXRows") if table is not None else None
            if rows:
                AS.AXUIElementSetAttributeValue(table, "AXSelectedRows", [rows[0]])
                return "selected"
    except Exception:
        pass
    return ""


def pid_alive(pid: int) -> bool:
    """Is this process still running? (Asked of the system directly: AppKit's
    own view of running apps goes stale in a process without an event loop.)"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def site_url(name: str) -> str | None:
    """A known website's address (config.SITES), by name, ignoring case."""
    for site, url in config.SITES.items():
        if site.lower() == (name or "").strip().lower():
            return url
    return None


def open_in_browser(url: str):
    """Open an address in config.BROWSER (Safari), whatever the default browser is."""
    return subprocess.run(["open", "-a", config.BROWSER, url], capture_output=True, text=True,
                          timeout=config.ACTION_TIMEOUT_S)


def search_url(query: str) -> str:
    return "https://www.google.com/search?q=" + urllib.parse.quote_plus(query.strip())


def load_contacts() -> dict:
    """Names the person emails, and their addresses (profiles/contacts.json)."""
    import json
    path = Path(config.CONTACTS_PATH)
    if not path.exists():   # the example file until the person makes their own
        path = path.with_name("contacts.example.json")
    try:
        data = json.loads(path.read_text())
        return {str(k): str(v) for k, v in data.items() if not str(k).startswith("_")}
    except Exception:
        return {}


def recipient(name: str, contacts: dict) -> str:
    """'Anna' -> her address from the contacts; an address stays as it is."""
    name = (name or "").strip()
    for known, address in contacts.items():
        if known.lower() == name.lower():
            return address
    return name


class MacActions:
    def __init__(self, own_pid: int | None = None):
        self._apps = self._choose_apps()
        self._targets: dict = {}        # label -> accessibility element, from the last context() call
        self.front = FrontApp(own_pid)
        self.contacts = load_contacts()
        self._last_email = None         # subject of the email this session wrote (the one "send" sends)

    # ---- what exists ------------------------------------------------------

    def _choose_apps(self) -> list[str]:
        installed = installed_apps()
        ordered = [a for a in running_apps() if a in installed]
        ordered += [a for a in PREFERRED_APPS if a in installed and a not in ordered]
        ordered += [a for a in installed if a not in ordered]
        return ordered[:config.MAX_APPS]

    def screen(self):
        """What is on screen in the app the person is using, right now (actions/screen.py)."""
        self.front.update()
        screen = read_screen(self.front.pid if self.target_name() else None, self.target_name())
        if not screen.readable:   # measured: Word doesn't answer for a moment right after typing
            time.sleep(0.4)
            screen = read_screen(self.front.pid if self.target_name() else None, self.target_name())
        return screen

    def context(self) -> dict:
        self._targets = self._on_screen()
        found = {"app": list(self._apps), "target": list(self._targets), "site": list(config.SITES),
                 "field": ["To", "Cc", "Subject", "Message"]}
        if self.contacts:
            found["to"] = list(self.contacts)
        found["_screen"] = self.screen()   # not a detail: what the Brain is told is on screen
        return found

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
        windows = self.front.windows_of(app)
        if windows:   # already open: bring it forward without a "reopen" (see FrontApp.bring_forward)
            self.front._remember(windows[0]["pid"], app)
            if not self.front.bring_forward():
                return ActionResult(False, problem=f"Could not bring {app} forward.")
        else:
            done = self._launch(app)
            if done.returncode != 0:
                return ActionResult(False, problem=done.stderr.strip() or f"Could not open {app}.")
        detail = f"Opened {app}"
        # It becomes the app being worked in (and typed into). Nothing else is done to it.
        if app not in NOT_A_TARGET:
            in_front = self.front.wait_until_front(app, LAUNCH_WAIT_S if not windows else 2.0)
            if not in_front:   # measured: Mail, still starting up, took the front back from Word
                windows = self.front.windows_of(app)
                if windows:
                    self.front._remember(windows[0]["pid"], app)
                in_front = self.front.bring_forward() and self.front.wait_until_front(app, 3.0)
            if not in_front:
                return ActionResult(False, problem=f"{app} is open but didn't come to the front. "
                                                   f"Click its window, or say \u201copen {app}\u201d again.")
            detail += " · typing goes here"

        def undo():
            if not was_running:   # only close what this action opened
                run_applescript(["on run argv", "tell application (item 1 of argv) to quit", "end run"], app)

        return ActionResult(True, detail=detail, undo=undo)

    def _launch(self, app: str):
        command = ["open", "-a", app]
        if app in NO_RESTORE and app not in running_apps():
            command += ["--args", "-ApplePersistenceIgnoreState", "YES"]   # no windows from last time
        return subprocess.run(command, capture_output=True, text=True, timeout=config.ACTION_TIMEOUT_S)

    def _ensure_open(self, app: str) -> bool:
        """The app is open and in front, and is the app being worked in."""
        windows = self.front.windows_of(app)
        if windows:
            self.front._remember(windows[0]["pid"], app)
            return self.front.bring_forward()
        if self._launch(app).returncode != 0:
            return False
        return self.front.wait_until_front(app, LAUNCH_WAIT_S)

    def _wait_for(self, test, seconds: float = READY_WAIT_S, every: float = 0.15):
        """Read the screen until test(screen) is true. Returns the last screen read."""
        end = time.monotonic() + seconds
        while True:
            screen = self.screen()
            if test(screen) or time.monotonic() > end:
                return screen
            time.sleep(every)

    def _scroll(self, lines: int) -> ActionResult:
        if not accessibility_allowed():
            return ActionResult(False, problem="Scrolling needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="There is no app window to scroll.")
        import Quartz
        event = Quartz.CGEventCreateScrollWheelEvent(None, Quartz.kCGScrollEventUnitLine, 1, lines)
        bounds = self.front.bounds()
        if bounds:   # scroll the middle of that app's window, wherever the pointer is
            Quartz.CGEventSetLocation(event, (bounds["X"] + bounds["Width"] / 2, bounds["Y"] + bounds["Height"] / 2))
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        return ActionResult(True, detail="Scrolled " + ("down" if lines < 0 else "up"),
                            undo=lambda: self._scroll(-lines))

    def _do_scroll_down(self, args: dict) -> ActionResult:
        return self._scroll(-SCROLL_LINES)

    def _do_scroll_up(self, args: dict) -> ActionResult:
        return self._scroll(SCROLL_LINES)

    def _key(self, key: str, command_key: bool = False, undo_key: str | None = None, shift: bool = False) -> ActionResult:
        if not accessibility_allowed():
            return ActionResult(False, problem="Pressing keys needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="There is no app to send the key to.")
        import Quartz
        flags = (COMMAND_KEY if command_key else 0) | (SHIFT_KEY if shift else 0)
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, KEYS[key], down)
            Quartz.CGEventSetFlags(event, flags)   # never inherit a held modifier
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
        undo = (lambda: self._key(undo_key, command_key)) if undo_key else None
        names = ("Command + " if command_key else "") + ("Shift + " if shift else "")
        return ActionResult(True, detail=f"Pressed {names}{key}", undo=undo)

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

    def _do_open_website(self, args: dict) -> ActionResult:
        site = args.get("site", "")
        url = site_url(site)
        if url is None:
            return ActionResult(False, problem=f"'{site}' is not one of the websites I know.")
        done = open_in_browser(url)
        if done.returncode != 0:
            return ActionResult(False, problem=done.stderr.strip() or f"Could not open {site}.")
        return ActionResult(True, detail=f"Opened {site} in {config.BROWSER}")

    def _do_search_web(self, args: dict) -> ActionResult:
        query = args.get("query", "").strip()
        if not query:
            return ActionResult(False, problem="There is nothing to search for.")
        done = open_in_browser(search_url(query))
        if done.returncode != 0:
            return ActionResult(False, problem=done.stderr.strip() or "Could not open the browser.")
        return ActionResult(True, detail=f"Searched Google for: {query}")

    def _do_compose_email(self, args: dict) -> ActionResult:
        to = recipient(args.get("to", ""), self.contacts)
        subject, body = args.get("subject", "").strip(), args.get("body", "").strip()
        if not to:
            return ActionResult(False, problem="Who should the email go to?")
        ok, output = run_applescript([
            "on run argv",
            'tell application "Mail"',
            "set m to make new outgoing message with properties "
            "{subject:(item 2 of argv), content:(item 3 of argv) & linefeed, visible:true}",
            "tell m to make new to recipient at end of to recipients with properties {address:(item 1 of argv)}",
            "activate",
            "end tell",
            "end run"], to, subject, body)
        if not ok:
            return ActionResult(False, problem=output)
        self._last_email = subject
        self._last_email_to = to
        shown = args.get("to", "") if to == args.get("to", "") else f"{args.get('to')} ({to})"

        def undo():   # close the draft without saving it
            run_applescript(["on run argv", 'tell application "Mail"',
                             "repeat with m in (outgoing messages whose subject is (item 1 of argv))",
                             "close m saving no", "end repeat", "end tell", "end run"], subject)

        return ActionResult(True, detail=f"Email to {shown} ready in Mail (not sent)", undo=undo)

    def _do_send_email(self, args: dict) -> ActionResult:
        """Send the email open on screen in Mail. (Risky: the person has already said yes.)
        Only ever to config.SEND_ONLY_TO, and only when config.EMAIL_REALLY_SEND is on."""
        screen = self.screen()
        if screen.app != "Mail" or screen.place != "new email":
            return ActionResult(False, problem="There's no email open to send. Say \u201cwrite an email\u201d first.")
        to = self._recipients(screen)
        allowed = self.contacts.get(config.SEND_ONLY_TO, "").lower()
        if not to:
            return ActionResult(False, problem="The email has no address in To yet. The email is still open in Mail.")
        if not allowed or any(address != allowed for address in to):
            return ActionResult(False, problem=f"For safety, emails are only sent to '{config.SEND_ONLY_TO}' "
                                               "(SEND_ONLY_TO in config.py). The email is still open in Mail.")
        if not config.EMAIL_REALLY_SEND:
            return ActionResult(False, problem="Sending is switched off (EMAIL_REALLY_SEND in config.py). "
                                               "The email is still open in Mail.")
        pressed = self._key("d", command_key=True, shift=True)   # Mail: Message > Send
        if not pressed.ok:
            return pressed
        after = self._wait_for(lambda s: s.place != "new email", 4.0)
        if after.place == "new email":
            return ActionResult(False, problem="Mail didn't send it (the email is still open).")
        return ActionResult(True, say="Your email was sent.", detail="Sent the email")

    def _recipients(self, screen) -> list[str]:
        """Every address in To, Cc and Bcc of the email on screen. Mail shows a known person as their
        name (measured: the chip says "Samarth Skywards", not the address), so then the draft is saved
        and the addresses are read from Mail itself; every saved draft with this subject counts, so a
        stray address anywhere means no."""
        from actions.screen import _attr
        fields = mail_fields(screen.pid)
        shown = []
        for key in ("to", "cc", "bcc"):
            if key in fields:
                shown += [part.strip(" ,;<>").lower() for part in value_of(fields[key]).split(",") if part.strip(" ,;<>")]
        if all("@" in part for part in shown):
            return shown
        subject = value_of(fields["subject"]) if "subject" in fields else ""
        if not self._key("s", command_key=True).ok:   # save the draft, so Mail can say who it is for
            return []
        time.sleep(1.2)
        ok, output = run_applescript([
            "on run argv", 'tell application "Mail"', 'set found to ""',
            "repeat with m in (messages of drafts mailbox whose subject is (item 1 of argv))",
            "repeat with r in ((to recipients of m) & (cc recipients of m) & (bcc recipients of m))",
            'set found to found & (address of r) & linefeed', "end repeat", "end repeat",
            "return found", "end tell", "end run"], subject)
        found = [line.strip().lower() for line in output.splitlines() if line.strip()] if ok else []
        return found if len(found) >= len(shown) else []   # every chip must have an address

    def _do_type_text(self, args: dict) -> ActionResult:
        text = args.get("text", "")
        if not text.strip():
            return ActionResult(False, problem="There is nothing to type.")
        if not accessibility_allowed():
            return ActionResult(False, problem="Typing needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="There is no app to type into. Open one first.")
        screen = self.screen()
        if not screen.typeable and not args.get("anyway") and screen.readable:
            # Never type unless a place for words has the cursor (Cowork asks the person first).
            return ActionResult(False, problem=f"Nothing in {screen.app or 'the app'} is ready for typing "
                                               f"({screen.words()}).")
        import Quartz
        for start in range(0, len(text), TYPE_CHUNK):
            chunk = text[start:start + TYPE_CHUNK]
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
                Quartz.CGEventSetFlags(event, 0)   # plain typing, whatever was pressed before
                Quartz.CGEventKeyboardSetUnicodeString(event, len(chunk), chunk)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.02)
        return ActionResult(True, detail=f"Typed into {self.front.name}: {text}")

    def _do_delete_text(self, args: dict) -> ActionResult:
        """Delete the last N characters typed (Backspace N times): undo for dictation.
        Not a command the Brain can choose; only used by the undo button."""
        count = int(args.get("count", 0) or 0)
        if count <= 0:
            return ActionResult(True, detail="Nothing to delete")
        if not accessibility_allowed():
            return ActionResult(False, problem="Deleting needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="The app with the text is not open any more.")
        import Quartz
        for _ in range(count):
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, KEYS["delete"], down)
                Quartz.CGEventSetFlags(event, 0)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.004)
        return ActionResult(True, detail=f"Deleted {count} characters in {self.front.name}")

    def front_app_name(self) -> str:
        return self.target_name()

    # ---- the dictation target: the app the person is working in ----------------

    def target_name(self) -> str:
        """The app typing goes into ('' if there is none yet). Never Finder, Terminal or Cowork."""
        return self.front.name if self.front.pid and pid_alive(self.front.pid) else ""

    def recent_targets(self, count: int = 3) -> list[str]:
        return [name for name in self.front.recent if name not in NOT_A_TARGET][:count]

    def set_target(self, name: str) -> bool:
        """Type into this app from now on (opening it if needed)."""
        if not name or name in NOT_A_TARGET:
            return False
        windows = self.front.windows_of(name)
        if windows:
            self.front._remember(windows[0]["pid"], name)
            return self.front.bring_forward()
        done = subprocess.run(["open", "-a", name], capture_output=True, text=True, timeout=config.ACTION_TIMEOUT_S)
        return done.returncode == 0 and self.front.wait_until_front(name, LAUNCH_WAIT_S)

    # ---- documents, email boxes and browsers (each reads the screen before and after) ----------

    def _do_new_document(self, args: dict) -> ActionResult:
        """A new blank document in the app being worked in (Word: "Blank Document" on its start screen)."""
        screen = self.screen()
        app = screen.app if screen.app in DOCUMENT_APPS else "Microsoft Word" if "Microsoft Word" in self._apps else "TextEdit"
        if screen.app != app and not self._ensure_open(app):
            return ActionResult(False, problem=f"Couldn't open {app}.")
        screen = self._wait_for(lambda s: s.app == app and (s.place or s.typeable), 6.0)
        if app == "Microsoft Word" and screen.place == "start screen":
            if not self._press_button(screen.pid, "Blank Document"):
                return ActionResult(False, problem="Couldn't find \u201cBlank Document\u201d on Word's start screen.")
        elif not self._key("n", command_key=True).ok:
            return ActionResult(False, problem=f"Couldn't make a new document in {app}.")
        ready = self._wait_for(lambda s: s.place == "document" and s.typeable)
        if not ready.typeable:
            return ActionResult(False, problem=f"The new document didn't open in time ({ready.words()}).")
        return ActionResult(True, detail=ready.words())

    def _press_button(self, pid: int, title: str) -> bool:
        """Press an on-screen button by its name (Accessibility). True if it was found."""
        import ApplicationServices as AS
        from actions.screen import _attr, find
        root = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(root, 1.0)
        window = _attr(root, "AXFocusedWindow")
        button = find(window, lambda e: _attr(e, "AXRole") == "AXButton" and _attr(e, "AXTitle") == title) if window else None
        if button is None:
            return False
        AS.AXUIElementPerformAction(button, "AXPress")   # Word answers "cannot complete" yet does it (measured)
        return True

    def _do_new_email(self, args: dict) -> ActionResult:
        """Exactly one new email in Mail, with the cursor in To."""
        if not self._ensure_open("Mail"):
            return ActionResult(False, problem="Couldn't open Mail.")
        screen = self._wait_for(lambda s: s.app == "Mail" and s.place, 6.0)
        if screen.place == "new email" and not any(value_of(e) for k, e in mail_fields(screen.pid).items() if k != "body"):
            ready = screen   # an empty new email is already open: use it rather than open another
        else:
            if not self._key("n", command_key=True).ok:
                return ActionResult(False, problem="Couldn't start a new email.")
            ready = self._wait_for(lambda s: s.place == "new email" and s.field == "to")
        if ready.field != "to":
            return ActionResult(False, problem=f"The new email didn't open in time ({ready.words()}).")
        return ActionResult(True, detail="New email, cursor in To", undo=self._discard_email)

    def _discard_email(self) -> None:
        """Close the email on screen without saving it (undo for "write an email")."""
        screen = self.screen()
        if screen.app == "Mail" and screen.place == "new email" and self._key("w", command_key=True).ok:
            time.sleep(0.6)
            self._press_button_in_sheet(screen.pid, "Don\u2019t Save")

    def _press_button_in_sheet(self, pid: int, title: str) -> bool:
        import ApplicationServices as AS
        from actions.screen import _attr
        root = AS.AXUIElementCreateApplication(pid)
        for window in _attr(root, "AXWindows") or []:
            for child in _attr(window, "AXChildren") or []:
                if _attr(child, "AXRole") == "AXSheet":
                    for button in _attr(child, "AXChildren") or []:
                        if _attr(button, "AXTitle") == title:
                            AS.AXUIElementPerformAction(button, "AXPress")
                            return True
        return False

    def _do_next_field(self, args: dict) -> ActionResult:
        return self._move_field(+1)

    def _do_previous_field(self, args: dict) -> ActionResult:
        return self._move_field(-1)

    def _move_field(self, step: int) -> ActionResult:
        """Mail: To -> Cc -> Subject -> body ("next box"), and back. Elsewhere: Tab / Shift+Tab."""
        screen = self.screen()
        if screen.app == "Mail" and screen.place == "new email":
            here = screen.field if screen.field in MAIL_ORDER else "to"
            index = MAIL_ORDER.index(here) + step
            if not 0 <= index < len(MAIL_ORDER):
                return ActionResult(True, detail=f"Already in the {'last' if step > 0 else 'first'} box "
                                                 f"({FIELD_NAMES.get(here, here)})")
            return self._focus_mail(MAIL_ORDER[index])
        if not screen.typeable:
            return ActionResult(False, problem=f"There are no boxes to move between ({screen.words()}).")
        pressed = self._key("tab", shift=step < 0)
        if not pressed.ok:
            return pressed
        time.sleep(0.3)
        return ActionResult(True, detail=self.screen().words())

    def _focus_mail(self, name: str) -> ActionResult:
        """Put the cursor in one of the boxes of the new email (measured: setting the focus through
        Accessibility moves Mail's cursor for To, Cc, Subject and the body; Tab in the body types a tab)."""
        import ApplicationServices as AS
        fields = mail_fields(self.front.pid)
        if name not in fields:
            return ActionResult(False, problem=f"The email has no {FIELD_NAMES.get(name, name)} box.")
        AS.AXUIElementSetAttributeValue(fields[name], "AXFocused", True)
        ready = self._wait_for(lambda s: s.field == name, 1.5, 0.1)
        if ready.field != name:
            return ActionResult(False, problem=f"Couldn't move the cursor to {FIELD_NAMES.get(name, name)}.")
        return ActionResult(True, detail=f"Cursor in {'the message' if name == 'body' else FIELD_NAMES[name]}")

    def _do_go_to_field(self, args: dict) -> ActionResult:
        name = (args.get("field", "") or "").strip().lower()
        name = {"message": "body", "body": "body", "text": "body"}.get(name, name)
        screen = self.screen()
        if screen.app != "Mail" or screen.place != "new email":
            return ActionResult(False, problem="There's no email open. Say \u201cwrite an email\u201d first.")
        return self._focus_mail(name)

    def _do_save_document(self, args: dict) -> ActionResult:
        pressed = self._key("s", command_key=True)
        return ActionResult(pressed.ok, detail=f"Saved (Command + S) in {self.front.name}" if pressed.ok else "",
                            problem=pressed.problem)

    def _do_new_tab(self, args: dict) -> ActionResult:
        screen = self.screen()
        app = screen.app if screen.app in BROWSERS else "Google Chrome" if "Google Chrome" in self._apps else config.BROWSER
        if screen.app != app and not self._ensure_open(app):
            return ActionResult(False, problem=f"Couldn't open {app}.")
        if not self._key("t", command_key=True).ok:
            return ActionResult(False, problem="Couldn't open a new tab.")
        ready = self._wait_for(lambda s: s.field == "address", 2.0, 0.1)
        return ActionResult(True, detail="New tab, cursor in the address bar" if ready.field == "address" else "New tab")

    def _do_search_for(self, args: dict) -> ActionResult:
        """Type the words into the browser's address bar (or the search box that has the cursor).
        Not run yet: "hit search" (Return) runs it, so the person can check the words first."""
        query = (args.get("query", "") or "").strip()
        if not query:
            return ActionResult(False, problem="There is nothing to search for.")
        screen = self.screen()
        if screen.app in BROWSERS:
            for _ in range(2):   # measured: Chrome builds what Accessibility sees only when first asked
                if screen.field == "address":
                    break
                self._key("l", command_key=True)   # the address bar of the page on screen
                screen = self._wait_for(lambda s: s.field == "address", 2.5, 0.15)
            if screen.field != "address":
                return ActionResult(False, problem="Couldn't put the cursor in the address bar.")
        elif not screen.search:
            return ActionResult(False, problem=f"There's no search box here ({screen.words()}).")
        typed = self._do_type_text({"text": query, "anyway": True})
        if not typed.ok:
            return typed
        if screen.app in BROWSERS:   # measured: Chrome filled in "Cornell" as a page from its history; keep the words only
            time.sleep(0.2)
            self._key("forward_delete")
        return ActionResult(True, detail=f"Typed \u201c{query}\u201d in {screen.focus or 'the search box'} "
                                         "(say \u201chit search\u201d to run it)")

    def _do_close_app(self, args: dict) -> ActionResult:
        """Quit the app the person was using (no name given)."""
        if not self.front.pid:
            return ActionResult(False, problem="There is no app to close.")
        return self._quit(self.front.pid, self.front.name)

    def _do_quit_app(self, args: dict) -> ActionResult:
        """Quit an app by name ("close Word")."""
        name = args.get("app", "")
        pid = self._running_pid(name)
        if pid is None:
            return ActionResult(False, problem=f"{name} isn't open.")
        return self._quit(pid, name)

    def _running_pid(self, name: str) -> int | None:
        for window in front_windows():
            if window["app"] == name:
                return window["pid"]
        from AppKit import NSWorkspace
        for app in NSWorkspace.sharedWorkspace().runningApplications():
            if app.localizedName() == name and pid_alive(app.processIdentifier()):
                return app.processIdentifier()
        return None

    def _quit(self, pid: int, name: str) -> ActionResult:
        """Quit an app exactly as if the person pressed Command+Q in it (so the app itself
        asks about unsaved work). Measured: asking the app to quit from outside
        (NSRunningApplication.terminate) was ignored by TextEdit."""
        from AppKit import NSRunningApplication
        if name in NEVER_CLOSE or pid == self.front.own_pid:
            return ActionResult(False, problem=f"I don't close {name}.")
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or not pid_alive(pid):
            return ActionResult(False, problem=f"{name} is already closed.")
        bundle = app.bundleIdentifier()
        self.front._remember(pid, name)   # Command+Q must go to this app
        pressed = self._key("q", command_key=True)
        if not pressed.ok:
            return pressed
        end = time.monotonic() + 4.0
        while time.monotonic() < end and pid_alive(pid):
            time.sleep(0.1)

        def undo():
            if bundle:
                subprocess.run(["open", "-b", bundle], capture_output=True, timeout=config.ACTION_TIMEOUT_S)

        if pid_alive(pid):   # e.g. it is asking whether to save
            return ActionResult(True, detail=f"Asked {name} to close (it may be asking about unsaved work)")
        self.front.update()
        return ActionResult(True, detail=f"Closed {name}", undo=undo)

    def _do_open_latest_email(self, args: dict) -> ActionResult:
        """Open the newest email in the inbox in its own window. Nothing is read aloud.
        1. Mail's main window shows the Inbox, and its first message (the newest: the list is
           sorted by date) is selected, through Accessibility. Measured: Mail's own scripting
           ("set selected messages") selects the wrong message with a Gmail account.
        2. That email is opened in its own window. Undo closes that window."""
        ok, output = run_applescript([
            'tell application "Mail"',
            "activate",
            "if (count of message viewers) = 0 then make new message viewer",
            "try",
            "set selected mailboxes of message viewer 1 to {inbox}",
            "end try",
            "end tell"])
        if not ok:
            return ActionResult(False, problem=output)
        time.sleep(0.4)
        selected = _select_first_message()   # "" if Accessibility isn't allowed (then it is only opened)
        ok, output = run_applescript([
            'tell application "Mail"',
            "set n to count of messages of inbox",
            'if n = 0 then error "Can\'t get a message" number -1728',
            "set k to 10",
            "if n < k then set k to n",
            "set m to message 1 of inbox",   # the newest, checked over the first few
            "repeat with i from 2 to k",
            "if (date received of message i of inbox) > (date received of m) then set m to message i of inbox",
            "end repeat",
            "open m",
            "activate",
            "return (subject of m) & linefeed & (sender of m)",
            "end tell"])
        if not ok:
            return ActionResult(False, problem=output)
        subject, _, sender = output.partition("\n")
        name = sender.split("<")[0].strip().strip('"') or "an unknown sender"   # the name, never the address
        subject = subject.strip() or "(no subject)"
        self.front.update()

        def undo():   # close the email's own window (Mail titles it "<subject> — <account>")
            run_applescript(["on run argv", 'tell application "Mail"',
                             "repeat with w in windows",
                             "if (name of w) starts with (item 1 of argv) and (name of w) does not start with \"Inbox\" then",
                             "close w", "exit repeat", "end if", "end repeat", "end tell", "end run"], subject)

        where = "selected in the Inbox and " if selected else ""
        return ActionResult(True, detail=f"Latest email {where}opened: \u201c{subject}\u201d from {name}", undo=undo)

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
