"""Try intent repair on its own:  .venv/bin/python -m repair "wanna watr" """

import json
import sys

import config
from repair import get_llm, repair
from repair.profile import load_profile

heard = " ".join(sys.argv[1:]) or "wanna watr pleez"
intent = repair(heard, load_profile(config.PROFILE_PATH), get_llm())
print(json.dumps(intent.to_dict(), indent=2))
