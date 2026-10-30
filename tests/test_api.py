from copy import deepcopy

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.example import example
from lifeev.storage import Store


@pytest.fixture
def client(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    store = make_store(tmp_path / 'api.sqlite', backed_up=True)
    with TestClient(create_app(store), raise_server_exceptions=False) as client:
        yield client


def create(client, document=None, parent_id=None):
    response = client.post('/api/nodes', json={'document': document or example(), 'parent_id': parent_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_revision_freezes_child_parent_and_archive_restore(client):
    parent = create(client)
    child = create(client, parent_id=parent['id'])
    revised = example()
    revised['title'] = 'Revised parent'
    result = client.post(f"/api/nodes/{parent['id']}/versions", json={'document': revised})
    assert result.status_code == 200
    assert result.json()['version'] == 2
    frozen = client.get(f"/api/nodes/{child['id']}").json()
    assert frozen['parent_version'] == 1
    assert frozen['frozen_parent']['document'] == parent['document']
    assert client.get(f"/api/nodes/{parent['id']}?version=1").json()['document'] == parent['document']
    assert client.post(f"/api/nodes/{child['id']}/archive").status_code == 200
    assert child['id'] not in [n['id'] for n in client.get('/api/nodes').json()]
    assert child['id'] in [n['id'] for n in client.get('/api/nodes?include_archived=true').json()]
    assert client.post(f"/api/nodes/{child['id']}/restore").status_code == 200
    assert child['id'] in [n['id'] for n in client.get('/api/nodes').json()]


def test_analysis_tiers_cache_and_revision_invalidation(client):
    document = example()
    document['options'] = document['options'][:1]
    document['options'][0]['outcomes'] = [document['options'][0]['outcomes'][1]]
    outcome = document['options'][0]['outcomes'][0]
    outcome.update(probability=1, probability_low=1, probability_high=1)
    for tier in ('small', 'medium', 'large'):
        body = {'document': document, 'force_tier': tier}
        first = client.post('/api/analyze', json=body)
        assert first.status_code == 200, first.text
        assert first.json()['routing']['tier'] == tier
        assert not first.json()['cache_hit']
        assert client.post('/api/analyze', json=body).json()['cache_hit']
    node = create(client, document)
    url = f"/api/nodes/{node['id']}/stats"
    assert not client.get(url).json()['cache_hit']
    assert client.get(url).json()['cache_hit']
    assert client.post(f"/api/nodes/{node['id']}/versions", json={'document': document}).status_code == 200
    assert not client.get(url).json()['cache_hit']


def test_question_skip_resume_finalize(client):
    session = client.post('/api/sessions', json={'document': {}}).json()
    url = f"/api/sessions/{session['id']}"
    assert client.post(url + '/finalize').status_code == 422
    assert client.post(url + '/answers', json={'field': 'not-asked', 'value': 1}).status_code == 422
    while session['status'] == 'questioning':
        assert 1 <= len(session['questions']) <= 3
        response = client.post(url + '/answers', json={'field': session['questions'][0]['field'], 'skip': True})
        assert response.status_code == 200, response.text
        session = response.json()
        assert client.get(url).json() == session
    assert session['round'] <= 3
    assert session['history'] and all(h['provenance'] for h in session['history'])
    node = client.post(url + '/finalize').json()
    assert node['document']['skip_provenance']
    assert client.post(url + '/finalize').json() == node
    assert client.get(url).json()['status'] == 'finalized'


def test_memory_conflict_review_and_context(client):
    proposal = client.post('/api/memory', json={'key': 'risk', 'value': 'low'}).json()
    first = client.post('/api/memory', json={'key': 'risk', 'value': 'medium', 'confirmed': True}).json()
    body = {'key': 'risk', 'value': 'high', 'confirmed': True}
    assert client.post('/api/memory', json=body).status_code == 422
    body['reviewed_conflicts'] = [first['id']]
    second = client.post('/api/memory', json=body).json()
    memory = client.get('/api/memory').json()
    assert not next(m for m in memory if m['id'] == first['id'])['confirmed']
    assert not proposal['confirmed']
    session = client.post('/api/sessions', json={'document': {}}).json()
    assert [m['id'] for m in session['memory_context']] == [second['id']]


def test_simulation_link_scope_and_branch_limit(client):
    parent = create(client)
    child = create(client, parent_id=parent['id'])
    other = create(client)
    def simulate(ids):
        return client.post('/api/simulate', json={'node_ids': ids, 'samples': 100, 'seed': 7})
    assert simulate([parent['id']]).status_code == 200
    pair = simulate([parent['id'], child['id']])
    assert pair.status_code == 200, pair.text
    assert pair.json()['pairs']
    assert simulate([parent['id'], other['id']]).status_code == 422
    assert simulate([parent['id'], parent['id']]).status_code == 422
    assert simulate([]).status_code == 422
    assert simulate([parent['id'], child['id'], other['id']]).status_code == 422
    create(client, parent_id=parent['id'])
    create(client, parent_id=parent['id'])
    assert simulate([parent['id']]).status_code == 422


def test_health_reports_model_provider_configuration(client, monkeypatch):
    monkeypatch.delenv('LIFEEV_MODEL_BASE_URL', raising=False)
    monkeypatch.delenv('LIFEEV_MODEL_API_KEY', raising=False)
    assert client.get('/api/health').json()['model_provider'] == 'not configured'
    monkeypatch.setenv('LIFEEV_MODEL_BASE_URL', 'https://provider.example/v1')
    assert client.get('/api/health').json()['model_provider'] == 'not configured'
    monkeypatch.setenv('LIFEEV_MODEL_API_KEY', 'test-key')
    assert client.get('/api/health').json()['model_provider'] == 'configured'


def test_origin_auth_and_security_headers(client, monkeypatch):
    assert client.post('/api/nodes', json={'document': {}}, headers={'Origin': 'https://evil.example'}).status_code == 403
    assert client.get('/api/nodes', headers={'Host': 'evil.example'}).status_code == 400
    assert client.post('/api/nodes', json={'document': {}}, headers={'Origin': 'http://testserver'}).status_code == 200
    monkeypatch.setenv('LIFEEV_API_TOKEN', 'test-secret')
    assert client.get('/api/health').status_code == 401
    assert client.get('/api/health', headers={'Authorization': 'Bearer wrong'}).status_code == 401
    response = client.get('/api/health', headers={'Authorization': 'Bearer test-secret'})
    assert response.status_code == 200
    assert response.headers['x-content-type-options'] == 'nosniff'
    assert response.headers['cache-control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['content-security-policy']


@pytest.mark.parametrize('path,body', [
    ('/api/nodes', {'document': []}),
    ('/api/nodes', {'document': {'bankroll': {'money': -1}}}),
    ('/api/nodes', {'document': {'title': 'x' * 20001}}),
    ('/api/analyze', {'document': {}, 'force_tier': 'invalid'}),
    ('/api/memory', {'key': 'risk', 'value': 'low', 'confirmed': 'yes'}),
    ('/api/simulate', {'node_ids': [], 'samples': 100}),
])
def test_invalid_inputs_are_client_errors(client, path, body):
    response = client.post(path, json=body)
    assert response.status_code == 422, response.text


def test_bad_json_missing_ids_and_request_limit(client):
    assert client.post('/api/nodes', content='{', headers={'Content-Type': 'application/json'}).status_code == 422
    assert client.get('/api/nodes/missing').status_code == 404
    assert client.post('/api/nodes', json={'document': {}}, headers={'Content-Length': '1000001'}).status_code == 413

def test_session_parent_snapshot_is_frozen_at_start(client):
    parent = create(client)
    session = client.post('/api/sessions', json={'document': {}, 'parent_id': parent['id']}).json()
    revised = deepcopy(parent['document'])
    revised['title'] = 'Changed while child clarification is open'
    assert client.post(f"/api/nodes/{parent['id']}/versions", json={'document': revised}).status_code == 200
    url = f"/api/sessions/{session['id']}"
    while session['status'] == 'questioning':
        response = client.post(url + '/answers', json={'field': session['questions'][0]['field'], 'skip': True})
        assert response.status_code == 200, response.text
        session = response.json()
    response = client.post(url + '/finalize')
    assert response.status_code == 200, response.text
    child = response.json()
    assert child['parent_version'] == parent['version']
    assert child['frozen_parent']['document'] == parent['document']
