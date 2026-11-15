"""Hold M to mute Cowork's microphone, in any app: speak to someone in the room without Cowork hearing.

    key = MuteKey(hold_s=0.3)
    key.event("down", keycode, repeat, now)  -> what to do with that key press

The M key never reaches the app while it is decided: held for HOLD_S or longer it mutes (until it is released);
a quick tap is given back to the app as an ordinary "m" (so typing "m" still works everywhere).
Keys with Command, Control or Option are never touched.
"""

M_KEYCODE = 46
HOLD_S = 0.3


class MuteKey:
    def __init__(self, hold_s: float = HOLD_S):
        self.hold_s = hold_s
        self.down_at = None     # when M went down (None: not held)
        self.muted = False

    def event(self, kind: str, keycode: int, repeat: bool, now: float, modifiers: bool = False) -> list[str]:
        """kind "down" / "up". Returns the actions, in order, from: "pass" (let the key through untouched),
        "swallow" (keep it from the app), "mute", "unmute", "type_m" (give the app a plain "m" after all)."""
        if keycode != M_KEYCODE or (modifiers and self.down_at is None):
            return ["pass"]
        if kind == "down":
            if self.down_at is None and not repeat:
                self.down_at = now
                return ["swallow"]
            # held down (the key repeats while held): mute once it has been held long enough
            if self.down_at is not None and not self.muted and now - self.down_at >= self.hold_s:
                self.muted = True
                return ["swallow", "mute"]
            return ["swallow"]
        # up
        if self.down_at is None:
            return ["pass"]
        held = now - self.down_at
        self.down_at = None
        if self.muted:
            self.muted = False
            return ["swallow", "unmute"]
        if held >= self.hold_s:   # held long enough, but no repeat came: still a hold (nothing to type)
            return ["swallow"]
        return ["swallow", "type_m"]   # a quick tap: the app gets its "m"

    def tick(self, now: float) -> list[str]:
        """Called now and then while M is down (key repeat may be off): mute once held long enough."""
        if self.down_at is not None and not self.muted and now - self.down_at >= self.hold_s:
            self.muted = True
            return ["mute"]
        return []
