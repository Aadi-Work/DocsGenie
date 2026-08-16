"""
Centralized logging setup.

Every entrypoint (CLI, API, or a script that imports the engine directly)
should call `setup_logging()` once, early. Without it, Python's logging
module defaults to WARNING-and-above with no handler attached in some
contexts - which is exactly how a run can appear to "do nothing": the code
executed fine, it just never printed anything below WARNING, and if nothing
in your script explicitly `print()`s a result, the terminal stays empty.

Usage:
    from engine.logging_config import setup_logging
    setup_logging()                    # INFO level, to stdout
    setup_logging(level="DEBUG")       # verbose, shows every stage's detail
    setup_logging(log_file="run.log")  # also write to a file
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

_CONFIGURED = False


def setup_logging(level: str = "INFO", log_file: Optional[str] = None,
                  fmt: Optional[str] = None) -> logging.Logger:
    """
    Configure the 'engine' logger tree (and its children) to print to stdout.
    Safe to call more than once - later calls just adjust the level.
    """
    global _CONFIGURED
    root = logging.getLogger("engine")
    root.setLevel(level.upper())

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            fmt or "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S"))
        root.addHandler(handler)
        root.propagate = False   # don't also hand records to the root logger
        _CONFIGURED = True

    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """Every engine module should log through `engine.<module>`, e.g. 'engine.pipeline'."""
    return logging.getLogger(f"engine.{name}")
