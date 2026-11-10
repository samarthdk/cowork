"""Try intent repair on its own:  .venv/bin/python -m repair "wanna watr" """

import json
import sys

import config
from repair import get_llm, repair
from repair.store import open_profile

heard = " ".join(sys.argv[1:]) or "wanna watr pleez"
intent = repair(heard, open_profile(), get_llm())
print(json.dumps(intent.to_dict(), indent=2))
