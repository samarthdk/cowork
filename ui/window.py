"""Cowork's own window: a plain Mac window showing the local page (WebKit, the
same engine as Safari, but inside this app, with no browser around it).

Why not a Safari tab: actions like "type", "scroll" and "close the app" act on
the app the person was using. With the page in its own window, that is never
Cowork itself (see actions/mac.py FrontApp), and Safari stays free for websites.

Glass: the window is see-through and macOS blurs whatever is behind it (the
desktop wallpaper, usually), exactly like Apple's own apps (NSVisualEffectView,
"behind window"). The page itself has a transparent background; its panels carry
a dark tint so text stays readable. Settings > "Glass / Plain" turns the blur off
(the page tells the window through a small message handler, "cowork").

    run_window("http://localhost:8765", core)   # blocks until the window is closed

The mini window (like your own video shrinking to a corner while you share your
screen in a call): when a command brings another app forward, the full window is
hidden and a small floating panel shows in the top right corner instead
(ui/static/mini.html). It is a "non-activating" panel: clicking it, or it
appearing, never takes the keyboard from the app being used, so typing still
lands in Mail or Word. While a question is open in it, Enter/Esc/Y/N/1-4 answer
it (caught before they reach the app; see App.mini_key); at other times keys
pass straight through, and Esc also stops hands-free listening.
"""

import os
import signal

import objc
from AppKit import (NSAppearance, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSColor, NSMakeRect, NSMenu, NSMenuItem, NSObject, NSPanel, NSScreen, NSVisualEffectView, NSWindow)
from Foundation import NSURL, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

STYLE = 1 | 2 | 4 | 8 | (1 << 15)   # titled, closable, miniaturizable, resizable, full-size content view
TITLEBAR = 28                        # the window's own (draggable) title bar, above the page
MATERIAL_UNDER_WINDOW = 21           # NSVisualEffectMaterialUnderWindowBackground
BEHIND_WINDOW, ACTIVE = 0, 1
MINI_WIDTH, MINI_HEIGHT, MINI_MARGIN = 360, 96, 16
NONACTIVATING_PANEL = 1 << 7          # NSWindowStyleMaskNonactivatingPanel: never takes the keyboard
FLOATING_LEVEL = 3                    # NSFloatingWindowLevel: above ordinary windows
ALL_SPACES = (1 << 0) | (1 << 8)      # every desktop, and over full-screen apps


def _quit(*_):
    os._exit(0)   # the server, camera and microphone threads all stop with the app


WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")


class _Bridge(NSObject, protocols=[WKScriptMessageHandler]):
    """Messages from the pages: {"glass": true/false} (full window), {"mini_height": n} (mini window)."""
    def initWithWindow_effect_(self, window, effect):
        self = objc.super(_Bridge, self).init()
        self.window, self.effect, self.mini = window, effect, None
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        try:
            if "mini_height" in body:
                if self.mini is not None:
                    place_mini(self.mini, float(body["mini_height"]))
                return
            glass = bool(body["glass"])
        except Exception:
            return
        set_glass(self.window, self.effect, glass)


class _FirstClickWebView(WKWebView):
    """A click on the mini window works the first time, without making Cowork the active app."""
    def acceptsFirstMouse_(self, event):
        return True


def place_mini(panel, height: float | None = None) -> None:
    """Keep the mini window in the top right corner of the main screen, as tall as its content."""
    area = NSScreen.mainScreen().visibleFrame()
    height = max(MINI_HEIGHT, min(height or panel.frame().size.height, 520))
    x = area.origin.x + area.size.width - MINI_WIDTH - MINI_MARGIN
    y = area.origin.y + area.size.height - height - MINI_MARGIN
    panel.setFrame_display_(NSMakeRect(x, y, MINI_WIDTH, height), True)


def make_mini(url: str, controller):
    """The small floating window: non-activating, on every desktop, see-through."""
    config = WKWebViewConfiguration.alloc().init()
    config.setUserContentController_(controller)
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, MINI_WIDTH, MINI_HEIGHT), NONACTIVATING_PANEL, NSBackingStoreBuffered, False)
    panel.setLevel_(FLOATING_LEVEL)
    panel.setCollectionBehavior_(ALL_SPACES)
    panel.setFloatingPanel_(True)
    panel.setHidesOnDeactivate_(False)      # stays while another app is in front (that is the point)
    panel.setBecomesKeyOnlyIfNeeded_(True)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setHasShadow_(True)
    panel.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
    web = _FirstClickWebView.alloc().initWithFrame_configuration_(NSMakeRect(0, 0, MINI_WIDTH, MINI_HEIGHT), config)
    web.setAutoresizingMask_(2 | 16)
    web.setValue_forKey_(False, "drawsBackground")
    panel.setContentView_(web)
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url + "/mini")))
    place_mini(panel)
    return panel


def watch_keys(core):
    """Keys pressed in any app while the mini window shows go to core.mini_key first; a key it
    uses (answering an open question) is kept from the app. Needs the Accessibility permission
    (already needed for typing). Returns the tap, or None if macOS doesn't allow it."""
    import time as _time

    import Quartz

    from ui.mute_key import MuteKey
    own = os.getpid()
    holder = {}
    mute = MuteKey()
    MINE = 0x5345434F   # marks the "m" Cowork gives back after a quick tap (so it isn't caught again)

    def act(actions, event):
        for action in actions:
            if action in ("mute", "unmute"):
                core.hold_mute(action == "mute")
            elif action == "type_m" and holder.get("m_down") is not None:
                for copy in (holder["m_down"], Quartz.CGEventCreateCopy(event)):   # the quick tap, given back
                    Quartz.CGEventSetIntegerValueField(copy, Quartz.kCGEventSourceUserData, MINE)
                    Quartz.CGEventPost(Quartz.kCGSessionEventTap, copy)

    def on_key(proxy, kind, event, refcon):
        if kind in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(holder["tap"], True)
            return event
        try:
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == own \
                    or Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == MINE:
                return event   # Cowork's own typing, or the "m" it gave back
            flags = Quartz.CGEventGetFlags(event) & (Quartz.kCGEventFlagMaskCommand | Quartz.kCGEventFlagMaskControl
                                                     | Quartz.kCGEventFlagMaskAlternate)
            code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
            repeat = bool(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat))
            actions = mute.event("down" if kind == Quartz.kCGEventKeyDown else "up", code, repeat, _time.monotonic(),
                                 modifiers=bool(flags))
            if kind == Quartz.kCGEventKeyDown and actions == ["swallow"] and not repeat and not mute.muted:
                holder["m_down"] = Quartz.CGEventCreateCopy(event)   # kept, in case it is only a quick tap
            if "swallow" in actions:
                act(actions, event)
                return None    # hold M: muting Cowork (or deciding): the app doesn't get it
            if kind != Quartz.kCGEventKeyDown:
                return event
            if not flags and core.mini_key(code):
                return None    # it answered the question: the app doesn't get it
        except Exception:
            pass
        return event

    def tick():   # key repeat may be off: mute once M has been held long enough anyway
        actions = mute.tick(_time.monotonic())
        if actions:
            core.hold_mute(True)

    holder["tick"] = tick
    tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                  Quartz.kCGEventTapOptionDefault,
                                  Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp),
                                  on_key, None)
    if tap is None:
        return None
    holder["tap"] = tap
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetMain(), source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    return tap, source, on_key, tick


class _Closer(NSObject):
    def windowWillClose_(self, notification):
        _quit()

    def tick_(self, timer):
        keys = getattr(run_window, "keys_tick", None)   # hold M: mute once held long enough
        if keys is not None:
            keys()


def set_glass(window, effect, glass: bool) -> None:
    """Glass: see-through window, blurred desktop behind. Plain: an ordinary opaque window."""
    effect.setHidden_(not glass)
    window.setOpaque_(not glass)
    window.setBackgroundColor_(NSColor.clearColor() if glass else NSColor.colorWithSRGBRed_green_blue_alpha_(
        0.078, 0.078, 0.086, 1.0))


def _menus(app) -> None:
    """Quit, plus Edit (so copy and paste work in the text boxes)."""
    bar = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    bar.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit Cowork", "terminate:", "q")
    app_item.setSubmenu_(app_menu)
    edit_item = NSMenuItem.alloc().init()
    bar.addItem_(edit_item)
    edit = NSMenu.alloc().initWithTitle_("Edit")
    for title, action, key in (("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"),
                               ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")):
        edit.addItemWithTitle_action_keyEquivalent_(title, action, key)
    edit_item.setSubmenu_(edit)
    app.setMainMenu_(bar)


def run_window(url: str, core=None, title: str = "Cowork", width: int = 1120, height: int = 740,
               glass: bool = True) -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _menus(app)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), STYLE, NSBackingStoreBuffered, False)
    window.setTitle_(title)
    window.setTitleVisibility_(1)              # hidden: the page shows the name
    window.setTitlebarAppearsTransparent_(True)
    window.setMinSize_((820, 560))
    window.center()
    window.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))

    # The blur: macOS draws the desktop behind the window, blurred (done by the system, not the page).
    effect = NSVisualEffectView.alloc().initWithFrame_(window.contentView().bounds())
    effect.setMaterial_(MATERIAL_UNDER_WINDOW)
    effect.setBlendingMode_(BEHIND_WINDOW)
    effect.setState_(ACTIVE)
    effect.setAutoresizingMask_(2 | 16)
    window.setContentView_(effect)

    config = WKWebViewConfiguration.alloc().init()
    controller = WKUserContentController.alloc().init()
    config.setUserContentController_(controller)
    bounds = effect.bounds()
    web = WKWebView.alloc().initWithFrame_configuration_(
        NSMakeRect(0, 0, bounds.size.width, bounds.size.height - TITLEBAR), config)
    web.setAutoresizingMask_(2 | 16)   # follow the window's width and height; the title bar stays draggable
    web.setValue_forKey_(False, "drawsBackground")   # see-through, so the blur shows
    effect.addSubview_(web)
    if os.environ.get("COWORK_PERF"):   # test switch: count frames drawn and print the rate every 5 s
        from WebKit import WKUserScript
        counter = ("window.__frames=0;(function f(){window.__frames++;requestAnimationFrame(f)})();"
                   "setInterval(function(){window.__fps=window.__frames/5;window.__frames=0},5000);")
        controller.addUserScript_(WKUserScript.alloc().initWithSource_injectionTime_forMainFrameOnly_(counter, 1, True))

        def report():
            web.evaluateJavaScript_completionHandler_(
                "window.__fps", lambda fps, error: print(f"PERF frames per second: {fps}", flush=True))
            AppHelper.callLater(5.0, report)
        AppHelper.callLater(8.0, report)
    bridge = _Bridge.alloc().initWithWindow_effect_(window, effect)
    controller.addScriptMessageHandler_name_(bridge, "cowork")
    keys = None
    if core is not None:   # the mini window, and the switch between the two
        mini_controller = WKUserContentController.alloc().init()
        mini_controller.addScriptMessageHandler_name_(bridge, "cowork")
        bridge.mini = make_mini(url, mini_controller)

        def switch(mode):
            def apply():
                if mode == "mini":
                    window.orderOut_(None)                  # hidden, not closed (closing quits Cowork)
                    place_mini(bridge.mini)
                    bridge.mini.orderFrontRegardless()      # shown without taking the keyboard
                else:
                    bridge.mini.orderOut_(None)
                    window.makeKeyAndOrderFront_(None)
                    app.activateIgnoringOtherApps_(True)
            AppHelper.callAfter(apply)

        core.window_hook = switch
        keys = watch_keys(core)
        if keys is not None:
            run_window.keys_tick = keys[3]
        if keys is None:
            print("Mini window: keys can't answer its questions (macOS didn't allow listening to keys); "
                  "thumbs, voice and clicks still work.")
    set_glass(window, effect, glass)
    web.loadRequest_(NSURLRequest.requestWithURL_(NSURL.URLWithString_(url)))

    closer = _Closer.alloc().init()
    window.setDelegate_(closer)
    window.makeKeyAndOrderFront_(None)
    window.makeFirstResponder_(web)
    app.activateIgnoringOtherApps_(True)
    objc.setVerbose(False)
    from Foundation import NSTimer
    signal.signal(signal.SIGINT, _quit)   # Ctrl+C in Terminal stops it
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.25, closer, "tick:", None, True)
    run_window.keep = (bridge, closer, keys)    # keep the Objective-C helpers alive
    AppHelper.runEventLoop()
