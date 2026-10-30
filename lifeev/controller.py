"""Persisted, deterministic clarification workflow (offline; no model is called)."""
from copy import deepcopy
from functools import wraps
import uuid

from .skeleton import gaps, validate
from .controls import route, Retriever
from .model_adapter import generate_questions


def _serialized(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self.store._lock:
            return method(self, *args, **kwargs)
    return locked


def _check(document):
    errors = validate(document)
    if errors:
        raise ValueError('; '.join(errors))


def _set(document, field, value):
    if field == 'status_quo':
        options = document.get('options', [])
        matches = [o for o in options if o.get('id') == value]
        if len(matches) != 1:
            raise ValueError('status_quo must identify an existing option ID')
        for option in options:
            option['status_quo'] = option['id'] == value
        return
    parts = field.split('.')
    current = document
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current.setdefault(part, {})
    if isinstance(current, list):
        current[int(parts[-1])] = deepcopy(value)
    else:
        current[parts[-1]] = deepcopy(value)


class Controller:
    def __init__(self, store):
        self.store = store
        self.retriever = Retriever()
        self._memory = {}  # decoded confirmed memory by id, kept in step with the retriever's stamps

    def _next(self, session):
        missing = gaps(session['document'])
        if not missing:
            session.update(questions=[], status='ready')
        elif session['round'] >= session['round_cap']:
            for gap in missing:
                field = gap['field']
                session['document'].setdefault('skipped', []).append(field)
                session['document'].setdefault('skip_provenance', []).append({'field': field, 'reason': 'round cap reached; unresolved uncertainty'})
                session['history'].append({'field': field, 'skip': True,
                    'provenance': 'round cap reached; unresolved uncertainty', 'round': session['round']})
            session.update(questions=[], status='ready')
        else:
            session.update(questions=missing[:3], status='questioning')
            tier = route(session['document'], force_tier=session.get('force_tier'), confidence=0.5)['tier']
            context = session.get('retrieved_context', []) + session.get('memory_context', [])
            generated = generate_questions(session['document'], session['questions'], context, tier)
            session['questions'] = generated['questions']
            session['question_provenance'] = generated['provenance']
            session['routing_tier'] = tier
        return session

    @_serialized
    def retrieve(self, query, pins=None):
        """Shared, serialized index for search and clarification context."""
        # Index only active nodes and confirmed memory; records unchanged since the last call are neither
        # decrypted nor re-embedded.
        active = self.retriever
        with active.lock:
            active_ids, seen = set(), set()
            for id, version, node in self.store.iter_nodes_stamped(lambda i, v: active.known(i, ('node', v))):
                seen.add(id)
                if active.sync(id, ('node', version), lambda: str(node['document'])):
                    active_ids.add(id)
            for id, blob, memory in self.store.iter_objects_stamped('memory', lambda i, b: active.known(i, ('memory', b))):
                seen.add(id)
                confirmed = memory is not None and memory.get('confirmed') is True
                if confirmed:
                    self._memory[id] = memory
                if active.sync(id, ('memory', blob), lambda: str(memory) if confirmed else None):
                    active_ids.add(id)
            active.retain(active_ids, seen)
            for stale in [i for i in self._memory if i not in active_ids]:
                del self._memory[stale]
            return active.search(query, pins=pins)

    @_serialized
    def start(self, document, parent_id=None, pins=None, force_tier=None, parent_version=None):
        _check(document)
        if parent_version is not None and (isinstance(parent_version, bool) or not isinstance(parent_version, int) or parent_version < 1 or not parent_id):
            raise ValueError('parent_version requires a parent ID and positive integer version')
        route(document, force_tier=force_tier)
        parent = None
        if parent_id:
            parent = self.store.get(parent_id, parent_version)
            if parent.get('archived_at'):
                raise ValueError('Parent is archived')
        context = self.retrieve(document.get('title', ''), pins)
        memory_ids = set(self._memory)
        selected_ids = {entry['id'] for entry in context}
        session = dict(id=str(uuid.uuid4()), document=deepcopy(document), parent_id=parent_id,
            parent_version=parent['version'] if parent else None, force_tier=force_tier, retrieved_context=[entry for entry in context if entry['id'] not in memory_ids],
            round=0, round_cap=3, questions=[], history=[], status='questioning',
            question_method='deterministic prioritized skeleton gaps; offline fallback',
            memory_context=[deepcopy(m) for m in self._memory.values() if m['id'] in selected_ids])
        return self.store.save_session(self._next(session))

    @_serialized
    def answer(self, session_id, field, value=None, skip=False):
        session = self.resume(session_id)
        if session['status'] != 'questioning':
            raise ValueError('Session is not accepting answers')
        if not isinstance(skip, bool):
            raise ValueError('skip must be boolean')
        if field not in [q['field'] for q in session['questions']]:
            raise ValueError('Answer must address a current question')
        if skip:
            session['document'].setdefault('skipped', []).append(field)
            session['document'].setdefault('skip_provenance', []).append({'field': field, 'reason': 'explicit user skip'})
        else:
            _set(session['document'], field, value)
        _check(session['document'])
        session['history'].append(dict(field=field, value=deepcopy(value) if not skip else None,
            skip=skip, provenance='explicit user skip' if skip else 'user answer', round=session['round']))
        remaining_fields = {q['field'] for q in gaps(session['document'])}
        session['questions'] = [q for q in session['questions'] if q['field'] != field and q['field'] in remaining_fields]
        if not session['questions']:
            session['round'] += 1
            self._next(session)
        return self.store.save_session(session)

    def resume(self, session_id):
        session = self.store.get_session(session_id)
        _check(session['document'])
        return session

    @_serialized
    def finalize(self, session_id):
        session = self.resume(session_id)
        if session['status'] == 'finalized':
            return self.store.get(session['node_id'], session['node_version'])
        if session['status'] != 'ready' or gaps(session['document']):
            raise ValueError('Resolve or explicitly skip remaining questions before finalizing')
        node = self.store.create(session['document'], session.get('parent_id'), session.get('parent_version'))
        session.update(status='finalized', node_id=node['id'], node_version=node['version'])
        self.store.save_session(session)
        return node


def propose_memory(store, key, value, confirmed=False, reviewed_conflicts=None):
    with store._lock:
        return _propose_memory(store, key, value, confirmed, reviewed_conflicts)


def _propose_memory(store, key, value, confirmed=False, reviewed_conflicts=None):
    """Keep proposals separate; conflict IDs must be explicitly reviewed to confirm."""
    if not isinstance(key, str) or not key.strip() or not isinstance(confirmed, bool):
        raise ValueError('Memory needs a nonempty key and boolean confirmation')
    conflicts = [m for m in store.list_memory() if m.get('key') == key
                 and m.get('confirmed') is True and m.get('value') != value]
    conflict_ids = [m['id'] for m in conflicts]
    if confirmed and not set(conflict_ids).issubset(set(reviewed_conflicts or [])):
        raise ValueError('Explicit conflict review required: ' + ', '.join(conflict_ids))
    result = store.add_memory(dict(key=key, value=deepcopy(value), confirmed=confirmed,
        conflicts=conflict_ids, reviewed_conflicts=list(reviewed_conflicts or []),
        provenance='explicit confirmation' if confirmed else 'unconfirmed proposal'))
    if confirmed:
        for old in conflicts:
            old.update(confirmed=False, superseded_by=result['id'])
            store.add_memory(old)
    return result

