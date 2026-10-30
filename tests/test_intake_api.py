"""Review-only intake boundary, including provider and incremental flows."""
from copy import deepcopy

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.storage import Store


@pytest.fixture
def intake_client(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    for name in ('LIFEEV_MODEL_BASE_URL', 'LIFEEV_MODEL_API_KEY', 'LIFEEV_MODEL_SMALL'):
        monkeypatch.delenv(name, raising=False)
    store = make_store(tmp_path / 'intake.sqlite', backed_up=True)
    with TestClient(create_app(store), raise_server_exceptions=False) as client:
        yield client, store


def snapshot(store):
    return deepcopy((store.list_nodes(include_archived=True), store.list_sessions(), store.list_memory()))


def test_preview_is_only_proposal_and_exposes_gaps(intake_client):
    client, store = intake_client
    before = snapshot(store)
    response = client.post('/api/intake/preview', json={'text': 'I am deciding between Stay and Go.'})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert [o['label'] for o in proposal['document']['options']] == ['Stay', 'Go']
    assert {'bankroll', 'weights', 'horizon'} <= {g['field'] for g in proposal['gaps']}
    assert snapshot(store) == before


def test_incremental_conflicts_preserve_source_and_saved_node(intake_client):
    client, store = intake_client
    source = {'title': 'Original', 'options': [{'id': 'stay', 'label': 'Stay', 'costs': {'money': 10}}], 'intake_notes': ['Earlier input']}
    original = deepcopy(source)
    saved = client.post('/api/nodes', json={'document': source})
    assert saved.status_code == 200, saved.text
    before = snapshot(store)
    response = client.post('/api/intake/preview', json={'document': source, 'text': 'Title: Revised\nCost Stay: money=20'})
    assert response.status_code == 200, response.text
    proposal = response.json()
    conflicts = {row['field']: row for row in proposal['changes']}
    assert conflicts['title']['before'] == 'Original'
    assert conflicts['title']['after'] == 'Revised'
    assert conflicts['options']['before'][0]['costs']['money'] == 10
    assert conflicts['options']['after'][0]['costs']['money'] == 20
    assert proposal['document']['intake_notes'] == ['Earlier input', 'Title: Revised\nCost Stay: money=20']
    assert source == original
    assert snapshot(store) == before
    assert client.get('/api/nodes/' + saved.json()['id']).json()['document'] == original


@pytest.mark.parametrize('body', [
    {'text': 'hello', 'use_model': 'yes'},
    {'text': 'hello', 'use_model': 1},
    {'text': 'x' * 20001},
    {'text': ''},
])
def test_invalid_inputs_are_422(intake_client, body):
    client, store = intake_client
    before = snapshot(store)
    response = client.post('/api/intake/preview', json=body)
    assert response.status_code == 422, response.text
    assert snapshot(store) == before


def test_unconfigured_provider_falls_back(intake_client):
    client, store = intake_client
    before = snapshot(store)
    response = client.post('/api/intake/preview', json={'text': 'Options: Stay vs Go', 'use_model': True})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert proposal['method'] == 'deterministic'
    assert proposal['provenance']['reason'] == 'not_configured'
    assert proposal['warnings']
    assert len(proposal['document']['options']) == 2
    assert snapshot(store) == before


def test_provider_keeps_raw_notes_and_cannot_persist(intake_client, monkeypatch):
    client, store = intake_client
    source = {'title': 'Old', 'intake_notes': ['Original context']}
    original = deepcopy(source)
    text = 'Please call this decision New.'
    calls = []

    def extract(raw, document):
        calls.append((raw, deepcopy(document)))
        candidate = deepcopy(document)
        candidate.update(title='New', intake_notes=['Provider attempted replacement'])
        return {'document': candidate, 'warnings': [], 'provenance': {'source': 'provider', 'model': 'test'}}

    monkeypatch.setattr('lifeev.intake_service.extract', extract)
    before = snapshot(store)
    response = client.post('/api/intake/preview', json={'text': text, 'document': source, 'use_model': True})
    assert response.status_code == 200, response.text
    proposal = response.json()
    assert calls == [(text, original)]
    assert proposal['document']['title'] == 'New'
    assert proposal['document']['intake_notes'] == ['Original context', text]
    assert proposal['provenance']['source'] == 'provider'
    assert next(row for row in proposal['changes'] if row['field'] == 'title') == {'field': 'title', 'before': 'Old', 'after': 'New'}
    assert source == original
    assert snapshot(store) == before
