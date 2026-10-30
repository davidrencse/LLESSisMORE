"""Regression tests for the three resource-exhaustion findings."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from lifeev.api import create_app
from lifeev.example import example
from lifeev.storage import Store, SnapshotCapacityError, SnapshotThrottled, _now
from lifeev.simulation import _SIMULATION_GATE


def files(store):
    return set(store.path.parent.glob(store.path.name + '.snapshots/*.fernet'))


def test_invalid_and_repeated_archive_requests_create_no_backups(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    store = make_store(tmp_path / 'db')
    client = TestClient(create_app(store))
    # Includes a fresh store with no daily backup yet.
    for _ in range(3):
        assert client.post('/api/nodes/missing/archive').status_code == 404
    assert files(store) == set()
    assert store.audit() == []
    node = store.create({})
    before = files(store)
    assert client.post(f"/api/nodes/{node['id']}/archive").status_code == 200
    after = files(store)
    assert len(after - before) == 1
    future = _now() + timedelta(days=1)
    monkeypatch.setattr('lifeev.storage._now', lambda: future)
    for _ in range(3):
        assert client.post(f"/api/nodes/{node['id']}/archive").status_code == 200
    assert files(store) == after


def test_backup_failure_leaves_archive_target_active(tmp_path, monkeypatch, make_store):
    store = make_store(tmp_path / 'db')
    node = store.create({})
    def fail(reason):
        raise OSError('disk full')
    monkeypatch.setattr(store, 'snapshot', fail)
    with pytest.raises(OSError):
        store.archive(node['id'])
    assert store.get(node['id'])['archived_at'] is None


def test_manual_snapshot_cooldown_persists_across_store_reopen(tmp_path, monkeypatch, make_store):
    store = make_store(tmp_path / 'db')
    snapshot = store.snapshot('manual')
    second = Store(store.path, make_store.key)
    with pytest.raises(SnapshotThrottled) as blocked:
        second.snapshot('manual')
    assert 1 <= blocked.value.retry_after <= 60
    assert files(store) == {Path(snapshot)}
    future = _now() + timedelta(seconds=61)
    monkeypatch.setattr('lifeev.storage._now', lambda: future)
    second.snapshot('manual')
    assert len(files(store)) == 2


def test_snapshot_caps_preserve_existing_backups_and_abort_archive(tmp_path, make_store):
    store = make_store(tmp_path / 'db', snapshot_max_files=1)
    node = store.create({})  # The required daily backup consumes the single slot.
    before = {file: file.read_bytes() for file in files(store)}
    with pytest.raises(SnapshotCapacityError):
        store.archive(node['id'])
    assert store.get(node['id'])['archived_at'] is None
    assert {file: file.read_bytes() for file in files(store)} == before


@pytest.mark.parametrize('limit', ['snapshot_max_bytes', 'snapshot_max_file_bytes'])
def test_snapshot_size_budget_rejects_before_encryption_or_file_creation(tmp_path, limit, make_store):
    store = make_store(tmp_path / 'db', **{limit: 100})
    with patch.object(store._cipher, 'encrypt') as encrypt:
        with pytest.raises(SnapshotCapacityError):
            store.snapshot('manual')
        encrypt.assert_not_called()
    assert not files(store)


def test_api_snapshot_errors_are_actionable_and_reads_remain_available(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    store = make_store(tmp_path / 'db')
    with TestClient(create_app(store)) as client:
        assert client.post('/api/snapshots').status_code == 200
        throttled = client.post('/api/snapshots')
        assert throttled.status_code == 429
        assert int(throttled.headers['Retry-After']) > 0
    full = make_store(tmp_path / 'full', snapshot_max_bytes=100)
    with TestClient(create_app(full)) as client:
        assert client.get('/api/health').status_code == 200
        assert client.get('/api/nodes').status_code == 200
        assert client.post('/api/snapshots').status_code == 507


def test_api_simulation_budget_rejects_original_attack_before_preparation(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    store = make_store(tmp_path / 'db')
    doc = example()
    option = deepcopy(doc['options'][0])
    doc['options'] = [dict(deepcopy(option), id=str(i)) for i in range(30)]
    parent = store.create(doc)
    child = store.create(doc, parent['id'])
    client = TestClient(create_app(store))
    with patch('lifeev.simulation._prepare') as prepare:
        response = client.post('/api/simulate', json={'node_ids': [parent['id'], child['id']], 'samples': 100000})
        assert response.status_code == 422
        assert 'reduce samples or options' in response.json()['detail']
        prepare.assert_not_called()


def test_api_simulation_busy_returns_retry_not_server_error(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    store = make_store(tmp_path / 'db')
    node = store.create(example())
    client = TestClient(create_app(store))
    assert _SIMULATION_GATE.acquire(blocking=False)
    try:
        response = client.post('/api/simulate', json={'node_ids': [node['id']], 'samples': 100})
        assert response.status_code == 429
        assert response.headers['Retry-After'] == '1'
        assert client.get('/api/health').status_code == 200
    finally:
        _SIMULATION_GATE.release()
    assert client.post('/api/simulate', json={'node_ids': [node['id']], 'samples': 100}).status_code == 200
