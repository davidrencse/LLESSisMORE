import json

import httpx
import pytest

from lifeev import intake_model


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('LIFEEV_MODEL_BASE_URL', 'https://example.test/v1')
    monkeypatch.setenv('LIFEEV_MODEL_API_KEY', 'secret')
    monkeypatch.setenv('LIFEEV_MODEL_LARGE', 'extractor')
    real_client = httpx.Client

    def install(updates=None, raw=None, status=200):
        def respond(request):
            payload = json.loads(request.content)
            assert payload['model'] == 'extractor'
            assert payload['response_format'] == {'type': 'json_object'}
            system = payload['messages'][0]['content']
            assert 'Return exactly one JSON object matching this schema:' in system
            schema = json.loads(system.split('matching this schema:\n', 1)[1])
            assert schema['properties']['updates']['anyOf'][0]['additionalProperties'] is False
            assert 'BEGIN_UNTRUSTED_JSON' in payload['messages'][1]['content']
            body = raw if raw is not None else json.dumps({
                'choices': [{'message': {'content': json.dumps({'updates': updates})}}]})
            return httpx.Response(status, content=body)
        monkeypatch.setattr(intake_model.httpx, 'Client',
                            lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    return install


def test_missing_config(monkeypatch):
    monkeypatch.delenv('LIFEEV_MODEL_API_KEY', raising=False)
    assert intake_model.extract('A decision', {})['provenance']['reason'] == 'not_configured'


def test_incremental_proposal_preserves_metadata(provider):
    provider({'horizon': 'three years'})
    original = {'title': 'Move', 'skipped': ['weights'], 'memory': {'confirmed': True}}
    result = intake_model.extract('Over three years', original)
    assert result['document'] == {**original, 'horizon': 'three years'}
    assert 'horizon' not in original
    assert result['warnings']


@pytest.mark.parametrize('updates', [
    {'skipped': ['weights']}, {'override_acceptances': []}, {'memory': {}},
    {'options': [{'id': 'x', 'confirmed': True}]},
    {'options': [{'outcomes': [{'recommendation': 'take it'}]}]},
    {'weights': {'money': -1}}, {'correlations': [{'rho': 2}]},
])
def test_reject_unsafe_or_invalid_output(provider, updates):
    provider(updates)
    assert intake_model.extract('Some facts', {})['document'] is None


def test_bounded_response(provider, monkeypatch):
    monkeypatch.setattr(intake_model, 'MAX_RESPONSE_BYTES', 10)
    provider({'title': 'A title'})
    assert intake_model.extract('Some facts', {})['provenance']['reason'] == 'response_too_large'


@pytest.mark.parametrize('raw,status', [('not json', 200), ('secret error', 500)])
def test_provider_errors_are_sanitized(provider, raw, status):
    provider(raw=raw, status=status)
    result = intake_model.extract('Some facts', {})
    assert result['document'] is None
    assert 'secret' not in str(result)


def test_nullable_unknowns_do_not_erase_existing(provider):
    provider({'title': None, 'horizon': 'one year'})
    assert intake_model.extract('one year', {'title': 'Move'})['document']['title'] == 'Move'


def test_partial_dimension_updates_preserve_existing(provider):
    provider({'bankroll': {'money': 200}, 'weights': {'time': 3}})
    original = {'bankroll': {'money': 100, 'time': 40}, 'weights': {'money': 1}}
    result = intake_model.extract('200 money and time weight 3', original)['document']
    assert result['bankroll'] == {'money': 200, 'time': 40}
    assert result['weights'] == {'money': 1, 'time': 3}


def test_incremental_options_preserve_order_outcomes_and_metadata(provider):
    original = {'options': [
        {'id': 'a', 'label': 'A', 'note': 'keep', 'costs': {'time': 2},
         'outcomes': [{'label': 'base', 'values': {'money': 10}, 'note': 'also keep'},
                      {'label': 'upside', 'values': {'money': 30}}]},
        {'id': 'b', 'label': 'B'}]}
    provider({'options': [{'id': 'b', 'costs': {'money': 5}},
                          {'id': 'a', 'outcomes': [{'label': 'base', 'values': {'time': 4}}]},
                          {'id': 'c', 'label': 'C'}]})
    result = intake_model.extract('More facts', original)['document']
    assert [row['id'] for row in result['options']] == ['a', 'b', 'c']
    assert result['options'][0]['note'] == 'keep'
    assert result['options'][0]['outcomes'] == [
        {'label': 'base', 'values': {'money': 10, 'time': 4}, 'note': 'also keep'},
        {'label': 'upside', 'values': {'money': 30}}]
    assert 'time' not in original['options'][0]['outcomes'][0]['values']


def test_empty_lists_cannot_delete_existing_entries(provider):
    provider({'options': [], 'correlations': []})
    original = {'options': [{'id': 'a', 'label': 'A'}],
                'correlations': [{'node_id': 'risk', 'rho': .2}]}
    assert intake_model.extract('Some facts', original)['document'] == original


def test_ambiguous_outcome_identity_falls_back(provider):
    provider({'options': [{'id': 'a', 'outcomes': [{'values': {'money': 4}}]}]})
    original = {'options': [{'id': 'a', 'outcomes': [{'label': 'base'}]}]}
    assert intake_model.extract('Some facts', original)['document'] is None
