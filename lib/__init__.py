"""Compatibility shim — real code lives in speech_pipeline/."""
from speech_pipeline import *  # noqa: F401,F403
from speech_pipeline import fourier_codec  # noqa: F401

import importlib as _importlib

def __getattr__(name):
    try:
        return _importlib.import_module(f"speech_pipeline.{name}")
    except ImportError:
        raise AttributeError(name)
