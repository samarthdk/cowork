# Progress report (written 2026-11-12)

Where the `fable-build` branch stands against `docs/BUILD_BRIEF.md`, in plain English.
Based on the git log, the code, `docs/DECISIONS.md`, `docs/STATUS.md`, and a fresh run of
all automated checks.

**Note:** `docs/STATUS.md` is out of date. It lists "evidence rule" and "second opinion"
as next steps, but both are finished and committed. Trust this file over it.

---

## Health check right now

- Branch `fable-build`, nothing uncommitted.
- **All 360 automated checks pass** (10 test files, run on 2026-11-12).
  To run them yourself:

      for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

- The last commit ("WIP: save Fable's unfinished work") was a save point made when
  the previous session ran out. Its new code has **no checks of its own yet** (see "Half-done").

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
   - Code is written (`repair/confirm.py`) and plugged into the conversation engine, and it is
     switched on in `config.py`. **But:** it has no automated checks, nothing in the app gives it
     audio yet (so it never actually runs), and the full M04 evaluation has not been re-run with it.
2. **Acting on the Mac** (open app, scroll, click on-screen buttons, read/reply email). Code is
   written with plain-English permission messages. Only "open app" has been checked. The rest
   needs you to grant Accessibility permission first (see below). Email replies are prepared
   but not sent, on purpose.
3. **The voice loop end to end.** All the pieces exist (mic, WhisperKit, Brain, voice out), but
   there is no single program that joins them into a live conversation yet. That job belongs to
   the app (next section).
4. **Memory measurement.** The tool exists, but the brief's "all models running together" number
   hasn't been taken. LM Studio was holding 4.7 GB after the long evaluations, so it needs a
   clean reload first.
5. **Evaluation write-up.** Numbers exist; the chart for slides (`eval/chart.py`) and the
   word-error-rate table have not been made.

## Not started

1. **The app's screen** (brief item 4): the FastAPI web page with transcript, confidence,
   2x2 grid, learning feed, metrics and offline indicator. The `ui/` folder doesn't exist yet.
   **This is the biggest missing piece for the demo.**
2. **Camera feeding gestures into the app** (the detector works alone, but isn't connected).
3. **One start command** with checks (LM Studio running, models present, thinking off, Wi-Fi off).
4. **Demo mode** following the story, and the **backup** that uses recorded clips if the mic fails.
5. **README.md for judges**, **CLAUDE.md update**, **manual test checklist**, **docs/SUMMARY.md**.
6. **Parkinson's baseline** from MDVR-KCL (optional in the brief).
7. **Stretch goals**: head gestures, SpeakerKit (ignore other voices), energy per request.

## Things only you can do

- **Accessibility permission for Terminal**: System Settings > Privacy & Security >
  Accessibility, turn on Terminal. Needed for scrolling, key presses and clicking buttons.
- **Automation > Terminal > Mail**: approve when macOS asks (for reading/replying to email).
- Camera and microphone are already allowed.

## Suggested order from here (before Saturday)

1. Add checks for "listening again" (quick, uses fakes), then decide whether to re-run M04
   with it on. That's a long job (WhisperKit + Bonsai on every clip), so ask first.
2. Build the app screen and connect mic, Brain, voice and camera into it. This is the demo.
3. One start command + demo mode + recorded-clip backup.
4. Chart of the M04 results for the slides.
5. Grant permissions, then test Mac actions by hand.
6. README, checklist, summary.
