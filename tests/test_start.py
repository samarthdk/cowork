"""The one start command (scripts/start.sh = python -m ui --demo): checks in plain English, then the demo.
Only the parts that need no models are checked here. Run:
    .venv/bin/python tests/test_start.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from ui.checks import Check, accessibility, ready, wifi  # noqa: E402

results = []


def check(name, got, expected):
    ok = got == expected
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got!r}, expected {expected!r}")


check("a needed check failing: not ready", ready([Check("LM Studio", False, "off", "open it")]), False)
check("only optional ones failing (camera): still ready", ready([Check("LM Studio", True, "ok"),
                                                                Check("Camera", False, "missing", needed=False)]), True)
w = wifi()
check("Wi-Fi is reported in plain words, never blocks the start", (isinstance(w.detail, str) and bool(w.detail), w.needed), (True, False))
a = accessibility()
check("Accessibility: a fix is given when it is off", a.ok or "System Settings" in a.fix, True)
script = (config.ROOT / "scripts" / "start.sh")
check("scripts/start.sh exists and can be run", script.exists() and bool(script.stat().st_mode & 0o111), True)
check("  ...it starts the demo settings", "-m ui --demo" in script.read_text(), True)
done = subprocess.run([sys.executable, "-m", "ui", "--demo", "--fake"], cwd=config.ROOT, capture_output=True, text=True, timeout=60)
check("--demo refuses the fakes", (done.returncode != 0, "leave out --fake" in done.stderr), (True, True))

print(f"\n{sum(results)}/{len(results)} passed  {'ALL PASS' if all(results) else 'SOME FAILED'}")
sys.exit(0 if all(results) else 1)
