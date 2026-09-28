"""Load sqlite-vec without importing its Python wrapper.

The ``sqlite_vec`` package imports numpy at import time (~60 ms), which every
CLI call would pay. Only the bundled extension file and float packing are needed.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
from array import array
from typing import Iterable


def _extension_path() -> str:
    spec = importlib.util.find_spec("sqlite_vec")
    if spec is None or not spec.submodule_search_locations:
        raise ImportError("sqlite-vec is not installed")
    # sqlite resolves the platform suffix (.dll / .so / .dylib) itself.
    return os.path.join(spec.submodule_search_locations[0], "vec0")


def load(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    try:
        conn.load_extension(_extension_path())
    finally:
        conn.enable_load_extension(False)


def serialize(vector: Iterable[float]) -> bytes:
    """Pack floats as little-endian float32, the format sqlite-vec expects."""
    return array("f", (float(value) for value in vector)).tobytes()
