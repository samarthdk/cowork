# Progress report

## Update 2026-11-15: overnight build, ready to publish

Branch `cowork`, merged into `main` on this Mac (not pushed). **All 26 test files pass** (also in a fresh clone,
where the checks that need the TORGO recordings are skipped):

    for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

| | What it does | Where |
|---|---|---|
| Answers as sentences | "No, I meant to say open Word" = No, then opens Word; "yes, then open Mail"; "the second one"; a clear new request drops the question | `choose/reading.py` |
| Recent tasks | "New task" saves the conversation; read-only view, continue; kept across restarts; `--fresh` clears | `ui/app.py` |
| Acting in the app in front | reply, reply all, forward, mark unread, delete/archive (risky), undo, select all, bold, new line/paragraph, reload, close tab; anything else through the app's own menus and buttons, Bonsai picks one or none | `actions/mac.py`, `repair/menu_pick.py` |
| One start command | `scripts/start.sh`: plain-English checks with fixes, then the demo | `ui/checks.py` |
| Stop everything | "stop", "cancel", Esc twice | `ui/app.py` |
| Demo backup | Cmd+1/2/3 play demo clips 1-3 | `ui/static/cowork.html` |
| Results view | TORGO M04 from `eval/results/torgo_M04.json`; `docs/chart.png` | Help > Results |
| Teach me | say a phrase 3 times, each saying learned | Your voice > Phrases |
| Dictation polish | spoken punctuation, "delete that", numbers/times/dates checked first, addresses kept exact | `ui/dictation.py` |
| Tough-judge fixes | "open <app>" recognised directly; kind refusals (settings, deleting files, buying); mumbling; screen text never instructions | `repair/learning.py`, `ui/screen_rules.py`, `repair/prompt.py` |
| UI | mode button shows where it goes; right column starts closed, camera picture in the card; panels 5% more see-through; no long dashes | `ui/static/cowork.html` |
| Voice | TTSKit only, one whole sentence per generation (no stitching), speaking off by default in Computer mode | `tts/` |
| GitHub | README for judges, MIT LICENSE, requirements.txt, STATUS.md removed, personal-data scan | repo root |

Not tested live (the screen was locked all night): anything that presses keys in another app (reply, forward,
typing into Word, print). Checklist in `docs/MORNING.md`. Full log: `docs/OVERNIGHT_LOG.md`.

---

## Update 2026-11-14: Cowork branch, round D ("look before acting", the mini window)

Branch `cowork`. **All 15 test files pass** (620 checks):

    for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

### What is new, in plain English

| | What it does | Where |
|---|---|---|
| Look before acting | Before and after every action Cowork reads the screen (Accessibility: which app, what it shows, what has the cursor), 1-8 ms a read. Shown in Progress ("Word open, start screen", "Blank document ready") and told to Bonsai with each request. | `actions/screen.py` |
| Never type in the wrong place | Typing only when a document, text box or email box has the cursor. Word's start screen offers "Blank document / Open a recent file / Something else"; a search box asks first. | `ui/app.py` (`_Targeted`) |
| Commands vs. typing vs. questions | Commands in any wording ("write an email", "new mail", "compose"...). Typing only for "type ..." or dictation into a box. Questions are answered aloud in 1-2 sentences, saying plainly that it is offline; never typed. | `ui/screen_rules.py`, `repair/answer.py` |
| Mail box by box | "open Mail" only opens Mail (bug fixed: Mail reopened last time's draft and Cowork also pressed Command+N). "write an email" = one email, cursor in To; "next box" / "back" / "go to subject"; spoken addresses ("sam at gmail dot com") or a contact's name ("me") are confirmed before typing; "send" asks first and only ever sends to Me with `EMAIL_REALLY_SEND` on (it is off). | `actions/mac.py`, `ui/spoken.py` |
| Browsers | "new tab", "search for ..." (typed into the address bar), "hit search". | `actions/mac.py` |
| Flicker fixed | The camera sent hand progress 15-30 times a second; each redraw replayed the card's slide-in. Now only changes are sent and the card is drawn once. | `tests/test_no_flicker.py` |
| "Something else" | Guesses use the words heard and the screen (as background only), and can be questions. | `repair/guess.py` |
| Mini window | When Cowork opens or brings forward an app, it shrinks to a small floating window in the top right corner that never takes the keyboard. It listens hands-free, says questions aloud, takes thumbs / voice / Enter, Esc, 1-4. "stop listening", Esc or a click stops listening; "show Cowork" or closing the last app it opened brings back the full window. | `ui/window.py`, `ui/static/mini.html` |

Pictures: `docs/screenshots/12`-`17`.

### Live results (real WhisperKit, Bonsai, Word, Mail, Chrome; requests made with the Mac voice and fed in)

| Flow | Result |
|---|---|
| open Microsoft Word -> new blank document -> a sentence | Works: the sentence is in a new blank document, nothing in the search box |
| open Mail -> write an email -> "me" -> next box x2 -> subject -> next box -> message -> send -> close Mail | Works: one window, no drafts; send asked first, then refused (`EMAIL_REALLY_SEND` off); Cowork back to full size |
| open Mail -> open Microsoft Word -> what's the weather in Boston? | Works: answered aloud, nothing typed, no flicker |
| open Chrome -> search for Cornell -> hit search | Works: a Google search for Cornell |

Response times: learned commands 0.3-0.5 s; with Bonsai 1.5-3 s; the first request after
learning something 4-6 s (Bonsai re-reads its instructions); a question about 6-7 s in all.

### Measured and decided against
- **Screenshot + Bonsai vision** as a fallback: 4.3-4.8 s and about +300 MB per new picture,
  wrong on both test pictures, and it needs the Screen Recording permission. Not used.

### Known problems
- **Hands-free hears the room.** With hands-free listening on, other people's speech becomes
  requests (seen live). "Did you mean...?" stops it doing anything unasked, but it keeps asking.
- Confirming a misheard request teaches it (e.g. "Open" -> new blank document).
- WhisperKit mishears spelled-out addresses; saying a contact's name ("me") is reliable.
- The send check saves the email to Drafts (Mail shows names, not addresses, on its chips),
  so a refused email stays in Drafts. "close Mail" also lets Mail keep an unsent email as a draft.
- Dictated sentences sometimes get a "Did you mean: Type ...?" check first.

### Test-only switches
`COWORK_TEST_AUDIO=1` (feed recordings in), `COWORK_TEST_SILENT_MIC=1` (the room is not heard),
`--work-profile PATH` (learn into a throwaway file). All off by default.

---

## Earlier report (written 2026-11-12, `fable-build` branch)

Where the `fable-build` branch stands against `docs/BUILD_BRIEF.md`, in plain English.
Based on the git log, the code, `docs/DECISIONS.md`, `docs/STATUS.md`, and a fresh run of
all automated checks.

**Note:** `docs/STATUS.md` is out of date. It lists "evidence rule" and "second opinion"
as next steps, but both are finished and committed. Trust this file over it.

---

## Health check right now

- Branch `fable-build`, nothing uncommitted.
- **All 456 automated checks pass** (12 test files, last run on 2026-11-12).
  To run them yourself:

      for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

- The previous session's unfinished "listening again" code now has its own checks
  (`tests/test_listen_again.py`, 41 checks).

## A correction about the M04 evaluation

The main M04 evaluation **did finish** (on Nov 11 at 9:33 am) and its results are saved in
`eval/results/torgo_M04.json`. What was cut off was a later experiment called
"listening again" (described below). The M04 numbers were measured **before** that
feature existed, so they don't include it.

### The M04 numbers (demo speaker, 98 held-out test clips)

| | Result |
|---|---|
| Raw WhisperKit gets the exact sentence | **8%** |
| Raw WhisperKit, nearest matching phrase | **14%** |
| WhisperKit word error rate | **78%** (most words wrong) |
| Second Voice reaches the right answer | **70%** |
| ...of which answered instantly, no question | 10% |
| ...after one yes/no question | 54% |
| ...after two questions | 6% |
| Not found (person would use "Something else" or try again) | 30% |
| Confidently WRONG | **0%** |
| Words outside the profile answered confidently wrong | **0%** (57 clips) |
| Time: WhisperKit / the Brain (median) | 0.16 s / 1.1 s |

In short: on a speaker whose speech WhisperKit almost never gets right, Second Voice reaches the
right meaning 7 times in 10, usually with one yes/no question, and it never confidently does
the wrong thing. That is the headline "before/after" number. No chart has been made yet.

(The saved M05 results file is older than the "high needs evidence" rule, so its 8% "wrong"
figure is from before the fix. The fix was measured separately and brought that to 0%.)

---

## Done and working

1. **The Brain** (understanding what was meant). Chooses only from the person's phrases and
   commands; answers in 0.8-1.5 s; real app names only; "Did you mean...?"; options grid;
   "Something else" free guess (up to 3 tries); learning from every confirmation; fast path
   for things it has heard before; undo; 3-second correction window; safe vs. risky commands;
   personal vocabulary.
2. **"High" needs evidence.** The model's "I'm sure" only counts if the heard words back it
   up. This took confident mistakes from about 7% to 0% on test speaker M05.
3. **Second opinion after "no".** The Brain is asked again with the rejected answer ruled out.
4. **Speech recognition (WhisperKit)** through a local server: fast (0.2-0.4 s per clip),
   cleans out markers like `[BLANK_AUDIO]`, gently boosts quiet speech. Tested on real audio.
5. **Microphone listening**: a 1.5 s pause ends a request; a "listen now" trigger; the mic
   is muted while the Mac speaks. Verified once on real hardware (it correctly heard
   "Please call my daughter").
6. **Voice output (TTSKit)**: known phrases are made in advance so they play instantly;
   new sentences are made live (slow, 6-11 s); macOS `say` as backup.
7. **One answer system**: speech, hand signals, keyboard and the screen all produce the
   same yes/no/grid answers. One conversation engine (`engine/session.py`) runs the whole flow.
8. **Hand gestures**: detector with hold-to-confirm and "when unsure, select nothing"
   (58 checks). Works on its own with the camera (`tests/hand_test.py`).
9. **TORGO data**: downloaded, every clip transcribed, demo speaker M04 chosen,
   enrollment and test clips kept separate.
10. **Evaluation runner**, and the M04 and M05 results above.
11. **Memory and offline measurement tool** (`sysinfo.py`).
12. **Keyboard demo** to try the Brain by typing:

        .venv/bin/python scripts/demo_text.py

    (Needs LM Studio running with Bonsai loaded, because `config.py` has `LLM = "real"`.)

## Half-done

1. **"Listening again" (the unfinished last piece of work).** The idea: when the Brain is
   unsure, ask WhisperKit to listen to the same recording again, this time hinting it with
   the Brain's guess. If the guess comes back clearly, speak it without asking.
   - Measured on M04 already: it approved 157 of 171 right guesses and **0 of 166 wrong ones**.
     That could cut down on the questions a lot.
   - A second use: after a "no", look through all phrases for one that fits the sound.
     Right 62 times out of 63 when it finds one, but it sometimes "hears" a phrase that wasn't
     said (4 in 60), so it is only ever offered, never acted on.
   - Code is written (`repair/confirm.py`), plugged into the conversation engine, switched on in
     `config.py`, and now checked (41 checks, with a fake recording). The app passes it the
     microphone recording. **Still to do:** try it with real WhisperKit, and re-run the full M04
     evaluation with it on (a long job).
2. **Acting on the Mac** (open app, scroll, click on-screen buttons, read/reply email). Code is
   written with plain-English permission messages. Only "open app" has been checked. The rest
   needs you to grant Accessibility permission first (see below). Email replies are prepared
   but not sent, on purpose.
3. **The app's screen** (`ui/`). Built and working with the fakes: what was heard, what was
   understood and how sure, why it's asking, the yes/no buttons and 2x2 grid that match the hand
   signals, the correction window, the learning feed, fast path, times per step, tokens/second,
   memory against the 3.5 GB budget, and the offline indicator. 55 checks. **Not yet tried with
   the real Brain, WhisperKit, voice or microphone.**
   Try it:  `.venv/bin/python -m ui --fake`  (Safari opens at http://localhost:8765)
4. **The voice loop end to end.** The app now joins mic, WhisperKit, Brain and voice
   (`--mic`), but this has only been checked with fakes, not with a real microphone.
5. **Memory measurement.** The tool exists, but the brief's "all models running together" number
   hasn't been taken. LM Studio was holding 4.7 GB after the long evaluations, so it needs a
   clean reload first.
6. **Evaluation write-up.** Numbers exist; the chart for slides (`eval/chart.py`) and the
   word-error-rate table have not been made.

## Not started

1. **Camera feeding gestures into the app** (the detector works alone; the app is ready to
   receive its signals and show the hold-to-confirm ring, but they aren't connected yet).
   **This is now the biggest missing piece for the demo.**
2. **One start command** with checks (LM Studio running, models present, thinking off, Wi-Fi off).
3. **Demo mode** following the story, and the **backup** that uses recorded clips if the mic fails.
4. **README.md for judges**, **CLAUDE.md update**, **manual test checklist**, **docs/SUMMARY.md**.
5. **Parkinson's baseline** from MDVR-KCL (optional in the brief).
6. **Stretch goals**: head gestures, SpeakerKit (ignore other voices), energy per request.

## Things only you can do

- **Accessibility permission for Terminal**: System Settings > Privacy & Security >
  Accessibility, turn on Terminal. Needed for scrolling, key presses and clicking buttons.
- **Automation > Terminal > Mail**: approve when macOS asks (for reading/replying to email).
- Camera and microphone are already allowed.

## Suggested order from here (before Saturday)

1. Try the app with the real Brain (LM Studio), then with the microphone and WhisperKit.
2. Connect the camera's hand signals to the app.
3. Decide whether to re-run M04 with "listening again" on (long job, ask first).
4. One start command + demo mode + recorded-clip backup.
5. Chart of the M04 results for the slides.
6. Grant permissions, then test Mac actions by hand.
7. README, checklist, summary.
