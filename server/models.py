"""VRAM model manager: at most ONE model resident at a time.

Klein (~13 GB peak) nearly fills a 16 GB card on its own, so every
pipeline is obtained through get(); switching names unloads the old
pipeline before loading the new one. Runs inside the single GPU worker
thread, so swaps serialize with generation.
"""
import gc
import logging
import math
import time

log = logging.getLogger("spriteloom.models")

_factories = {}
_resident_name = None
_resident = None


def register(name, factory):
    _factories[name] = factory


def is_ready(name):
    """True when the model is loaded and resident (no load wait on use)."""
    return _resident_name == name


_load_progress = None  # (value, stage, ceiling, reported at) while loading
_load_floor = 0.0
CREEP_TAU = 6.0   # seconds to cover ~63% of the gap to the ceiling
CREEP_MAX = 0.9   # of the gap, so a step never reads as finished early


def set_load_progress(v, stage=None, ceiling=None):
    """Fraction of the whole load, with the console's own stage as the label.
    ceiling is where the step in flight ends; v=None clears (done/failed)."""
    global _load_progress, _load_floor
    if v is None:
        _load_progress, _load_floor = None, 0.0
        return
    _load_progress = (v, stage, v if ceiling is None else ceiling,
                      time.monotonic())


def load_progress():
    """Between step boundaries the value creeps toward the step's end,
    never reaching it, so a silent loader still moves the bar."""
    global _load_floor
    if _load_progress is None:
        return None
    v, stage, ceiling, since = _load_progress
    if ceiling > v:
        gap = 1.0 - math.exp(-(time.monotonic() - since) / CREEP_TAU)
        v += (ceiling - v) * min(gap, CREEP_MAX)
    _load_floor = max(_load_floor, v)
    return _load_floor, stage


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
    set_load_progress(0.0)
    try:
        _resident = _factories[name]()
    finally:
        set_load_progress(None)
    _resident_name = name
    return _resident


def reset():
    """Drop everything (tests and error recovery)."""
    global _resident_name, _resident
    _resident = None
    _resident_name = None
    set_load_progress(None)
    _factories.clear()
