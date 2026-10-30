from copy import deepcopy

import pytest
from cryptography.fernet import Fernet

from lifeev.controller import Controller, propose_memory
from lifeev.storage import Store


@pytest.fixture
def store(tmp_path, make_store):
    return make_store(tmp_path / 'test.db', backed_up=True)


def test_round_cap_autosaves_and_records_provenance(store):
    controller = Controller(store)
    session = controller.start({})
    assert session['round'] == 0
    while session['status'] == 'questioning':
        assert len(session['questions']) <= 3
        field = session['questions'][0]['field']
        session = controller.answer(session['id'], field, skip=True)
        assert controller.resume(session['id']) == session
    assert session['round'] <= 3
    assert all('provenance' in h for h in session['history'])
    node = controller.finalize(session['id'])
    assert controller.finalize(session['id']) == node
    node['document']['title'] = 'changed locally'
    assert store.get(node['id'])['document'] != node['document']


def test_dotted_answers_validate_before_save(store):
    controller = Controller(store)
    session = controller.start({'options': [{'id': 'a', 'label': 'A', 'status_quo': True,
        'outcomes': [{'label': 'base'}]}], 'weights': {'money': 1}})
    # The first batch prioritizes an outcome collection and bankroll; skip until a leaf appears.
    while not any('.' in q['field'] and not q['field'].endswith('outcomes') for q in session['questions']):
        session = controller.answer(session['id'], session['questions'][0]['field'], skip=True)
    leaf = next(q['field'] for q in session['questions'] if q['field'].endswith('probability'))
    before = deepcopy(session)
    with pytest.raises(ValueError):
        controller.answer(session['id'], leaf, 2)
    assert controller.resume(session['id']) == before
    session = controller.answer(session['id'], leaf, 1)
    assert session['document']['options'][0]['outcomes'][0]['probability'] == 1


def test_reject_unasked_and_premature_finalize(store):
    controller = Controller(store)
    session = controller.start({})
    with pytest.raises(ValueError):
        controller.answer(session['id'], 'made_up', 1)
    with pytest.raises(ValueError):
        controller.finalize(session['id'])
    with pytest.raises(ValueError):
        controller.start({'bankroll': {'money': float('nan')}})


def test_memory_needs_confirmation_and_conflict_review(store):
    proposal = propose_memory(store, 'risk', 'low')
    first = propose_memory(store, 'risk', 'medium', confirmed=True)
    controller = Controller(store)
    session = controller.start({})
    assert [m['id'] for m in session['memory_context']] == [first['id']]
    with pytest.raises(ValueError, match='conflict review'):
        propose_memory(store, 'risk', 'high', confirmed=True)
    second = propose_memory(store, 'risk', 'high', confirmed=True, reviewed_conflicts=[first['id']])
    assert second['confirmed']
    assert not next(m for m in store.list_memory() if m['id'] == first['id'])['confirmed']
    assert not proposal['confirmed']


def _count_decodes(store, monkeypatch):
    calls = []
    original = store._decode
    # Every write re-reads the encrypted "snapshot_day" metadata; that is not record retrieval, so keep it out of the count.
    monkeypatch.setattr(store, '_daily', lambda: None)
    monkeypatch.setattr(store, '_decode', lambda v: calls.append(1) or original(v))
    return calls


def _context(session):
    return ([(e['id'], e['text']) for e in session['retrieved_context']],
            [(m['id'], m['value']) for m in session['memory_context']])


def _seeded(store):
    nodes = [store.create({'title': t, 'options': []}) for t in ['career change plan', 'buy a house', 'learn piano']]
    store.add_memory({'id': 'mem1', 'key': 'prefers', 'value': 'low risk piano', 'confirmed': True})
    store.add_memory({'id': 'mem2', 'key': 'draft', 'value': 'secret draft piano', 'confirmed': False})
    return nodes


def test_start_context_stable_and_repeat_decrypts_nothing(store, monkeypatch):
    nodes = _seeded(store)
    controller = Controller(store)
    first = controller.start({'title': 'piano'})
    ids = {e['id'] for e in first['retrieved_context']}
    assert ids == {n['id'] for n in nodes}
    assert [m['id'] for m in first['memory_context']] == ['mem1']
    assert 'secret draft' not in str(first['retrieved_context'] + first['memory_context'])
    calls = _count_decodes(store, monkeypatch)
    for _ in range(3):
        again = controller.start({'title': 'piano'})
        assert (again['retrieved_context'], again['memory_context']) == (first['retrieved_context'], first['memory_context'])
    assert calls == []
    # A brand-new controller (cold index) sees exactly the same context as the warm one.
    monkeypatch.undo()
    cold = Controller(store).start({'title': 'piano'})
    assert (cold['retrieved_context'], cold['memory_context']) == (first['retrieved_context'], first['memory_context'])


def test_start_decrypts_only_changed_records(store, monkeypatch):
    nodes = _seeded(store)
    controller = Controller(store)
    controller.start({'title': 'piano'})
    store.revise(nodes[2]['id'], {'title': 'learn violin', 'options': []})
    calls = _count_decodes(store, monkeypatch)
    session = controller.start({'title': 'violin'})
    assert len(calls) == 1  # just the revised node, not the other two nodes or the memories
    assert dict(_context(session)[0])[nodes[2]['id']].count('violin') >= 1
    store.add_memory({'id': 'mem1', 'key': 'prefers', 'value': 'high risk violin', 'confirmed': True})
    del calls[:]
    session = controller.start({'title': 'violin'})
    assert len(calls) == 1 and dict(_context(session)[1])['mem1'] == 'high risk violin'
    del calls[:]
    controller.start({'title': 'violin'})
    assert calls == []


def test_start_context_follows_archive_confirmation_and_restore(store):
    nodes = _seeded(store)
    controller = Controller(store)
    pick = lambda title='piano': _context(controller.start({'title': title}))
    assert pick()[1] and 'mem2' not in dict(pick()[1])
    store.archive(nodes[0]['id'])
    assert nodes[0]['id'] not in dict(pick('career')[0])
    store.restore(nodes[0]['id'])
    assert nodes[0]['id'] in dict(pick('career')[0])
    store.add_memory({'id': 'mem1', 'key': 'prefers', 'value': 'low risk piano', 'confirmed': False})
    assert pick()[1] == []
    assert 'mem1' not in dict(pick()[0])
    store.add_memory({'id': 'mem2', 'key': 'draft', 'value': 'secret draft piano', 'confirmed': True})
    assert [m for m, _ in pick()[1]] == ['mem2']
    with pytest.raises(ValueError):
        controller.start({'title': 'piano'}, pins=['mem1'])
    # Matches what a never-used controller computes from the same store.
    assert pick() == _context(Controller(store).start({'title': 'piano'}))
