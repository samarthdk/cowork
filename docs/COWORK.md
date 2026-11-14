# Cowork (the hackathon "Recreate" version of the screen)

A Cowork-style screen on top of the unchanged Second Voice engine: your request as
a message (with what was really heard underneath), step cards that tick when done,
permission cards for "Did you mean...?" and risky actions, a learning panel, and a
status strip. It runs in its own Mac window, not in a browser.

Screenshots (made with the fakes): `docs/screenshots/`.
Remake them any time with `.venv/bin/python scripts/screenshots.py` (no browser is opened).

## The dictation demo (for the video)

Before: LM Studio running with Bonsai (server on); Wi-Fi off; Microsoft Word open with a blank
document (click into it once, so Cowork knows where to type). Prepared voice: run
`.venv/bin/python scripts/prepare_voice.py` once (instant if already done).

    cd ~/Desktop/second-voice && .venv/bin/python -m ui --real --dictation --fresh

(`--fresh` forgets what was learned in rehearsals, so clip 2 asks "Did you mean" again.)
Then "Play test clip" and play, in order:

1. "The humidity is overwhelming there": WhisperKit hears "But humanity is overwhelming."; typed at once.
2. "We gathered shells on the beach": heard "We can get a joke on the beach."; card "Did you mean...?";
   answer Yes (Enter, "yes", right hand or thumb up); typed into Word and learned.
3. The same recording again: recognised instantly from what was learned; typed at once.

Checked on 2026-11-14 with the real WhisperKit + Bonsai (clip 2's card appears after about 2 s), and
typing into Word: 0.46 s per sentence, Undo removes it (Backspace). Every sentence in this demo
is a prepared TTSKit sentence.

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
| Camera -> hand answers + preview | `choose/camera.py`: only the built-in "FaceTime HD Camera", by name; on only while a request is handled |
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

Live-tested on 2026-11-14 with the real Bonsai model (LM Studio):

| Action | Checked how |
|---|---|
| Open any installed app (86 on this Mac) | real; clear names work, badly garbled app names often pick the wrong app |
| Type text into the app you were using | **real, on TextEdit and Microsoft Word** |
| Dictation: confirmed sentences typed into Word, Undo deletes them | **real, on Microsoft Word** |
| Scroll up / down that app | **real, on TextEdit** (scroll position read back) |
| Close (quit) that app | **real, on Calculator**, and undo reopened it |
| Open a known website / search Google | **real, in Safari**; **hidden from the demo** (needs the internet) |
| Write an email (to / subject / message), not sent | **real, in Mail**, to your own address; undo closed the draft |
| Send it (risky: always asks) | refused unless the email is to "Me" (`SEND_ONLY_TO`) **and** `EMAIL_REALLY_SEND = True` |
| Read the latest email aloud | works, but Mail has **no account set up**, so it says the inbox is empty |
| Back / forward / arrow keys / Return (TORGO command words) | same key path as typing |
| Clicking on-screen buttons, replying to email | **hidden** (not reliable enough) |

Your address is only in `profiles/contacts.json` (as "Me"), which stays on this Mac and is not in git.

## The voice: which is which

- **TTSKit** speaks every *prepared* sentence: all your phrases, every command's spoken step
  ("Scrolling down.", "Sending your email." ...), "Opening <app>." for the 12 most-used apps,
  and "Opening <website>." for the 14 websites (52 sentences now).
  Make them once before a demo: `.venv/bin/python scripts/prepare_voice.py` (about 4 minutes).
- **The Mac voice ("Reed")** speaks everything else at once: searches, emails, typed text,
  other apps, emails read aloud, "Something else" guesses. Meanwhile TTSKit prepares that
  sentence, so the next time it is TTSKit too. Each card on screen says "voice: TTSKit" or
  "voice: Mac voice (Reed)".
- Measured: the first sound comes about 0.01 s after Cowork decides what to do (both voices).
  From request to first sound: instant for learned / word-for-word requests, about 1.0-1.6 s
  when the Brain thinks (plus about 0.3 s of speech recognition for spoken requests).
- Why not TTSKit for everything: it can't stay loaded between sentences with the tools on this
  Mac (whisperkit-cli has no speech server; a helper would need a newer Xcode, several GB), and
  every fresh start costs about 2 s before the first sound (4-5 s for the whole sentence).

## Not done: sound-alike app names

"open male" -> Mail when the Brain is unsure was left out. Doing it well means sending the
app list with each request (so it can be narrowed to apps that sound like what was heard), which
changes how the Brain's instructions are cached and would make every request about 3-6 s slower
unless the engine's prompt is changed. The engine was kept exactly as it is.
