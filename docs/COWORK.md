# Cowork (the hackathon "Recreate" version of the screen)

A Cowork-style screen on top of the unchanged Second Voice engine: your request as
a message (with what was really heard underneath), step cards that tick when done,
permission cards for "Did you mean...?" and risky actions, a learning panel, and a
status strip. It runs in its own Mac window, not in a browser.

Screenshots (made with the fakes): `docs/screenshots/`.
Remake them any time with `.venv/bin/python scripts/screenshots.py` (no browser is opened).

## Run it

    .venv/bin/python -m ui --fake          # try everything with fakes (no models, nothing done on the Mac)
    .venv/bin/python -m ui --mic           # the real thing: LM Studio running with Bonsai, camera + microphone

Close the window or press Ctrl+C in Terminal to stop. `--fresh` starts learning over,
`--no-camera` turns hand answers off, `--profile personal` uses your own 4-phrase profile.
The old screen is still at http://localhost:8765/classic.

## What's in it

| Part | Where |
|---|---|
| The screen | `ui/static/cowork.html` (one offline file) |
| Conversation, cards, steps, test clips | `ui/app.py`, wording in `ui/wording.py` |
| Its own window | `ui/window.py` (WebKit inside the app) |
| Camera -> hand answers + preview | `choose/camera.py` (on only while a request is being handled) |
| New Mac actions | `actions/mac.py` |
| The demo profile (TORGO M04 + everyday phrases + actions) | `profiles/cowork_base.json`, made by `scripts/make_cowork_profile.py` |
| Test clips + TORGO credit | `ui/clips.py` |
| Websites the Brain can choose | `config.SITES` |
| People you email | `profiles/contacts.example.json` (copy to `profiles/contacts.json` and edit) |

## Answering a card

| | Allow / Yes | Don't allow / No | Option 1-4 |
|---|---|---|---|
| Voice | "yes" | "no" | "one" ... "four" |
| Hand | raise RIGHT hand, or thumb up | raise LEFT hand, or thumb down | thumb: left/right hand = column, up/down = row |
| Keyboard | Enter | Esc | 1-4 (Esc = none of these) |

After an action there are 3 seconds to undo it: Esc, "no", or raise your left hand.

## Actions (each one is chosen from a list, plus words filled in)

| Action | Checked how |
|---|---|
| Open any installed app (86 on this Mac) | fake + real (open) |
| Type text into the app you were using | **real, on TextEdit** |
| Scroll up / down that app | **real, on TextEdit** (scroll position read back) |
| Close (quit) that app | **real, on Calculator**, and undo reopened it |
| Open a known website / search Google | fake only (needs Safari/Chrome: not run without your OK) |
| Write an email (to / subject / message), not sent | fake only (needs Mail) |
| Send it (risky: always asks) | fake only; **switched off** until `EMAIL_REALLY_SEND = True` in `config.py` |
| Read the latest email aloud | fake only (needs Mail) |
| Back / forward / arrow keys / Return (TORGO command words) | fake; same key code path as typing |
| Clicking on-screen buttons, replying to email | **hidden** (not reliable enough) |
