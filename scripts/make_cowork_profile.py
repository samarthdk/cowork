"""Builds the Cowork demo profile: profiles/cowork_base.json.

    .venv/bin/python scripts/make_cowork_profile.py

What is in it:
  - the sentences said in the "Play test clip" recordings, and the command words,
    of TORGO speaker M04, with the learned examples from M04's
    enrollment recordings (fold 0, exactly as in the M04 evaluation), and
    M04's own way of saying yes and no;
  - the everyday phrases from profiles/sample_profile.json;
  - the Cowork actions on the Mac. Each one is chosen from a list plus details
    to fill in, never a free plan:

      open_app       {app}               one of the apps installed on this Mac
      open_website   {site}              one of config.SITES        (hidden: needs the internet)
      search_web     {query}             words to search Google for (hidden: needs the internet)
      compose_email  {to, subject, body} "to" is one of profiles/contacts.json
      send_email                         RISKY: always asks first
      read_latest_email
      type_text      {text}              typed into the app you were using
      scroll_up / scroll_down, go_back / go_forward, press_left / press_right / press_return
      close_app                          quits the app you were using

"request" is how the screen writes the person's request ("Open Mail").
"said_as" is how people tend to say it (used to check the Brain's confidence).

The working copy (profiles/cowork_profile.json) is made from this the first
time the app starts, and is where learning goes. --fresh starts it over.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
from eval.torgo_profile import build, enrolled_profile  # noqa: E402

SPEAKER = "M04"
OUT = config.ROOT / "profiles" / "cowork_base.json"

COMMANDS = [
    {"name": "open_app", "description": "Open an application on the Mac.", "safety": "safe",
     "args": {"app": "Name of the app to open"}, "said_as": ["open"],
     "say": "Opening {app}.", "request": "Open {app}"},
    {"name": "open_website", "description": "Open a well-known website in the browser.", "safety": "safe",
     "args": {"site": "Name of the website"}, "said_as": ["open website", "go to"],
     "say": "Opening {site}.", "request": "Open {site}"},
    {"name": "search_web", "description": "Search Google in the browser for some words.", "safety": "safe",
     "args": {"query": "What to search for, in a few words"}, "said_as": ["search for", "google", "look up"],
     "say": "Searching for {query}.", "request": "Search Google for “{query}”"},
    {"name": "compose_email",
     "description": "Write a new email in Mail (it is NOT sent; sending is a separate command).",
     "safety": "safe",
     "args": {"to": "Who the email is for", "subject": "A short subject line",
              "body": "The message, as one or two short sentences"},
     "said_as": ["email", "write an email to"],
     "say": "Writing an email to {to}.", "request": "Write an email to {to}"},
    {"name": "send_email", "description": "Send the email that was just written.", "safety": "risky",
     "args": {}, "said_as": ["send", "send it", "send the email"],
     "say": "Sending your email.", "request": "Send the email"},
    {"name": "read_latest_email", "description": "Read the newest email out loud.", "safety": "safe",
     "args": {}, "said_as": ["read email", "read my mail"],
     "say": "Reading your latest email.", "request": "Read my latest email"},
    {"name": "type_text", "description": "Type some words into the app the person is using.", "safety": "safe",
     "args": {"text": "The exact words to type"}, "said_as": ["type", "write"],
     "say": "Typing.", "request": "Type “{text}”"},
    {"name": "close_app", "description": "Close (quit) the app the person is using.", "safety": "safe",
     "args": {}, "said_as": ["close", "quit", "close app"],
     "say": "Closing the app.", "request": "Close the app"},
]
# Kept in the code but not offered: websites and Google search can't load with Wi-Fi off (offline demo).
HIDDEN = {"open_website", "search_web"}
REQUESTS = {"scroll_up": "Scroll up", "scroll_down": "Scroll down", "go_back": "Go back",
            "go_forward": "Go forward", "press_left": "Press the left arrow", "press_right": "Press the right arrow",
            "press_return": "Press Return"}
EXTRA_SAID_AS = {"scroll_up": ["scroll up"], "scroll_down": ["scroll down"]}


def make() -> dict:
    from ui.clips import CLIP_IDS
    setup = build(SPEAKER)
    profile = enrolled_profile(setup, fold=0)
    everyday = json.loads(config.BASE_PROFILE_PATH.read_text())["phrases"]
    # Only the TORGO sentences the test clips say (measured: with all 64 TORGO sentences the
    # Brain was buried in them and missed "Open Mail"; the person's real list would be short too).
    clip_sentences = {setup["truth"][c] for c in CLIP_IDS if c in setup["truth"]}
    torgo = [p for p in profile["phrases"] if p in clip_sentences and p not in everyday]
    profile["phrases"] = everyday + torgo
    profile["examples"] = [e for e in profile["examples"]
                           if e["meant"] in profile["phrases"] or any(c["name"] == e["meant"] for c in profile["commands"])]
    torgo_commands = []
    for command in profile["commands"]:
        command["request"] = REQUESTS.get(command["name"], command["name"].replace("_", " ").capitalize())
        command["said_as"] = command.get("said_as", []) + EXTRA_SAID_AS.get(command["name"], [])
        torgo_commands.append(command)
    profile["commands"] = [c for c in COMMANDS if c["name"] not in HIDDEN] + torgo_commands
    profile["user"] = (f"Cowork demo: TORGO speaker {SPEAKER} (research recordings) "
                       "plus everyday phrases and Mac actions")
    return profile


if __name__ == "__main__":
    profile = make()
    OUT.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}: {len(profile['phrases'])} phrases, {len(profile['commands'])} commands, "
          f"{len(profile['examples'])} learned examples")
