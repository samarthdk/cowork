"""Real ASR (WhisperKit, small model).

######################################################################
#  PLACEHOLDER - NOT BUILT YET.                                      #
#  Will call WhisperKit with the small model, add volume             #
#  normalization, and log latency via metrics.log_call().            #
######################################################################
"""


class WhisperKitASR:
    def transcribe(self, audio_path: str) -> str:
        raise NotImplementedError(
            "Real ASR is not built yet. Set ASR = \"fake\" in config.py for now."
        )
