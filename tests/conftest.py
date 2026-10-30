"""Shared fixtures.

Creating a Store from scratch costs ~0.17 s on Windows (SQLite file creation and
schema commit), and an app's first write/startup adds a ~0.1 s daily backup.
Both dominate the suite, so the empty database is built once per session and each
test receives a cheap file copy.  Every test still gets its own private database.
"""
import shutil

from cryptography.fernet import Fernet
import pytest

from lifeev import storage
from lifeev.storage import Store

KEY = Fernet.generate_key()


@pytest.fixture(scope='session')
def _store_templates(tmp_path_factory):
    root = tmp_path_factory.mktemp('store-templates')
    fresh = root / 'fresh.sqlite'
    Store(fresh, KEY)
    # Same empty database, but flagged as "today's daily backup already taken" so
    # tests that do not examine backups skip the redundant startup/first-write snapshot.
    backed_up = root / 'backed_up.sqlite'
    shutil.copy(fresh, backed_up)
    store = Store(backed_up, KEY)
    with store._connection() as db:
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('snapshot_day', ?)",
                   (store._encode(storage._now().date().isoformat()),))
    return {False: fresh, True: backed_up}


@pytest.fixture
def make_store(_store_templates):
    """make_store(path, *, backed_up=False, **limits) -> Store using the shared KEY."""
    def make(path, *, backed_up=False, **limits):
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(_store_templates[backed_up], path)
        return Store(path, KEY, **limits)
    make.key = KEY
    return make
