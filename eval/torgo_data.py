"""TORGO data preparation: extract clips and transcribe them with WhisperKit.

TORGO (Rudzicz, Namasivayam & Wolff, 2012) is a research database of speech
from speakers with dysarthria (cerebral palsy or ALS) and matched controls.
We use the Hugging Face copy `abnerh/TORGO-database` (short words and
restricted sentences; free for academic, non-profit use).

    .venv/bin/python -m eval.torgo_data download          (1.6 GB, once)
    .venv/bin/python -m eval.torgo_data index             (list every recording)
    .venv/bin/python -m eval.torgo_data transcribe M04    (WhisperKit on one speaker)
    .venv/bin/python -m eval.torgo_data transcribe all    (all dysarthric + a control sample)
    .venv/bin/python -m eval.torgo_data report            (word error rate per speaker)

Each utterance was recorded on two microphones at once (head and array). They
are the same moment of speech, so only ONE is used per utterance (the head
microphone when it exists), otherwise the "same" recording could end up in
both the enrollment and the test set.

Everything is cached under datasets/torgo/ (not in git).
"""

import glob
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

ROOT = config.ROOT / "datasets" / "torgo"
RAW, WAV, TRANSCRIPTS = ROOT / "raw", ROOT / "wav", ROOT / "transcripts"
INDEX = ROOT / "utterances.json"
HF_URL = "https://huggingface.co/datasets/abnerh/TORGO-database/resolve/main/data/train-0000{i}-of-00004.parquet"
DYSARTHRIC = ["F01", "F03", "F04", "M01", "M02", "M03", "M04", "M05"]
CONTROLS = ["FC01", "FC02", "FC03", "MC01", "MC02", "MC03", "MC04"]
CONTROL_SAMPLE = 250   # utterances per control speaker (they are only a baseline)


def normalize(text: str) -> str:
    """Lowercase, no punctuation, single spaces: for fair text comparison."""
    import re
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", text.lower()).split())


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for i in range(4):
        target = RAW / f"train-0000{i}-of-00004.parquet"
        print(f"downloading {target.name} ...")
        subprocess.run(["curl", "-sSfL", "-C", "-", "-o", str(target), HF_URL.format(i=i)], check=True)


def build_index() -> list[dict]:
    """One entry per utterance (the two microphones merged)."""
    import pyarrow.parquet as pq
    utterances = {}
    for f in sorted(glob.glob(str(RAW / "*.parquet"))):
        pf = pq.ParquetFile(f)
        table = pf.read(columns=["transcription", "speech_status", "duration"])
        paths = pf.read(columns=["audio"]).column("audio").combine_chunks().field("path").to_pylist()
        for path, text, status, duration in zip(paths, table["transcription"].to_pylist(),
                                                table["speech_status"].to_pylist(),
                                                table["duration"].to_pylist()):
            speaker, session, mic, number = path[:-4].split("_")
            key = f"{speaker}_{session}_{number}"
            entry = utterances.setdefault(key, {"id": key, "speaker": speaker, "session": session,
                                                "number": number, "text": " ".join(text.split()),
                                                "status": status, "duration": duration, "files": {}})
            entry["files"][mic] = path
    for entry in utterances.values():
        mic = "headMic" if "headMic" in entry["files"] else sorted(entry["files"])[0]
        entry["mic"], entry["file"] = mic, entry["files"][mic]
    index = sorted(utterances.values(), key=lambda e: e["id"])
    INDEX.write_text(json.dumps(index))
    return index


def load_index() -> list[dict]:
    return json.loads(INDEX.read_text()) if INDEX.exists() else build_index()


def extract(files: set[str]) -> None:
    """Write the chosen recordings out of the dataset files as .wav clips."""
    import pyarrow.parquet as pq
    WAV.mkdir(parents=True, exist_ok=True)
    missing = {f for f in files if not (WAV / f).exists()}
    if not missing:
        return
    for f in sorted(glob.glob(str(RAW / "*.parquet"))):
        pf = pq.ParquetFile(f)
        for batch in pf.iter_batches(batch_size=256, columns=["audio"]):
            for audio in batch.column("audio").to_pylist():
                if audio["path"] in missing:
                    (WAV / audio["path"]).write_bytes(audio["bytes"])
                    missing.discard(audio["path"])
        if not missing:
            break


def utterances_for(speaker: str) -> list[dict]:
    items = [u for u in load_index() if u["speaker"] == speaker]
    if speaker in CONTROLS:  # an evenly spread sample is enough for a baseline
        step = max(1, len(items) // CONTROL_SAMPLE)
        items = items[::step][:CONTROL_SAMPLE]
    return items


def transcript_path(speaker: str, variant: str = "plain") -> Path:
    return TRANSCRIPTS / f"{speaker}.{variant}.jsonl"


def load_transcripts(speaker: str, variant: str = "plain") -> dict[str, dict]:
    path = transcript_path(speaker, variant)
    if not path.exists():
        return {}
    return {r["id"]: r for r in map(json.loads, path.read_text().splitlines()) if r}


def transcribe(speaker: str, variant: str = "plain", prompt: str = "", only: set | None = None) -> dict[str, dict]:
    """Run WhisperKit over a speaker's recordings (results are cached).
    `variant` names the cache: "plain" = no hint; anything else = with `prompt`."""
    from asr.real import WhisperKitASR
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    items = [u for u in utterances_for(speaker) if only is None or u["id"] in only]
    extract({u["file"] for u in items})
    done = load_transcripts(speaker, variant)
    asr = WhisperKitASR()
    start = time.monotonic()
    with open(transcript_path(speaker, variant), "a") as out:
        for n, u in enumerate(items, 1):
            if u["id"] in done:
                continue
            t = asr.transcribe(str(WAV / u["file"]), prompt=prompt)
            record = {"id": u["id"], "ref": u["text"], "heard": t.text, "raw": t.raw_text,
                      "logprob": round(t.avg_logprob, 3), "latency_ms": round(t.latency_ms),
                      "problem": t.problem}
            out.write(json.dumps(record) + "\n")
            out.flush()
            done[u["id"]] = record
            if n % 100 == 0:
                print(f"  {speaker} {variant}: {n}/{len(items)}  ({time.monotonic() - start:.0f} s)", flush=True)
    return done


def word_error_rate(pairs: list[tuple[str, str]]) -> float:
    """Word error rate over (reference, heard) pairs, as a fraction."""
    import jiwer
    refs = [normalize(r) for r, _ in pairs]
    hyps = [normalize(h) for _, h in pairs]
    keep = [(r, h) for r, h in zip(refs, hyps) if r]
    return jiwer.wer([r for r, _ in keep], [h for _, h in keep]) if keep else 0.0


def report() -> list[dict]:
    rows = []
    for speaker in DYSARTHRIC + CONTROLS:
        records = list(load_transcripts(speaker).values())
        if not records:
            continue
        pairs = [(r["ref"], r["heard"]) for r in records]
        exact = sum(normalize(r) == normalize(h) for r, h in pairs) / len(pairs)
        sentences = [(r, h) for r, h in pairs if len(r.split()) > 1]
        words = [(r, h) for r, h in pairs if len(r.split()) == 1]
        rows.append({"speaker": speaker, "group": "dysarthric" if speaker in DYSARTHRIC else "control",
                     "clips": len(pairs), "wer": word_error_rate(pairs), "exact": exact,
                     "sentence_wer": word_error_rate(sentences) if sentences else None,
                     "word_exact": (sum(normalize(r) == normalize(h) for r, h in words) / len(words)) if words else None})
    print(f"{'speaker':8} {'group':11} {'clips':>5} {'WER':>7} {'sent.WER':>9} {'exact':>7} {'word exact':>11}")
    for r in rows:
        fmt = lambda v: f"{v:6.1%}" if v is not None else "   n/a"  # noqa: E731
        print(f"{r['speaker']:8} {r['group']:11} {r['clips']:5} {fmt(r['wer']):>7} {fmt(r['sentence_wer']):>9} "
              f"{fmt(r['exact']):>7} {fmt(r['word_exact']):>11}")
    return rows


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "report"
    if command == "download":
        download()
    elif command == "index":
        index = build_index()
        print(f"{len(index)} utterances from {len({u['speaker'] for u in index})} speakers -> {INDEX}")
    elif command == "transcribe":
        who = sys.argv[2] if len(sys.argv) > 2 else "all"
        for speaker in (DYSARTHRIC + CONTROLS if who == "all" else [who]):
            print(f"{speaker}: {len(transcribe(speaker))} transcripts", flush=True)
    else:
        report()
