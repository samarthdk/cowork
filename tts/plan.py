"""Which sentences get the TTSKit voice ready in advance (the rest use the Mac voice).

Used by Cowork at start-up (in the background) and by scripts/prepare_voice.py.
"""

import config

STEP_APPS = 12   # "Opening <app>." is prepared for this many apps (running and common ones first)


def sentences(profile: dict, apps: list[str], step_apps: int = STEP_APPS) -> list[str]:
    """In order: the commands' spoken steps, "Opening <app>." for the first apps,
    every phrase, then "Opening <website>." for the websites."""
    from engine.speech import sentences_to_prepare
    steps = [f"Opening {app}." for app in apps[:step_apps]]
    sites = [f"Opening {site}." for site in config.SITES]
    everything = sentences_to_prepare(profile)
    commands = [s for s in everything if s not in profile["phrases"]]
    return list(dict.fromkeys(commands + steps + profile["phrases"] + sites))
