"""Compatibility shim — real code lives in speech_pipeline/registry.py."""
from speech_pipeline.registry import *  # noqa: F401,F403
from speech_pipeline.registry import TTSRegistry, load_voice_info, VoiceInfo  # noqa: F401
