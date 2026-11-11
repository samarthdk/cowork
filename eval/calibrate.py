"""Choosing the rule for when "high confidence" may be trusted.

    .venv/bin/python -m eval.calibrate M05 F03 M01      (development speakers)

It re-reads saved evaluation results (no model calls) and recomputes what
would have happened under different rules:

    as run        the rule in force when the results were made
    model         trust the model's own "high"
    model+ev>=T   the model says "high" AND the evidence score is at least T
    ev>=T         the evidence score alone decides

"Evidence" (repair/support.py) = how much of what was heard is covered by the
chosen phrase or a learned example of it, and how much of that text was
actually heard. The threshold is chosen on development speakers and then used
unchanged on the test speaker, so the test result is honest.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.run_eval import RESULTS  # noqa: E402
from eval.torgo_profile import build, enrolled_profile  # noqa: E402
from repair.rank import group_of, rank_options  # noqa: E402
from repair.support import evidence  # noqa: E402


def load(speaker: str) -> list[dict]:
    """Every answered clip with what is needed to replay it under another rule."""
    result = json.loads((RESULTS / f"torgo_{speaker}.json").read_text())
    setup = build(speaker)
    profiles = {"fresh": setup["profile"], 0: enrolled_profile(setup, 0), 1: enrolled_profile(setup, 1)}
    rows = []
    for group, records in result["records"].items():
        for r in records:
            if not r.get("heard") or r.get("choice") is None:
                continue
            profile = profiles["fresh"] if group == "fresh" else profiles[r.get("fold", 0)]
            lowered = any(p.startswith("high confidence lowered") for p in r.get("problems", []))
            right = r["choice"] == r["truth"]
            in_grid = (not right and r["truth"] is not None and r["truth"] in rank_options(
                r["heard"], profile, [], exclude=(r["choice"],), first_group=group_of(r["choice"], profile)))
            rows.append({"speaker": speaker, "group": group, "right": right, "in_grid": in_grid,
                         "outside": r["truth"] is None, "ran_high": r["confidence"] == "high",
                         "model_high": r["confidence"] == "high" or lowered,
                         "fast": r.get("source") == "fast_path",
                         "evidence": evidence(r["heard"], r["choice"], profile)})
    return rows


def outcome(row: dict, high: bool) -> str:
    if row["outside"]:
        return "wrong" if high else "asked"
    if high:
        return "instant" if row["right"] else "wrong"
    return "one question" if row["right"] else ("two questions" if row["in_grid"] else "not found")


RULES = {"as run": lambda r: r["ran_high"], "model": lambda r: r["model_high"]}
for t in (0.5, 0.6, 0.7, 0.8, 0.9):
    RULES[f"model+ev>={t}"] = lambda r, t=t: r["fast"] or (r["model_high"] and r["evidence"] >= t)
    RULES[f"ev>={t}"] = lambda r, t=t: r["fast"] or r["evidence"] >= t


def table(rows: list[dict], title: str) -> None:
    inside = [r for r in rows if not r["outside"]]
    outside = [r for r in rows if r["outside"]]
    print(f"\n{title}: {len(inside)} in-profile answers, {len(outside)} outside-profile answers")
    print(f"  {'rule':16}{'instant':>9}{'1 question':>12}{'2 questions':>13}{'not found':>11}{'WRONG':>8}"
          f"{'questions':>11}   | outside: confidently wrong")
    for name, rule in RULES.items():
        outs = [outcome(r, rule(r)) for r in inside]
        share = lambda o: outs.count(o) / max(len(outs), 1)  # noqa: E731
        questions = (outs.count("one question") + 2 * outs.count("two questions") + 2 * outs.count("not found")) / max(len(outs), 1)
        wrong_outside = sum(outcome(r, rule(r)) == "wrong" for r in outside) / max(len(outside), 1)
        print(f"  {name:16}{share('instant'):>9.0%}{share('one question'):>12.0%}{share('two questions'):>13.0%}"
              f"{share('not found'):>11.0%}{share('wrong'):>8.1%}{questions:>11.2f}   | {wrong_outside:.0%}")


if __name__ == "__main__":
    speakers = sys.argv[1:] or ["M05"]
    everything = []
    for speaker in speakers:
        rows = load(speaker)
        everything += rows
        table(rows, f"Speaker {speaker}")
    if len(speakers) > 1:
        table(everything, "All together")
