# Build status (against docs/BUILD_BRIEF.md)

Updated as work proceeds, so the build can be resumed from this file alone.
Branch: `fable-build`. Run all checks with:

    for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

## Done and committed

| Brief item | State | Where |
|---|---|---|
| Open items 1-5 (guess again, grouped ranking, forget wrong example, safe/risky, vocabulary) | done (before this branch) | `repair/`, `engine/session.py` |
| Brain speed: compact one-of-shapes answers (0.8-1.5 s, was 2.0-2.7 s) | done | `repair/prompt.py` |
| Details chosen from real lists (installed apps, on-screen items) | done; on-screen items untested (needs Accessibility) | `repair/prompt.py`, `actions/mac.py` |
| 1. Voice loop: WhisperKit local server, marker cleaning, gentle boost | done, tested on real audio | `asr/` |
| 1. Request endpointing (1.5 s pause), "listen now" trigger, mute while speaking | logic done and tested; real speaker-to-microphone loop verified once | `asr/mic.py` |
| 2. One input system for answers (speech, hand signals, keyboard, screen) | done | `choose/answers.py`, `engine/session.py` |
| 3. Acting on the Mac: fake + real (open app, scroll, keys, click, read/reply email) | written; only open-app logic checked. Real scrolling/clicking/email UNTESTED (permissions) | `actions/` |
| TTSKit voice with instant playback of known phrases | done; silent synthesis verified | `tts/real.py` |
| 5. TORGO downloaded, indexed, every speaker transcribed with WhisperKit | done (data in `datasets/torgo/`, not in git) | `eval/torgo_data.py` |
| 5. Profile + no-overlap enrollment/test split per speaker (demo speaker: M04) | done | `eval/torgo_profile.py` |
| 6. Evaluation runner (simulated truthful user, cached model answers) | done; run on development speaker M05 only | `eval/run_eval.py`, `eval/calibrate.py` |
| Memory / offline measurements | done | `sysinfo.py` |

## In progress / next (in this order)

1. Confidence rule: "high" needs evidence from the heard words (threshold chosen on M05:
   confident-wrong went from 6.9% to 0%). Apply in `repair/`, update affected checks.
2. After a "no": ask the Brain a second time with the rejected choice removed.
3. Final evaluation on M04 + chart (`eval/chart.py`), word-error-rate table (already measurable:
   `.venv/bin/python -m eval.torgo_data report`).
4. The app: `ui/app.py` (orchestrator), `ui/server.py` (FastAPI + live updates), one HTML page.
5. Camera thread feeding hand signals into the app; optional preview.
6. Start command with checks (`scripts/start.py`), demo mode + recorded-clip fallback.
7. Memory measurement with everything running; reload LM Studio's model for a clean number
   (after long evaluations `llama-server` held 4.7 GB, probably saved prompt states).
8. README.md, CLAUDE.md update, manual test checklist, docs/SUMMARY.md.
9. Stretch: head gestures, SpeakerKit, energy per request. MDVR-KCL baseline if time allows.

## Things only Samarth can do

- Grant **Accessibility** to Terminal (System Settings > Privacy & Security > Accessibility):
  needed for scrolling, key presses, and listing/clicking on-screen buttons.
- Approve **Automation > Terminal > Mail** when macOS asks (reading/replying to email).
  A prompt for "System Events" may already be waiting on screen from an earlier probe; it can be dismissed.

## Known facts worth remembering

- LM Studio: only `reasoning_effort: "none"` turns thinking off. Anything that changes the
  system message costs seconds (about 4 s with 64 phrases); the request part is cheap.
- WhisperKit server: `whisperkit-cli serve --model-path <local folder> --port 50060`; 0.2-0.4 s per clip.
- TTSKit: 6-11 s per sentence from cold, so known phrases are pre-made and cached in `cache/tts/`.
- Camera and microphone permissions are already granted to Terminal.
