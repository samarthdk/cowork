# Second Voice (Cowork)

An offline assistant for people whose speech is hard for computers (and people) to understand:
Parkinson's, ALS, stroke, cerebral palsy. It hears what the person says, works out what they
meant from a short list of their own phrases and commands, checks with them when unsure
("Did you mean...?", answered by voice, thumb or keyboard), and then either acts on the Mac
(open an app, type, write an email box by box) or says the sentence aloud for other people.

Everything runs on the Mac. There are no network calls except to `localhost`.

| Part | What runs it |
|---|---|
| Hearing | WhisperKit (small model) |
| Understanding | Bonsai 2 4B in LM Studio (`http://localhost:1234/v1`), strict JSON, thinking off |
| Voice | Argmax TTSKit 0.6B |
| Hands and head | MediaPipe, built-in FaceTime HD Camera |

## Try it

    .venv/bin/python -m ui --fake      # everything fake: nothing else needed
    .venv/bin/python -m ui --real      # everything real (LM Studio with Bonsai loaded)

Run all automated checks:

    for t in tests/test_*.py; do .venv/bin/python $t | tail -1; done

More: `docs/PROGRESS.md` (where things stand), `docs/COWORK.md`, `docs/DECISIONS.md`.

## Credit

Test recordings come from the TORGO database of dysarthric speech: F. Rudzicz,
A. K. Namasivayam and T. Wolff, *The TORGO database of acoustic and articulatory speech from
speakers with dysarthria*, University of Toronto, 2012 (Language Resources and Evaluation 46(4)).
The demo uses speaker M04, a man with cerebral palsy. The same credit is shown in the app under
Settings > About and below the test clip list.
