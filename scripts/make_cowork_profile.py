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
      compose_email  {to, subject, body} (hidden: replaced by new_email, box by box)
      new_email                          one new email, cursor in To
      next_field / previous_field        To -> Cc -> Subject -> message, and back
      go_to_field    {field}             To, Cc, Subject or Message
      send_email                         RISKY: always asks first; sends the email on screen
      new_document, save_document        Word's "Blank Document", Command+S
      new_tab, search_for {query}        browsers (search_for types the words; "hit search" runs it)
      answer_question {question}         a question: answered from the Brain's own knowledge, spoken
      open_latest_email                  opens the newest email in its own window (Mail)
      read_latest_email                  reads it aloud (with speaking off: opens it instead)
      type_text      {text}              typed into the app you were using
      scroll_up / scroll_down, go_back / go_forward, press_left / press_right / press_return
      quit_app       {app}               quits that app (close Word)
      close_app                          quits the app you were using (close the app)

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
    {"name": "open_app", "description": "Open an application on the Mac (only opens it).", "safety": "safe",
     "args": {"app": "Name of the app to open"}, "said_as": ["open", "launch", "start"],
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
    {"name": "new_email",
     "description": "Start ONE new email in Mail, with the cursor in To. Any wording: write an email, new email, "
                    "new mail, compose, open a draft, new message.", "safety": "safe", "args": {},
     "said_as": ["write an email", "new email", "new mail", "compose", "compose an email", "open a draft",
                 "new message", "start an email", "draft an email", "write a new email", "an email"],
     "say": "Starting a new email.", "request": "Write a new email"},
    {"name": "next_field", "description": "In an email (or a form): move the cursor to the next box "
                                          "(To, Cc, Subject, message).", "safety": "safe", "args": {},
     "said_as": ["next box", "next", "next field", "go to the next box", "move on"],
     "say": "Next box.", "request": "Next box"},
    {"name": "previous_field", "description": "In an email (or a form): move the cursor back to the previous box.",
     "safety": "safe", "args": {}, "said_as": ["previous box", "back a box", "last box", "previous field", "go back a box"],
     "say": "Back a box.", "request": "Back a box"},
    {"name": "go_to_field", "description": "In an email: put the cursor in a box the person names "
                                           "(go to subject, go to the message).", "safety": "safe",
     "args": {"field": "Which box"}, "said_as": ["go to", "go to the"],
     "say": "Going to {field}.", "request": "Go to {field}"},
    {"name": "send_email", "description": "Send the email that is open on screen.", "safety": "risky",
     "args": {}, "said_as": ["send", "send it", "send the email", "send email"],
     "say": "Sending your email.", "request": "Send the email"},
    {"name": "open_latest_email", "description": "Open the newest email in the inbox in its own window (Mail), "
                                                 "to look at it. Any wording: open my latest email, open the newest "
                                                 "email, show my last email.", "safety": "safe",
     "args": {}, "said_as": ["open my latest email", "open the newest email", "show my last email",
                             "open my last email", "show my latest email", "open the latest email",
                             "show the newest email", "open my newest email"],
     "say": "Opening your latest email.", "request": "Open my latest email"},
    {"name": "read_latest_email", "description": "Read the newest email OUT LOUD (only when the person asks to "
                                                 "hear it: read out, read aloud).", "safety": "safe",
     "args": {}, "said_as": ["read email", "read my mail", "read out my latest email", "read my latest email aloud",
                             "read out my email"],
     "say": "Reading your latest email.", "request": "Read my latest email"},
    {"name": "new_document", "description": "Create a new blank document (Word, TextEdit, Pages). "
                                            "Any wording: new document, blank document, new page.", "safety": "safe",
     "args": {}, "said_as": ["new document", "blank document", "new blank document", "a blank document",
                             "new page", "create a document", "new doc"],
     "say": "Making a blank document.", "request": "New blank document"},
    {"name": "save_document", "description": "Save the document or file being worked on.", "safety": "safe",
     "args": {}, "said_as": ["save", "save it", "save the document", "save this"],
     "say": "Saving.", "request": "Save"},
    {"name": "new_tab", "description": "Open a new tab in the web browser.", "safety": "safe", "args": {},
     "said_as": ["new tab", "open a new tab", "another tab"], "say": "Opening a new tab.", "request": "New tab"},
    {"name": "search_for", "description": "Type words to search for into the browser's address bar or the "
                                          "search box (it is run with \"hit search\").", "safety": "safe",
     "args": {"query": "The words to search for"}, "said_as": ["search for", "look up", "google"], "echo": True,
     "echo_after": ["search for", "search", "look up", "google"],
     "say": "Typing your search.", "request": "Search for “{query}”"},
    {"name": "answer_question",
     "description": "The person asks a QUESTION (what, who, when, where, how, why, is, will, can...), e.g. "
                    "the weather, a fact, the time. Answer it; never type it.", "safety": "safe",
     "args": {"question": "The question, in plain words"}, "echo": True,
     "said_as": ["what", "what is", "what's", "who", "when", "where", "how", "why", "is it", "will it", "tell me"],
     "say": "Let me think.", "request": "Answer: “{question}”"},
    {"name": "type_text",
     "description": "Type words the person dictates. ONLY when they say type or write followed by the words, "
                    "or when dictation is on and the cursor is in a text box. Never for a question or a command.",
     "safety": "safe", "echo": True, "echo_after": ["type", "write", "dictate"],
     "args": {"text": "The exact words to type"}, "said_as": ["type", "write"],
     "say": "Typing.", "request": "Type “{text}”"},
    {"name": "quit_app", "description": "Close (quit) an app by its name, e.g. close Word, quit Chrome, close Mail.",
     "safety": "safe",
     "args": {"app": "Name of the app to close"}, "said_as": ["close", "quit"],
     "say": "Closing {app}.", "request": "Close {app}"},
    {"name": "close_app", "description": "Close (quit) the app the person is using right now, when they do not "
                                         "name an app (close the app, close this).", "safety": "safe",
     "args": {}, "said_as": ["close the app", "close this", "close it"],
     "say": "Closing the app.", "request": "Close the app"},
]
# Kept in the code but not offered: websites and Google search can't load with Wi-Fi off (offline demo).
HIDDEN = {"open_website", "search_web",
          "compose_email"}   # replaced by new_email: the email is written box by box, by voice
REQUESTS = {"scroll_up": "Scroll up", "scroll_down": "Scroll down", "go_back": "Go back",
            "go_forward": "Go forward", "press_left": "Press the left arrow", "press_right": "Press the right arrow",
            "press_return": "Press Enter"}
EXTRA_SAID_AS = {"scroll_up": ["scroll up"], "scroll_down": ["scroll down"],
                 "press_return": ["hit search", "press enter", "hit enter", "enter", "search"]}
DESCRIPTIONS = {"press_return": "Press Return (Enter) in the app being typed into, e.g. to run a search. "
                                "The person says: hit search, press enter, select."}


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
        command["description"] = DESCRIPTIONS.get(command["name"], command["description"])
        if command["name"] == "press_return":
            command["say"] = "Pressing Enter."
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
