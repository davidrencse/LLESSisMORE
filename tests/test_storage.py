from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3

from cryptography.fernet import Fernet
import pytest

from lifeev.storage import Store, _now


@pytest.fixture
def store(tmp_path, make_store):
    return make_store(tmp_path / 'private.sqlite')


def test_versions_and_frozen_parent(store):
    parent = store.create({'title': 'Original'})
    child = store.create({'title': 'Child'}, parent['id'])
    store.revise(parent['id'], {'title': 'Changed'})
    revised = store.revise(child['id'], {'title': 'Updated child'})
    assert revised['frozen_parent']['document']['title'] == 'Original'
    assert revised['parent_version'] == 1
    assert store.get(child['id'], 1)['document']['title'] == 'Child'
    revised['document']['title'] = 'local mutation'
    assert store.get(child['id'])['document']['title'] == 'Updated child'


def test_payloads_and_snapshots_encrypted(store):
    secret = 'UNIQUE_PRIVATE_CONTENT_83567'
    store.create({'title': secret})
    session = store.save_session({'document': {'title': secret}})
    store.add_memory({'value': secret})
    store.save_profile({'name': secret})
    assert store.get_session(session['id'])['document']['title'] == secret
    assert store.list_sessions() and store.list_memory() and store.list_profiles()
    snapshot = store.snapshot('manual')
    from pathlib import Path
    for path in store.path.parent.rglob('*'):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
    assert secret not in str(store.audit())
    raw = store._cipher.decrypt(Path(snapshot).read_bytes())
    restored_path = store.path.parent / 'restored.sqlite'
    restored_path.write_bytes(raw)
    db = sqlite3.connect(restored_path)
    try:
        assert db.execute('SELECT COUNT(*) FROM nodes').fetchone()[0] == 1
    finally:
        db.close()
    with pytest.raises(ValueError):
        Store(store.path, Fernet.generate_key())


def test_archive_restore_and_expired_purge(store, monkeypatch):
    node = store.create({'title': 'Example'})
    store.archive(node['id'])
    assert not store.list_nodes()
    assert store.list_nodes(True)[0]['archived_at']
    with pytest.raises(ValueError):
        store.purge(node['id'])
    assert store.restore(node['id'])['archived_at'] is None
    store.archive(node['id'])
    future = _now() + timedelta(days=8)
    monkeypatch.setattr('lifeev.storage._now', lambda: future)
    with pytest.raises(ValueError, match='expired'):
        store.restore(node['id'])
    snapshots_before = len(list(store.path.parent.rglob('*.fernet')))
    store.purge(node['id'])
    assert len(list(store.path.parent.rglob('*.fernet'))) > snapshots_before
    with pytest.raises(KeyError):
        store.get(node['id'])


def test_failed_snapshot_blocks_purge(store, monkeypatch):
    node = store.create({})
    store.archive(node['id'])
    future = _now() + timedelta(days=8)
    monkeypatch.setattr('lifeev.storage._now', lambda: future)
    def fail(reason):
        raise OSError('disk full')
    monkeypatch.setattr(store, 'snapshot', fail)
    with pytest.raises(OSError):
        store.purge(node['id'])
    assert store.get(node['id'])


def test_concurrent_revisions_and_invalid_payload(store):
    node = store.create({})
    with ThreadPoolExecutor(max_workers=4) as executor:
        versions = list(executor.map(lambda n: store.revise(node['id'], {'n': n})['version'], range(12)))
    assert sorted(versions) == list(range(2, 14))
    with pytest.raises(ValueError):
        store.revise(node['id'], {'value': float('nan')})
    assert store.get(node['id'])['version'] == 13
    with pytest.raises(KeyError):
        store.create({}, 'missing')
