"""Shared paths and API-key loading used across the app."""

import os

# Repo root = parent of the directory this file lives in (app/). Anchoring to the
# file location means the app works regardless of the current working directory.
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_key(env_var: str, file_name: str) -> str:
    """Read an API key from an env var, falling back to a key file at the repo root."""
    key = os.environ.get(env_var)
    if key:
        return key.strip()
    with open(os.path.join(ROOT_DIR, file_name), "r") as f:
        return f.readline().strip()
