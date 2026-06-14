"""Minimal JSON file cache so results survive server restarts."""

import json
import os
import tempfile

# Repo root = parent of the directory this file lives in (app/).
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(ROOT_DIR, "cache")


def load(name: str, default=None):
    """Load JSON cache file `name`, returning `default` if missing/corrupt."""
    path = os.path.join(CACHE_DIR, name)
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return default


def save(name: str, data) -> None:
    """Atomically write `data` as JSON to cache file `name`."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name)
    fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
