import sqlite3

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import pytest

from lifeev.api import create_app
from lifeev.controls import Retriever
from lifeev.storage import Store


@pytest.fixture
def store(tmp_path, make_store):
    # Daily snapshot already taken: none of these tests examine backups.
    return make_store(tmp_path / 'e.sqlite', backed_up=True)


@pytest.fixture
def client(store, monkeypatch):
    monkeypatch.delenv('LIFEEV_API_TOKEN', raising=False)
    with TestClient(create_app(store), raise_server_exceptions=False) as c:
        yield c


def naive_list_nodes(store, include_archived=False):
    with store._connection() as db:
        ids = [row[0] for row in db.execute('SELECT DISTINCT id FROM nodes')]
        nodes = [store._get(db, i) for i in ids]
    return [n for n in nodes if include_archived or not n['archived_at']]


def count_decodes(store, monkeypatch):
    calls = []
    original = store._decode
    monkeypatch.setattr(store, '_decode', lambda v: calls.append(1) or original(v))
    return calls


def test_list_nodes_matches_per_node_reads(history):
    store = history
    assert store.list_nodes() == naive_list_nodes(store)
    assert store.list_nodes(True) == naive_list_nodes(store, True)
    assert len(store.list_nodes()) == 4 and len(store.list_nodes(True)) == 6
    ids = [n['id'] for n in store.list_nodes(True)]
    assert ids == sorted(ids)
    revised = [n for n in store.list_nodes(True) if n['document']['title'].startswith('revised')]
    assert len(revised) == 2 and all(n['version'] == 2 for n in revised)
    assert all(n['version'] == 1 for n in store.list_nodes(True) if n not in revised)


def test_list_nodes_empty(store):
    assert store.list_nodes() == [] and store.list_nodes(True) == []


def test_list_nodes_single_decrypt_per_node(store, monkeypatch):
    for i in range(4):
        store.create({'title': str(i)})
    calls = count_decodes(store, monkeypatch)
    store.list_nodes()
    assert len(calls) == 4


def test_payloads_still_encrypted_after_writes(store):
    secret = 'EFFICIENCY_SECRET_5521'
    node = store.create({'title': secret})
    store.revise(node['id'], {'title': secret + 'x'})
    store.add_memory({'key': 'k', 'value': secret, 'confirmed': True})
    with sqlite3.connect(store.path) as db:
        raw = b''.join(bytes(r[0]) for r in db.execute(
            'SELECT payload FROM nodes UNION ALL SELECT payload FROM objects UNION ALL SELECT payload FROM events'))
    assert secret.encode() not in raw


def test_write_returns_independent_normalised_copy(store):
    node = store.create({'title': 't', 'n': 1})
    node['document']['title'] = 'mutated'
    assert store.get(node['id'])['document']['title'] == 't'
    with pytest.raises(ValueError):
        store.create({'bad': float('nan')})


@pytest.fixture(scope='module')
def history(tmp_path_factory):
    """One read-only store shared by every test that only inspects history: six nodes (two revised, two
    archived) with session/memory saves interleaved among the writes, so the log mixes create, revise,
    archive, save_session, save_memory and snapshot events (13 in all, more than every tested limit
    except 100, which exceeds the whole log).  Built fresh and not backed_up so the first write's snapshot
    event is in the log.  Tests must not write to it."""
    store = Store(tmp_path_factory.mktemp('history') / 'h.sqlite', Fernet.generate_key())
    n = [store.create({'title': f'n{i}'}) for i in range(3)]
    store.revise(n[0]['id'], {'title': 'revised n0'})
    store.save_session({'id': 's1'})
    n += [store.create({'title': f'n{i}'}) for i in range(3, 6)]
    store.revise(n[3]['id'], {'title': 'revised n3'})
    store.add_memory({'id': 'm1', 'confirmed': True})
    store.archive(n[1]['id'])
    store.archive(n[4]['id'])
    return store


@pytest.fixture
def history_client(history):
    with TestClient(create_app(history), raise_server_exceptions=False) as c:
        yield c


@pytest.mark.parametrize('action', [None, '', 'create', 'revise', 'archive', 'snapshot', 'save_memory', 'missing'])
@pytest.mark.parametrize('limit', [None, 1, 2, 5, 6, 100])
def test_audit_matches_naive_filter(history, action, limit):
    expected = history.audit()
    assert {'create', 'revise', 'archive', 'snapshot', 'save_session', 'save_memory'} <= {e['action'] for e in expected}
    assert len(expected) > 6
    if action:
        expected = [e for e in expected if e.get('action') == action]
    if limit is not None:
        expected = expected[-limit:]
    assert history.audit(action, limit) == expected


def test_audit_decrypts_only_what_it_needs(history, monkeypatch):
    total = len(history.audit())
    calls = count_decodes(history, monkeypatch)
    assert len(history.audit(limit=3)) == 3
    assert len(calls) == 3 < total


def test_audit_route(history_client, history):
    client, store = history_client, history
    everything = client.get('/api/audit').json()
    assert everything == store.audit()
    assert client.get('/api/audit?limit=3').json() == everything[-3:]
    creates = [e for e in everything if e['action'] == 'create']
    assert client.get('/api/audit?action=create').json() == creates
    assert client.get('/api/audit?action=create&limit=2').json() == creates[-2:]
    assert client.get('/api/audit?limit=0').status_code != 200


def test_retriever_upsert_skips_unchanged_text():
    embedded = []
    r = Retriever(lambda t: embedded.append(t) or [1., len(t)])
    r.upsert('a', 'hello world')
    r.upsert('a', 'hello world')
    r.upsert('a', 'hello world', confirmed=False)
    assert embedded == ['hello world'] and r._records['a']['confirmed'] is False
    r.upsert('a', 'changed')
    assert embedded == ['hello world', 'changed']


def test_retriever_search_unchanged_by_cached_norms():
    r = Retriever()
    for i, t in enumerate(['risk of failure', 'safe option', 'moving abroad plan', 'risk plan']):
        r.upsert(str(i), t)
    cached = r.search('risk plan', top_k=4)
    for rec in r._records.values():
        rec.pop('sq')
    assert r.search('risk plan', top_k=4) == cached


def test_retriever_sync_and_retain():
    r = Retriever()
    loads = []
    load = lambda text: lambda: loads.append(text) or text
    assert r.sync('a', 1, load('alpha')) and r.sync('a', 1, load('alpha')) and loads == ['alpha']
    assert r.sync('a', 2, load('beta')) and r._records['a']['text'] == 'beta'
    assert not r.sync('u', 'x', lambda: None) and 'u' not in r._records
    assert not r.sync('u', 'x', load('never')) and loads == ['alpha', 'beta']
    r.upsert('a', 'direct')
    assert not r.known('a', 2)
    r.retain({'a'}, {'a'})
    assert 'u' not in r._stamps and set(r._records) == {'a'}
    r.retain(set())
    assert not r._records and not r._stamps


def doc(title):
    return {'title': title, 'options': []}


def retrieve(client, query, **extra):
    return client.post('/api/retrieve', json={'query': query, **extra})


def test_retrieve_repeated_calls_identical_and_incremental(client, store, monkeypatch):
    for t in ['career change plan', 'buy a house', 'learn piano']:
        store.create(doc(t))
    store.add_memory({'id': 'mem1', 'key': 'prefers', 'value': 'low risk piano', 'confirmed': True})
    store.add_memory({'id': 'mem2', 'key': 'draft', 'value': 'secret draft', 'confirmed': False})
    first = retrieve(client, 'piano').json()
    assert 'mem1' in {r['id'] for r in first} and all('secret draft' not in r['text'] for r in first)
    calls = count_decodes(store, monkeypatch)
    for _ in range(3):
        assert retrieve(client, 'piano').json() == first
    assert calls == []
    with TestClient(create_app(store)) as cold:
        assert retrieve(cold, 'piano').json() == first


def test_retrieve_follows_revisions_memory_edits_and_archives(client, store):
    a = store.create(doc('alpha topic'))
    b = store.create(doc('beta topic'))
    store.add_memory({'id': 'm', 'value': 'gamma memory', 'confirmed': True})
    ids = lambda q: {r['id'] for r in retrieve(client, q).json()}
    texts = lambda q: {r['id']: r['text'] for r in retrieve(client, q).json()}
    assert ids('topic memory') == {a['id'], b['id'], 'm'}
    store.revise(a['id'], doc('zeta renamed'))
    assert 'zeta' in texts('zeta')[a['id']]
    store.add_memory({'id': 'm', 'value': 'gamma memory', 'confirmed': False})
    assert 'm' not in ids('memory')
    store.add_memory({'id': 'm', 'value': 'delta memory', 'confirmed': True})
    assert 'delta' in texts('delta')['m']
    store.archive(b['id'])
    assert b['id'] not in ids('beta topic')
    store.restore(b['id'])
    assert b['id'] in ids('beta topic')
    resp = retrieve(client, 'x', pins=[b['id']])
    assert resp.status_code == 200 and resp.json()[0]['id'] == b['id']
    store.archive(b['id'])
    assert retrieve(client, 'x', pins=[b['id']]).status_code != 200
