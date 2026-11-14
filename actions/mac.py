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
KEYS = {"left": 123, "right": 124, "return": 36, "[": 33, "]": 30, "q": 12, "delete": 51, "l": 37, "n": 45, "t": 17}
COMMAND_KEY = 0x100000  # kCGEventFlagMaskCommand
SCROLL_LINES = 12
NOT_A_TARGET = ("Terminal", "iTerm2", "Finder", "loginwindow", "Python")   # never typed into (Python = Cowork)
# Where the cursor goes when an app is opened by voice (the natural place to start typing):
BROWSERS = ("Google Chrome", "Safari", "Firefox", "Arc", "Microsoft Edge", "Brave Browser")
NEW_ITEM_APPS = ("Mail", "Notes")                     # Command+N: a new message / a new note
DOCUMENT_APPS = ("Microsoft Word", "TextEdit", "Pages")   # Command+N only if no document is open yet
LAUNCH_WAIT_S = 12.0
NEVER_CLOSE = ("Finder", "Terminal", "loginwindow", "Dock", "Python")
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
        if front_windows()[:1] and front_windows()[0]["pid"] == self.pid:
            return True
        # Activate it directly first. ("open -b" sends a "reopen" event: measured, Chrome then shows
        # its "Who's using Chrome?" window, which takes the keystrokes.) "open" only as a fallback.
        app.activateWithOptions_(2)   # NSApplicationActivateIgnoringOtherApps
        end = time.monotonic() + 0.8
        while time.monotonic() < end:
            windows = front_windows()
            if windows and windows[0]["pid"] == self.pid:
                time.sleep(0.15)
                return True
            time.sleep(0.05)
        bundle = app.bundleIdentifier()
        if bundle:
            subprocess.run(["open", "-b", bundle], capture_output=True, timeout=config.ACTION_TIMEOUT_S)
        end = time.monotonic() + 2.0
        while time.monotonic() < end:
            windows = front_windows()
            if windows and windows[0]["pid"] == self.pid:
                time.sleep(0.15)   # let it settle before keys arrive
                return True
            time.sleep(0.05)
        return False

    def stop(self):
        self._stop.set()


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

    def context(self) -> dict:
        self._targets = self._on_screen()
        found = {"app": list(self._apps), "target": list(self._targets), "site": list(config.SITES)}
        if self.contacts:
            found["to"] = list(self.contacts)
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
        had_windows = bool(windows)
        if windows:   # already open: bring it forward without a "reopen" (see FrontApp.bring_forward)
            self.front._remember(windows[0]["pid"], app)
            if not self.front.bring_forward():
                return ActionResult(False, problem=f"Could not bring {app} forward.")
        else:
            done = subprocess.run(["open", "-a", app], capture_output=True, text=True, timeout=config.ACTION_TIMEOUT_S)
            if done.returncode != 0:
                return ActionResult(False, problem=done.stderr.strip() or f"Could not open {app}.")
        detail = f"Opened {app}"
        # It becomes the dictation target, with the cursor where typing naturally starts.
        if app not in NOT_A_TARGET and self.front.wait_until_front(app, LAUNCH_WAIT_S):
            placed = self._place_cursor(app, had_windows) if accessibility_allowed() else ""
            detail += f" · typing goes here{f' ({placed})' if placed else ''}"

        def undo():
            if not was_running:   # only close what this action opened
                run_applescript(["on run argv", "tell application (item 1 of argv) to quit", "end run"], app)

        return ActionResult(True, detail=detail, undo=undo)

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

    def _key(self, key: str, command_key: bool = False, undo_key: str | None = None) -> ActionResult:
        if not accessibility_allowed():
            return ActionResult(False, problem="Pressing keys needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="There is no app to send the key to.")
        import Quartz
        for down in (True, False):
            event = Quartz.CGEventCreateKeyboardEvent(None, KEYS[key], down)
            Quartz.CGEventSetFlags(event, COMMAND_KEY if command_key else 0)   # never inherit a held modifier
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
        if self._last_email is None:
            return ActionResult(False, problem="There is no email to send. Ask me to write one first.")
        allowed = self.contacts.get(config.SEND_ONLY_TO, "")
        if not allowed or getattr(self, "_last_email_to", "").lower() != allowed.lower():
            return ActionResult(False, problem=f"For safety, emails are only sent to '{config.SEND_ONLY_TO}' "
                                               "(SEND_ONLY_TO in config.py). The email is still open in Mail.")
        if not config.EMAIL_REALLY_SEND:
            return ActionResult(False, problem="Sending is switched off (EMAIL_REALLY_SEND in config.py). "
                                               "The email is still open in Mail.")
        ok, output = run_applescript(["on run argv", 'tell application "Mail"',
                                      "set m to first outgoing message whose subject is (item 1 of argv)",
                                      "send m", "end tell", "end run"], self._last_email)
        if not ok:
            return ActionResult(False, problem=output)
        self._last_email = None
        return ActionResult(True, say="Your email was sent.", detail="Sent the email")

    def _do_type_text(self, args: dict) -> ActionResult:
        text = args.get("text", "")
        if not text.strip():
            return ActionResult(False, problem="There is nothing to type.")
        if not accessibility_allowed():
            return ActionResult(False, problem="Typing needs the Accessibility permission. " + ACCESSIBILITY_FIX)
        if not self.front.bring_forward():
            return ActionResult(False, problem="There is no app to type into. Open one first.")
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

    def _tab_to(self, pid: int, role: str, window_title: str = "New Message", most: int = 6) -> bool:
        """Press Tab until the focused element has this role (e.g. Mail: To -> Cc -> Subject -> body).
        Measured: setting the focus through Accessibility did not move Mail's cursor; Tab does."""
        import ApplicationServices as AS
        import Quartz
        app = AS.AXUIElementCreateApplication(pid)
        end = time.monotonic() + 3.0   # wait for the new window to open and take the focus
        while time.monotonic() < end:
            error, window = AS.AXUIElementCopyAttributeValue(app, "AXFocusedWindow", None)
            if error == 0 and AS.AXUIElementCopyAttributeValue(window, "AXTitle", None)[1] == window_title:
                break
            time.sleep(0.1)
        else:
            return False
        time.sleep(0.2)
        for _ in range(most + 1):
            error, focused = AS.AXUIElementCopyAttributeValue(app, "AXFocusedUIElement", None)
            if error == 0 and AS.AXUIElementCopyAttributeValue(focused, "AXRole", None)[1] == role:
                return True
            for down in (True, False):
                event = Quartz.CGEventCreateKeyboardEvent(None, 48, down)
                Quartz.CGEventSetFlags(event, 0)   # a plain Tab (right after Command+N it became Command+Tab)
                Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
            time.sleep(0.25)
        return False

    def _place_cursor(self, app: str, had_windows: bool) -> str:
        """Put the cursor where typing naturally starts. Returns what was done, in words."""
        if app in BROWSERS:
            # Command+T: a new tab with the cursor in its address bar. (Command+L would put the cursor
            # in the address bar of the page already open, and typing would replace that page.)
            return "a new tab, cursor in the address bar" if self._key("t", command_key=True).ok else ""
        if app in NEW_ITEM_APPS:
            what = "a new message" if app == "Mail" else "a new note"
            if not self._key("n", command_key=True).ok:
                return ""
            if app == "Mail":   # a new message starts in "To:"; the person dictates the message itself
                what += ", cursor in the body" if self._tab_to(self.front.pid, "AXWebArea") else ""
            return what
        if app in DOCUMENT_APPS and not had_windows:
            return "a new document" if self._key("n", command_key=True).ok else ""
        return ""

    def _do_close_app(self, args: dict) -> ActionResult:
        """Quit the app the person was using, exactly as if they pressed Command+Q
        (so the app itself asks about unsaved work). Measured: asking the app to
        quit from outside (NSRunningApplication.terminate) was ignored by TextEdit."""
        from AppKit import NSRunningApplication
        name, pid = self.front.name, self.front.pid
        if not pid:
            return ActionResult(False, problem="There is no app to close.")
        if name in NEVER_CLOSE:
            return ActionResult(False, problem=f"I don't close {name}.")
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or not pid_alive(pid):
            return ActionResult(False, problem=f"{name} is already closed.")
        bundle = app.bundleIdentifier()
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
            return ActionResult(True, detail=f"Asked {name} to close (it may be asking about unsaved work)",
                                undo=None)
        return ActionResult(True, detail=f"Closed {name}", undo=undo)

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
