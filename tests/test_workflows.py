"""Cross-endpoint regressions for guided decision workflows."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.example import example
from lifeev.storage import Store
from lifeev.controller import Controller
from lifeev.skeleton import gaps


def test_incomplete_correlation_remains_a_gap():
    draft = example()
    draft['correlations'] = [{}]
    fields = {gap['field'] for gap in gaps(draft)}
    assert {'correlations.0.node_id', 'correlations.0.rho', 'correlations.0.exposure'} <= fields


@pytest.fixture
def workflow_client(tmp_path, monkeypatch, make_store):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    monkeypatch.delenv('LIFEEV_MODEL_BASE_URL', raising=False)
    store = make_store(tmp_path / 'workflows.sqlite', backed_up=True)
    with TestClient(create_app(store), raise_server_exceptions=False) as client:
        yield client


@pytest.mark.parametrize('endpoint', ['/api/nodes', '/api/sessions'])
def test_branch_uses_explicit_historical_parent(workflow_client, endpoint):
    client = workflow_client
    parent = client.post('/api/nodes', json={'document': example()}).json()
    revised = example()
    revised['title'] = 'A later version'
    assert client.post(f"/api/nodes/{parent['id']}/versions", json={'document': revised}).status_code == 200
    response = client.post(endpoint, json={
        'document': example(), 'parent_id': parent['id'], 'parent_version': 1})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['parent_version'] == 1
    if endpoint == '/api/sessions':
        result = client.post(f"/api/sessions/{result['id']}/finalize").json()
    assert result['frozen_parent']['document'] == parent['document']


def test_replacing_outcome_array_removes_obsolete_questions(workflow_client):
    client = workflow_client
    draft = example()
    draft['options'][0]['outcomes'] = [{'label': 'unclassified'}]
    session = client.post('/api/sessions', json={'document': draft}).json()
    assert session['questions'][0]['field'] == 'options.0.outcomes'
    outcomes = deepcopy(example()['options'][0]['outcomes'])
    response = client.post(f"/api/sessions/{session['id']}/answers", json={
        'field': 'options.0.outcomes', 'value': outcomes})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session['status'] == 'ready'
    assert session['questions'] == []
    assert session['document']['options'][0]['outcomes'] == outcomes


@pytest.mark.parametrize('field,value', [('skip_provenance', {}), ('override_acceptances', {})])
def test_malformed_workflow_metadata_is_rejected(workflow_client, field, value):
    response = workflow_client.post('/api/sessions', json={'document': {field: value}})
    assert response.status_code == 422


def test_memory_conflict_failure_preserves_confirmed_context(workflow_client):
    client = workflow_client
    first = client.post('/api/memory', json={'key': 'risk', 'value': 'low', 'confirmed': True}).json()
    rejected = client.post('/api/memory', json={'key': 'risk', 'value': 'high', 'confirmed': True})
    assert rejected.status_code == 422
    session = client.post('/api/sessions', json={'document': {}, 'pins': [first['id']]}).json()
    assert session['memory_context'] == [first]
    assert len(client.get('/api/memory').json()) == 1


def test_revision_rejects_stale_editor(workflow_client):
    client = workflow_client
    node = client.post('/api/nodes', json={'document': example()}).json()
    url = f"/api/nodes/{node['id']}/versions"
    body = {'document': example(), 'source_version': 1}
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 409
    assert client.get(f"/api/nodes/{node['id']}").json()['version'] == 2


def test_concurrent_answers_and_finalize_are_serialized(tmp_path, make_store):
    store = make_store(tmp_path / 'concurrent.sqlite', backed_up=True)
    controller = Controller(store)
    session = controller.start({})
    fields = [q['field'] for q in session['questions']]
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda field: controller.answer(session['id'], field, skip=True), fields))
    resumed = controller.resume(session['id'])
    assert set(fields).issubset(resumed['document']['skipped'])
    assert len(resumed['history']) >= len(fields)
    ready = controller.start(example())
    with ThreadPoolExecutor(max_workers=3) as pool:
        nodes = list(pool.map(lambda _: controller.finalize(ready['id']), range(3)))
    assert len({node['id'] for node in nodes}) == 1
    assert len(store.list_nodes()) == 1


def test_dimension_periods_are_rejected_before_question_generation(workflow_client):
    response = workflow_client.post('/api/sessions', json={'document': {'weights': {'money.usd': 1}}})
    assert response.status_code == 422
    assert 'periods' in response.json()['detail']
