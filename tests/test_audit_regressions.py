"""Regression cases found during the correctness and memory audit."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import math
import random

import pytest
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.skeleton import validate
from lifeev.simulation import _summary
from lifeev.quick import quick_text


@pytest.mark.parametrize('bounds', [{'probability_low': .7}, {'probability_high': .3}])
def test_point_probability_respects_one_sided_bounds(bounds):
    assert validate({'options': [{'outcomes': [
        {'probability': .5, **bounds}, {'probability': .5}]}]})


@pytest.mark.parametrize('body', [{'pins': [{}]}, {'pins': 'x'}, {'query': []}])
def test_invalid_retrieval_returns_validation_error(tmp_path, make_store, body):
    with TestClient(create_app(make_store(tmp_path / 'a.sqlite')), raise_server_exceptions=False) as client:
        assert client.post('/api/retrieve', json=body).status_code == 422


def test_daily_backup_check_is_serialized(tmp_path, make_store, monkeypatch):
    store = make_store(tmp_path / 'a.sqlite')
    entered, release, competing = Event(), Event(), Event()
    original = store.snapshot
    calls = []

    def snapshot(reason):
        calls.append(reason)
        entered.set()
        assert release.wait(5)
        return original(reason)

    def second():
        competing.set()
        store._daily()

    monkeypatch.setattr(store, 'snapshot', snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(store._daily)
        assert entered.wait(5)
        other = pool.submit(second)
        assert competing.wait(5)
        release.set()
        first.result(timeout=5)
        other.result(timeout=5)
    assert calls == ['daily']


def test_histogram_summary_matches_sample_order_statistics():
    rng = random.Random(82)
    for count in (100, 101, 10000):
        values = [rng.choice([-7., -2., 0., 3., 14.]) for _ in range(count)]
        actual, ordered = _summary(values), sorted(values)
        for key, q in [('p05', .05), ('median', .5), ('p95', .95)]:
            assert actual[key] == ordered[math.ceil(q * count) - 1]
        assert sum(row['count'] for row in actual['distribution']) == count


def test_sparse_game_rejected_before_cartesian_expansion():
    text = '\n'.join(f'If I choice{i} and she choice{i}: 1, 2' for i in range(31))
    with pytest.raises(ValueError, match='at most 30 choices'):
        quick_text(text)

