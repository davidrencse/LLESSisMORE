from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.example import example
from lifeev.storage import Store
from lifeev.skeleton import validate


def test_statistics_frozen_with_each_version(tmp_path, make_store):
    store = make_store(tmp_path / 'db', backed_up=True)
    doc = example()
    first = store.create(doc)
    assert first['statistics_snapshot']['options'][0]['dimensions']['money']['ev'] == 40
    doc['options'][0]['costs']['money'] = 10
    second = store.revise(first['id'], doc)
    assert second['statistics_snapshot']['options'][0]['dimensions']['money']['ev'] == 30
    assert store.get(first['id'], 1)['statistics_snapshot'] == first['statistics_snapshot']


def test_custom_dimensions_are_explicit():
    assert validate({'weights': {'opportunity': 2, 'health': 3}}) == []


def test_oversized_undeclared_request_and_bad_length(tmp_path, make_store):
    app = create_app(make_store(tmp_path / 'db', backed_up=True))
    client = TestClient(app)
    response = client.post('/api/analyze', content=iter([b'x' * 600_000, b'y' * 600_000]))
    assert response.status_code == 413
    assert client.post('/api/analyze', headers={'content-length': 'invalid'}, content='{}').status_code == 400


def test_static_assets_served(tmp_path, make_store):
    client = TestClient(create_app(make_store(tmp_path / 'db', backed_up=True)))
    assert 'Decision notebook' in client.get('/').text
    assert client.get('/app.js').status_code == 200
    assert client.get('/style.css').status_code == 200
