"""Acting on the Mac. Use get_actions() to get the fake or real version chosen in config.py."""

import config


def get_actions():
    if config.ACTIONS == "fake":
        from actions.fake import FakeActions
        return FakeActions()
    if config.ACTIONS == "real":
        from actions.mac import MacActions
        return MacActions()
    raise ValueError(f'config.ACTIONS must be "fake" or "real", not "{config.ACTIONS}"')
