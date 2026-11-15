#!/bin/bash
# Start Cowork for the demo: checks everything first (LM Studio and Bonsai, thinking off, WhisperKit and
# TTSKit models, camera, microphone, Accessibility, Wi-Fi), says in plain English what to fix if something
# is wrong, then starts Cowork with the demo settings (real models, microphone and camera on, demo profile).
#
#     scripts/start.sh             (add --fresh to forget what was learned in rehearsals, and the saved tasks)
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "The Python environment isn't set up yet. See README.md, 'How to run'."
  exit 1
fi
PYTHONUNBUFFERED=1 exec .venv/bin/python -m ui --demo "$@"
