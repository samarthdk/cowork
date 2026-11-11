# Second Voice: Full Build Brief

(Saved from the conversation on 2026-11-11 so the work can be resumed from the files alone.
Progress against this brief is tracked in `docs/STATUS.md`; decisions in `docs/DECISIONS.md`.)

Take your time with this. It is a long, multi-part job and I would rather you go slowly and get it right than rush. I am going to let you work through all of it without stopping, then review everything afterwards.

## Who I am and how to work with me

- I am not an engineer. I am a Cornell MILR (HR) master's student building this for my first hackathon. I rely on you to write and run the code.
- I have already delegated the decisions in this brief to you. Do NOT stop to ask for approval between steps. When something is ambiguous, pick the most sensible option, write it down in `docs/DECISIONS.md` (what you chose and why), and keep going.
- Only stop if you are truly blocked (for example, something requires my physical action, like granting a macOS permission). If so, finish everything else first and list what you need from me in the final summary.
- You are smarter than my instructions. Treat everything below as goals and constraints, not step-by-step orders. If you see a better way to reach a goal, use it and explain why in `docs/DECISIONS.md`.

## Before you start

1. Read `CLAUDE.md`, the full `git log`, and all existing code and tests. Understand what already exists before changing anything.
2. Create and work on a new git branch called `fable-build`, so `main` stays safe. Commit in small, working milestones with clear messages. Never leave the branch in a broken state between commits.
3. Run all existing tests first and note their status.

## The project

**Second Voice** is an offline assistant for people whose speech is hard to understand (Parkinson's, ALS, stroke, cerebral palsy). It listens to them, works out what they meant, and then either speaks a clear sentence for them or acts on the Mac for them. When unsure, it asks, and the person can answer with very simple movements (raising a hand, a thumb up or down) instead of speech. It learns each person's speech over time.

The pitch: "Speech technology was built for typical voices. Second Voice learns yours, on your own laptop, with no internet and no data leaving the room." My grandfather had Parkinson's; that is the personal motivation.

### The event
- Cornell "Own Your AI" Local AI Hackathon, Gates Hall. Building starts Saturday Nov 14, 12:30 pm; submissions due Sunday Nov 15, noon; live demos for the top 10 after that.
- The app must run entirely offline in the demo. Judges are Cornell professors and engineers from the sponsors.
- Required sponsor technology: **Bonsai 2** language models (PrismML), the **Argmax open-source SDK** (WhisperKit speech recognition, TTSKit speech synthesis, SpeakerKit diarization), and **LM Studio**.
- Prize tracks include 1st/2nd/3rd overall, Best Accessibility (the richest track, with an interview), Best Sustainability, Judges' Choice, Best Robotics. We are aiming for 1st, with Best Accessibility as the natural fallback.

### Hard constraints
- **Hardware:** MacBook Air M1 (2020), **8 GB RAM**, macOS Tahoe 26. Memory is the tightest constraint. Keep all models together within roughly 3.5 GB, and measure it.
- **Offline at runtime:** no network calls except to localhost. Downloading datasets or models during setup is fine.
- **Python 3.12**, virtual environment in `.venv`, packages installed with `uv`. Project folder: `~/Desktop/second-voice`.
- **LLM:** Bonsai 2 4B, model id `ternary-bonsai-2-4b`, served by LM Studio's OpenAI-compatible server at `http://localhost:1234/v1`, context 4096, Parallel 1. **Thinking must be off in every request**: the only method that works through LM Studio is `reasoning_effort: "none"` (we tested `chat_template_kwargs`, `/no_think` and `reasoning_effort: "low"`; all were ignored). Writing speed is about 18 to 23 tokens/second, so keep answers short.
- **Speech recognition:** `whisperkit-cli transcribe --model small.en --language en --skip-special-tokens --without-timestamps` (it also has `--stream` for the microphone, `--audio-path` for files, and a `serve` subcommand that runs a local transcription server; check `whisperkit-cli help serve`). Models live in `~/Documents/huggingface/models/`.
- **Speech synthesis:** `whisperkit-cli tts --text "..." --play` (0.6B model, voices like `aiden`, `serena`), with macOS `say` as the fallback. The 1.7B model will not fit in RAM; do not use it.
- **Camera:** MediaPipe hands (and face landmarks if needed). Always use the MacBook's built-in camera, never an iPhone (Continuity Camera). A camera picker already exists.
- The Mac's Wi-Fi will be visibly turned off during the demo.

## Design decisions already made (keep these)

These came out of real testing. Do not undo them; build on them.

**The Brain (repair/)**
- The model never guesses freely: it chooses from a fixed list (the user's phrases and commands, plus what is on screen), enforced by a JSON schema with enums. Phrases use short codes (P1, P2, ...), turned back into full text in code.
- Every answer has a confidence. High: act or speak. Low: ask.
- A second check can lower "high" to "low" when the heard words don't actually line up with the chosen phrase or its learned examples (this fixed a real bug: "cud eye haf sum pep see" was confidently mapped to "water").
- Low confidence with a best guess: ask "Did you mean ...?" (yes/no). On "no": show the next-best options plus "Something else".
- "Something else": the one deliberate exception to "never guess freely". The model writes a free-text guess, clearly labelled as a guess, which the user must confirm. It can only produce a sentence to speak, never a Mac action.
- Learning: every confirmed result is saved as heard text -> meaning in `profiles/profile.json` (personal, not in git). A fuzzy fast path (rapidfuzz, about 90%) answers instantly without the LLM, with safeguards (ambiguous matches and very short inputs are refused). The last ~10 examples go into the prompt. `undo` forgets the last lesson. `--fresh` starts from a clean copy of `profiles/sample_profile.json` and backs up the old profile.
- A ~3-second correction window after every action or spoken phrase: "no" cancels it, undoes what was learned, and goes to the options.

**Open items from the last round (finish them if they are not already done):**
1. "Guess again": up to 3 free guesses, each told which guesses were already rejected.
2. After "no", rank phrases and commands separately, and show the group of the top guess first (never offer commands to someone asking for a drink).
3. When a fast-path answer is cancelled and the heard text matches that example at 95% or more, delete the old example.
4. Action safety: each command is "safe" (act, then allow cancel: open_app, scroll_down, read_latest_email) or "risky" (always ask first, even at high confidence: anything that sends, calls or deletes, such as reply_email).
5. A personal vocabulary in the profile (names, drinks, foods, places this person mentions often) used by the free guess, growing as guesses are confirmed.

**Hand gestures (choose/)**
- One automatic system, no modes. After hold-to-confirm (about 1 second, 80% of frames, with a progress ring), the detector reports one of: RIGHT, LEFT (raised hand, any pose) or RIGHT_UP, RIGHT_DOWN, LEFT_UP, LEFT_DOWN (clear thumb).
- Meaning depends on the question. Yes/no: RIGHT, RIGHT_UP, LEFT_UP = yes; LEFT, RIGHT_DOWN, LEFT_DOWN = no. Four-choice: the four thumb signals map to the 2x2 grid; a plain hand means "ask again". Three-choice: one square empty.
- No calibration required (many users can't do a timed setup). Rotation-tolerant default rules, a fist rejected by checking whether the thumb rests on the fingers, thumb angles up to about 85 degrees accepted when the thumb is clearly out (my own left thumb has limited movement and points about 72 degrees from vertical). Quiet adaptation from confirmed gestures. Calibration is optional only.
- Both hands raised = nothing. Hands low in the frame are ignored.
- The overriding rule everywhere: **when unsure, select nothing.**

## What still needs to be built

Reach these goals in whatever order and way you judge best. Each should end with something that runs.

### 1. The real voice loop
Speech in, through WhisperKit, the Brain, then speech out or an action, end to end. Work out request endpointing (a pause of about 1.5 s ends a request), ignore WhisperKit's markers like `[BLANK_AUDIO]` and `[Inaudible]`, gently boost quiet audio (never discard quiet speech: soft speech is a Parkinson's symptom), and deal with background speech. In testing, a bystander's "How is she doing?" was transcribed. The Brain's confidence already filters most of this; consider a deliberate "listen now" trigger (a raised hand, a key, a big on-screen button) and whatever else you judge best. Keep the latency of every step measured.

### 2. One input system for answers
Speech ("yes"/"no"), hand gestures and the keyboard must all produce the same answer events, used by every question in the flow ("Did you mean...?", the options grid, the correction window, "guess again"). The person should be able to complete the whole flow without touching the keyboard or speaking clearly.

### 3. Acting on the Mac
Implement the commands for real using macOS accessibility / automation: open an app, read the latest email aloud (Mail), scroll, reply to an email (risky: always confirm). Give the Brain the list of what is actually clickable or available on screen, so it chooses from real options. This is a key part of the pitch ("it only chooses things that exist on your screen"). Respect the safe/risky split. If something needs a permission I must grant in System Settings, detect it, explain it clearly, and list it in the summary.

### 4. The app's screen
A local web page (FastAPI + one HTML page, opened in Safari) that a judge can understand at a glance and a person with limited vision or motor control can use: large text, high contrast, big targets. It should show: what was heard, what the Brain chose and its confidence (with the reason when it lowers confidence), the 2x2 grid that matches the hand signals, the hold-to-confirm progress, the learning feed ("Learned: ..."), the FAST PATH moments, an optional camera preview, and a metrics panel: time per step, tokens/second, memory in use, and a clear offline indicator. Design it to look polished; it will be on a projector.

### 5. A real speech profile from TORGO
Download TORGO (Hugging Face: `abnerh/TORGO-database`, academic use; cite the TORGO papers). Pick one dysarthric speaker as the demo "user". Split their recordings into enrollment and held-out test sets with no overlap. Build a profile (phrases and commands) that fits what that speaker actually says, and fill it with real heard -> meant pairs by running WhisperKit on the enrollment clips. This is what makes the Brain confident on real impaired speech.

### 6. Evaluation: the before/after number
A repeatable evaluation on the held-out clips: raw WhisperKit (does its text match the right phrase or command?) versus Second Voice (does it reach the right answer, and with how many questions?). Also word error rate, latency per step, and peak memory. Save results to a file and produce a clean chart for the slides. If possible, also a Parkinson's baseline from MDVR-KCL (Zenodo record 2867216; check its license and cite it): word error rate for Parkinson's speakers versus healthy controls. Report numbers honestly, including failures.

### 7. Performance on 8 GB
Measure total memory with WhisperKit, Bonsai, TTSKit and MediaPipe running together. Reduce latency where it helps (for example, warm LM Studio up in the background after learning, since the first call after the prompt changes is about 2 s slower; keep answers short; consider short codes for commands too). The fanless Air slows down when hot, so avoid unnecessary load.

### 8. Demo readiness
- One command that starts everything: it checks that LM Studio's server is running with the right model, that all models are present, that thinking is off, and whether Wi-Fi is off, then opens the app.
- A demo mode built around the story: fresh start, a garbled sentence it isn't sure about, the person confirms with a raised hand, the same sentence again answered instantly by the fast path, a Pepsi-style new sentence reached through "Something else", an action on the Mac, and the metrics panel.
- A fallback if something fails on stage (for example, recorded clips instead of the live microphone).
- A `README.md` for judges: what it is, why it matters, the architecture, how the sponsor technology is used, how to run it, the evaluation results, and an ethics note (recorded research clips, never imitating impaired speech, an assistive prototype, not a medical device).

### 9. Stretch goals (only after everything above works)
- A head-gesture input (tilt left/right, chin up/down) mapped to the same answer events, for people who can't raise their arms (ALS).
- SpeakerKit (`whisperkit-cli diarize`) to ignore voices other than the enrolled user.
- An energy-per-request measurement for the Sustainability angle.

## Quality bar

- Keep every existing test passing, and add tests for new logic that don't need a camera, microphone or the model (use the fakes).
- Hardware you can't test yourself (microphone, camera, speakers, macOS permissions): write a clear manual test checklist for me in plain English.
- Log every model call (time, tokens, tokens/second).
- Fail gracefully: if LM Studio, WhisperKit or the camera is unavailable, say so clearly and fall back safely; never crash in front of judges.
- Don't install anything large without a good reason, and record every new package in `docs/DECISIONS.md`.
- Update `CLAUDE.md` so it reflects the final design.

## When you are done

1. Make sure everything is committed on `fable-build` and all tests pass.
2. Write `docs/SUMMARY.md` **in plain English for a non-engineer**, and also print it as your final message. It should cover:
   - What you built, part by part, and how to run each part (exact commands).
   - What you tested yourself and what is untested (especially anything needing the camera, microphone, speakers or permissions).
   - The evaluation numbers, if you got them.
   - Known problems and limitations.
   - The decisions you made on my behalf (a short version of `docs/DECISIONS.md`).
   - A manual test checklist I can follow step by step.
   - Anything you need from me (permissions, downloads, choices).
   - Your recommended next steps before Saturday, in priority order.
