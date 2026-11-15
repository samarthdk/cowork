# Overnight log

## Morning summary (kept up to date)

- Started 2026-11-14 late evening on branch `cowork`. Work in progress; see the sections below.

## Decisions (made without asking, safest and simplest for the demo)

- SpeakerKit: not downloaded (spec says so). Not wired in.

## Log

### Start
- Mac kept awake with `caffeinate -dimsu`.
- Memory at start: 34% free, about 10 GB of swap already used (LM Studio holds Bonsai; tests only, one heavy job at a time).
- All 18 test files passed at the start (738 checks), last commit b816232.

### Bug 1: answers said as sentences (fixed)
- Cause: while a question was open, only single words were accepted (yes, no, a number, an option's exact words).
- Fix: `choose/reading.py` reads the whole sentence with the 5 rules from the spec. Used for typed and spoken
  answers, so the full window, the mini window and Conference mode all behave the same.
- Safety choices: "right / ok / sure / correct" count as yes only before a comma or full stop ("right click the
  button" is not a yes). A sentence only counts as a new request when it starts with a request word (open, write,
  what...) or closely matches a phrase or command; otherwise the question stays open with a hint.
- Test: `tests/test_sentence_answers.py` (49 checks, including the exact sentence in all three places).

### Bug 2: Recent tasks lost past conversations (fixed)
- Cause: "New task" only cleared the screen; the sidebar listed the current conversation's requests.
- Fix: "New task" saves the conversation (named from its first request, with the time) into
  `profiles/cowork_tasks.json` (gitignored; `fake_cowork_tasks.json` with --fake). Recent tasks lists them newest
  first; clicking one shows it read-only with "Continue this task" and "Back". Empty conversations are not saved.
  `--fresh` moves the tasks file into profiles/backups/ together with the learning.
- Test: `tests/test_tasks.py` (18 checks, including a restart).

### Bug 3: right column opened by itself (fixed)
- The right column (Camera, Progress, Learned) now starts collapsed, and stays as the person leaves it. The camera row
  no longer opens by itself when a question waits for a hand answer: a small camera picture appears inside the
  question card instead. (Saying "show learned" still opens it, since the person asked.)
- Test: added to `tests/test_composer.py`.

### Bug 5: mode button (fixed)
- The button now shows where it takes you: in Computer mode the people icon, "Switch to Conference mode"; in
  Conference mode the computer icon, "Switch to Computer mode". In Conference mode a quiet label above the
  composer says "Conference mode: speaking for you". Test: `tests/test_composer.py`.

### Bug 6: question card text (fixed)
- With speaking off the Yes button shows the real outcome ("Typing into Word", "Opening Mail") or just "Yes";
  never "Saying it aloud".
- Long dashes removed from all app text (cowork, mini and classic pages, app.py, wording.py), e.g. "My guess 1 of 3,
  not from your list", "Watching for your answer. Nothing is recorded." Tests check the page and the source files.

### Change 7: panels 5% more see-through (done)
- Panel fill 0.72 -> 0.67. Checked the worst case (a pure white wallpaper, with the 0.22 base layer): white 8.8,
  light gray 6.1, green 5.5, red 4.5, amber 5.4. The clay accent fell to 4.3, so instead of keeping one area more
  opaque (accent text is used in many places) the accent was made 8% lighter (#F2A385 -> #F3AA8F, 4.6:1, looks the
  same). The red-orange "listening" colour is only used for icons and dots (3.4:1; 3:1 is the rule for those).

### Bug 4: requests inside the app in front (fixed; live testing limited, see below)
- New commands (keyboard shortcuts, only in the right app, else a clear message): Mail: reply, reply all, forward,
  mark unread, delete and archive (risky: always the permission card). Word/Notes/TextEdit: undo that, select all,
  bold, new line, new paragraph. Chrome/Safari: reload, close tab. "Undo" alone stays Cowork's own undo.
- Fallback for anything else (`repair/menu_pick.py`, `do_in_app`): the front app's menu bar and buttons are read
  with Accessibility (36 items in TextEdit in 0.3 s), up to 15 sharing words with the request are shortlisted,
  Bonsai picks one or none (about 1 s). Words in the item's own name count most; a match only through the menu
  path or a similar word ("bigger" -> Zoom) is only a guess and asks "Did you mean...?" first. Risky items
  (send, delete, quit, erase, buy, pay, sign out...) always get the permission card. Nothing fits: "I couldn't
  find a way to do that in <app>", with alike items offered only when they really look alike.
- Real Bonsai on TextEdit's real menus: "show me the fonts" -> Format > Font > Show Fonts (click); "check the
  spelling" -> Show Spelling and Grammar (click); "close everything" -> File > Close All (permission card);
  "make the text bigger" -> Window > Zoom All (asks first, it is only a guess); "fly me to the moon" -> says so.
  Before the fix, "find a word" clicked Edit > Find > Select Line and "make the text bigger" clicked Zoom All
  without asking: fixed and tested.
- Also fixed: after "Don't allow" on a risky request Cowork was sure about, it no longer offers a grid of other
  options (that looked like a way round the "no"). It stops and says "Okay, not allowed. Nothing was done."
- Tests: `tests/test_app_commands.py` (51 checks).
- LIVE TESTING BLOCKED: the screen locked during the night (CGSSessionScreenIsLocked = true). While locked, macOS
  lets no app come to the front and key presses go nowhere, so reply / forward / mark unread / print could not be
  tried live tonight (they failed with "There is no app to send the key to", which is the lock, not the code).
  Opening the latest email (AppleScript) did work while locked. These are on the morning checklist.

### F1: one start command (done)
- `scripts/start.sh` (= `.venv/bin/python -m ui --demo`): checks LM Studio and Bonsai, a 1-second known-answer test
  with thinking off, WhisperKit and TTSKit models, how many demo sentences are ready, the built-in camera, the
  microphone, Accessibility, and Wi-Fi on/off. Each line in plain English with a fix. Needed checks failing: it stops
  and says what to do. Then Cowork starts with real models, microphone and camera on, demo profile.
- Run on this Mac: all 8 checks OK (Bonsai test 2.2 s, 57 demo sentences ready). Cowork started with mic and camera.
- Test: `tests/test_start.py`. Just the checks: `.venv/bin/python -m ui.checks`.

### F2: stop everything (done)
- "stop", "cancel" (and "stop everything", "cancel that"...), typed or said, or Esc twice within a second (full
  window and mini window): speaking stops, typing stops mid-sentence, requests waiting their turn are dropped, the
  open question closes, listening stops. "Stopped." is shown. What was already done stays (Undo is separate).
  "stop listening" still only stops the microphone.
- The both-hands-up gesture was skipped (decision): it would overlap the existing raised-hand yes/no answers.
- Test: `tests/test_stop.py` (11 checks) and keys in `tests/test_composer.py`.

### F3: demo backup (done)
- The 3 demo clips are at the top of Test clips, in order (as before). Cmd+1, Cmd+2, Cmd+3 play them directly
  (only in Cowork's own window, so Word's own Cmd+1 is never taken). Test in `tests/test_composer.py`.

### F4: Results page (done)
- In-app "Results" view (Help > Results, or Cmd+K "Results"). Numbers read from eval/results/torgo_M04.json through
  /api/results (not typed in): WhisperKit alone wrote the exact sentence 8% of the time (78% of words wrong); Cowork
  reached the right meaning 70% (10% at once, 54% after one yes/no, 6% after two); 0% confidently wrong; 0%
  confidently wrong on 57 recordings of words outside the list. One sentence per bar and the honest limits (one
  speaker, 98 held-out recordings, a fixed phrase set, yes/no answers simulated as always correct).
- Chart: two horizontal bars on one axis, WhisperKit in quiet gray, Cowork in the app's accent, values in text colour,
  tooltips on the bars, a screen-reader label. `docs/chart.png` made from the same view
  (scripts/screenshots.py, results_scene). Test: `tests/test_composer.py`.
