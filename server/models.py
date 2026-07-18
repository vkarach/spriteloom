"""VRAM model manager: at most ONE model resident at a time.

Klein (~13 GB peak) nearly fills a 16 GB card on its own, so every
pipeline is obtained through get(); switching names unloads the old
pipeline before loading the new one. Runs inside the single GPU worker
thread, so swaps serialize with generation.
"""
import gc
import logging

log = logging.getLogger("spriteforge.models")

_factories = {}
_resident_name = None
_resident = None


def register(name, factory):
    _factories[name] = factory


def is_ready(name):
    """True when the model is loaded and resident (no load wait on use)."""
    return _resident_name == name


def get(name, on_stage=None):
    global _resident_name, _resident
    if name not in _factories:
        raise KeyError(f"unknown model '{name}'")
    if _resident_name == name:
        return _resident
    if _resident is not None:
        if on_stage:
            on_stage(f"Unloading {_resident_name} model...")
        log.info("unloading %s", _resident_name)
        _resident = None
        _resident_name = None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:  # torch absent in unit tests
            pass
    if on_stage:
        on_stage(f"Loading {name} model...")
    log.info("loading %s", name)
    _resident = _factories[name]()
    _resident_name = name
    return _resident


def reset():
    """Drop everything (tests and error recovery)."""
    global _resident_name, _resident
    _resident = None
    _resident_name = None
    _factories.clear()
