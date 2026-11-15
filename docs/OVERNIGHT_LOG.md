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
