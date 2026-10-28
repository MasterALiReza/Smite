"""Test isolation: never touch the real panel database or signing key.

The env vars are set before any test module imports ``app.config`` so the
panel lifespan used by integration tests creates its SQLite database inside a
temporary directory instead of the repository's ``data/`` directory.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

_TEST_STATE = Path(tempfile.mkdtemp(prefix="smite-tests-"))
atexit.register(shutil.rmtree, str(_TEST_STATE), True)

os.environ.setdefault("SECRET_KEY", "test-only-secret-key-0123456789abcdef0123456789abcdef")
os.environ.setdefault("DB_PATH", str(_TEST_STATE / "smite.db"))
