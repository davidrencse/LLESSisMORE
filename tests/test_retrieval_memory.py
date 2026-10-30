"""Shared-index, compact-listing and bounded-shortlist regression coverage."""
import json

import pytest
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.controls import Retriever
from lifeev.controller import Controller
from lifeev.example import example


def test_search_and_sessions_reuse_the_same_decoded_corpus(tmp_path, make_store, monkeypatch):
    store = make_store(tmp_path / 'shared.sqlite', backed_up=True)
    store.create({'title': 'career'})
    store.add_memory({'id': 'm', 'key': 'risk', 'value': 'low', 'confirmed': True})
    with TestClient(create_app(store)) as client:
        first = client.post('/api/retrieve', json={'query': 'career'}).json()
        decoded = []
        original = store._decode
        monkeypatch.setattr(store, '_daily', lambda: None)
        monkeypatch.setattr(store, '_decode', lambda value: decoded.append(1) or original(value))
        session = client.post('/api/sessions', json={'document': {'title': 'career'}})
        assert session.status_code == 200
        assert [m['id'] for m in session.json()['memory_context']] == ['m']
        assert client.post('/api/retrieve', json={'query': 'career'}).json() == first
        assert decoded == []


def test_memory_stamps_are_fixed_size_and_changed_records_refresh(tmp_path, make_store):
    store = make_store(tmp_path / 'stamps.sqlite', backed_up=True)
    store.add_memory({'id': 'm', 'value': 'large' * 10000, 'confirmed': True})
    controller = Controller(store)
    first = controller.retrieve('large')
    stamp = controller.retriever._stamps['m'][1]
    assert isinstance(stamp, bytes) and len(stamp) == 32
    store.add_memory({'id': 'm', 'value': 'changed', 'confirmed': True})
    assert controller.retrieve('large') != first
    assert controller.retriever._stamps['m'][1] != stamp
    store.add_memory({'id': 'm', 'value': 'changed', 'confirmed': False, 'status': 'confirmed'})
    assert controller.retrieve('large') == []
    assert controller._memory == {}


@pytest.mark.parametrize('pins', [[], ['099'], ['099', '098']])
@pytest.mark.parametrize('top_k', [2, 5, 30])
def test_heap_shortlist_matches_full_sort_with_ties_and_pins(monkeypatch, pins, top_k):
    retriever = Retriever(lambda text: [1., len(text) % 3], lambda q, t: len(t) % 7)
    for i in range(100):
        retriever.upsert(f'{i:03}', 'topic ' + 'x' * i, confirmed=i % 10 != 0)
    actual = retriever.search('topic', pins, top_k)
    monkeypatch.setattr('lifeev.controls.heapq.nsmallest', lambda n, items, key: sorted(items, key=key)[:n])
    assert retriever.search('topic', pins, top_k) == actual


def test_summary_listings_keep_metadata_without_documents_or_snapshots(tmp_path, make_store):
    store = make_store(tmp_path / 'summary.sqlite', backed_up=True)
    parent = store.create(example())
    child = store.create(example(), parent['id'])
    session = Controller(store).start({'title': 'Draft', 'intake_notes': ['private details']})
    with TestClient(create_app(store)) as client:
        full = client.get('/api/nodes').json()
        compact = client.get('/api/nodes?summary=true').json()
        assert [n['id'] for n in compact] == [n['id'] for n in full]
        for short, record in zip(compact, full):
            assert short['document'] == {'title': record['document']['title']}
            assert short['version'] == record['version']
            assert short['parent_id'] == record['parent_id']
            assert 'statistics_snapshot' not in short and 'frozen_parent' not in short
        assert len(json.dumps(compact)) < len(json.dumps(full)) / 10
        assert client.get(f"/api/nodes/{child['id']}").json()['frozen_parent']['id'] == parent['id']
        summaries = client.get('/api/sessions?summary=true').json()
        assert summaries[0]['status'] == session['status']
        assert summaries[0]['document'] == {'title': 'Draft'}
        assert 'history' not in summaries[0]
        assert client.get(f"/api/sessions/{session['id']}").json()['document']['intake_notes'] == ['private details']
    store.archive(child['id'])
    assert len(store.list_nodes(summary=True)) == 1
    assert len(store.list_nodes(True, summary=True)) == 2
