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

    run_window("http://localhost:8765")      # blocks until the window is closed
"""

import os
import signal

import objc
from AppKit import (NSAppearance, NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered,
                    NSColor, NSMakeRect, NSMenu, NSMenuItem, NSObject, NSVisualEffectView, NSWindow)
from Foundation import NSURL, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import WKUserContentController, WKWebView, WKWebViewConfiguration

STYLE = 1 | 2 | 4 | 8 | (1 << 15)   # titled, closable, miniaturizable, resizable, full-size content view
TITLEBAR = 28                        # the window's own (draggable) title bar, above the page
MATERIAL_UNDER_WINDOW = 21           # NSVisualEffectMaterialUnderWindowBackground
BEHIND_WINDOW, ACTIVE = 0, 1


def _quit(*_):
    os._exit(0)   # the server, camera and microphone threads all stop with the app


WKScriptMessageHandler = objc.protocolNamed("WKScriptMessageHandler")


class _Bridge(NSObject, protocols=[WKScriptMessageHandler]):
    """Messages from the page: {"glass": true/false}."""
    def initWithWindow_effect_(self, window, effect):
        self = objc.super(_Bridge, self).init()
        self.window, self.effect = window, effect
        return self

    def userContentController_didReceiveScriptMessage_(self, controller, message):
        body = message.body()
        try:
            glass = bool(body["glass"])
        except Exception:
            return
        set_glass(self.window, self.effect, glass)


class _Closer(NSObject):
    def windowWillClose_(self, notification):
        _quit()

    def tick_(self, timer):
        pass   # lets Python notice Ctrl+C while the Mac event loop runs


def set_glass(window, effect, glass: bool) -> None:
    """Glass: see-through window, blurred desktop behind. Plain: an ordinary opaque window."""
    effect.setHidden_(not glass)
    window.setOpaque_(not glass)
    window.setBackgroundColor_(NSColor.clearColor() if glass else NSColor.colorWithSRGBRed_green_blue_alpha_(
        0.11, 0.082, 0.07, 1.0))


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


def run_window(url: str, title: str = "Cowork", width: int = 1380, height: int = 880, glass: bool = True) -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _menus(app)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), STYLE, NSBackingStoreBuffered, False)
    window.setTitle_(title)
    window.setTitleVisibility_(1)              # hidden: the page shows the name
    window.setTitlebarAppearsTransparent_(True)
    window.setMinSize_((900, 640))
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
    run_window.keep = (bridge, closer)    # keep the Objective-C helpers alive
    AppHelper.runEventLoop()
