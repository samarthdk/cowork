"""Tidying what the recognizer returns.

WhisperKit writes markers for things that are not speech, such as
[BLANK_AUDIO], [Inaudible], (music) or *coughing*. Those must never reach the
Brain as if the person had said them. On silence it can also "hear" a few stock
phrases; those are dropped only when the recognizer itself was unsure.
"""

import re

# [BLANK_AUDIO], (inaudible), *panting*, <|endoftext|> ...
_MARKERS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*|<\|[^|]*\|>")
# Phrases Whisper models are known to invent on silence or noise.
_SILENCE_PHRASES = {"thank you", "thanks for watching", "thank you for watching", "you", "bye",
                    "thanks", "so", "okay", "please subscribe", "i'm sorry"}
UNSURE_LOGPROB = -0.7   # below this the recognizer was guessing
NO_SPEECH = 0.6         # above this it thinks nobody spoke


def clean_text(raw: str) -> str:
    """Remove non-speech markers and tidy spaces. Returns "" if nothing is left."""
    text = _MARKERS.sub(" ", raw or "")
    text = " ".join(text.split()).strip()
    if not re.search(r"[A-Za-z0-9]", text):
        return ""
    return text


def is_probably_noise(text: str, avg_logprob: float, no_speech_prob: float) -> bool:
    """True if this looks like something invented from silence or background noise."""
    if not text:
        return True
    if no_speech_prob >= NO_SPEECH:
        return True
    key = re.sub(r"[^a-z' ]", "", text.lower()).strip()
    return key in _SILENCE_PHRASES and avg_logprob <= UNSURE_LOGPROB
