"""Cowork's own window: a plain Mac window showing the local page (WebKit, the
same engine as Safari, but inside this app, with no browser around it).

Why not a Safari tab: actions like "type", "scroll" and "close the app" act on
the app the person was using. With the page in its own window, that is never
Cowork itself (see actions/mac.py FrontApp), and Safari stays free for websites.

    run_window("http://localhost:8765")      # blocks until the window is closed
"""

import os
import signal

import objc
from AppKit import (NSApplication, NSApplicationActivationPolicyRegular, NSBackingStoreBuffered, NSMakeRect,
                    NSMenu, NSMenuItem, NSObject, NSWindow)
from Foundation import NSURL, NSURLRequest
from PyObjCTools import AppHelper
from WebKit import WKWebView, WKWebViewConfiguration

STYLE = 1 | 2 | 4 | 8   # titled, closable, miniaturizable, resizable


def _quit(*_):
    os._exit(0)   # the server, camera and microphone threads all stop with the app


class _Closer(NSObject):
    def windowWillClose_(self, notification):
        _quit()

    def tick_(self, timer):
        pass   # lets Python notice Ctrl+C while the Mac event loop runs


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


def run_window(url: str, title: str = "Cowork", width: int = 1380, height: int = 880) -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    _menus(app)
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, width, height), STYLE, NSBackingStoreBuffered, False)
    window.setTitle_(title)
    window.setMinSize_((900, 640))
    window.center()
    web = WKWebView.alloc().initWithFrame_configuration_(window.contentView().bounds(),
                                                         WKWebViewConfiguration.alloc().init())
    web.setAutoresizingMask_(2 | 16)   # follow the window's width and height
    window.setContentView_(web)
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
    AppHelper.runEventLoop()
