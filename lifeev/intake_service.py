"""Review-only intake orchestration; no storage writes or skeleton overrides."""
from copy import deepcopy

from .intake import propose
from .intake_model import extract
from .skeleton import gaps, validate


def changes(before, after, path=''):
    if isinstance(before, dict) and isinstance(after, dict):
        rows = []
        for key in dict.fromkeys([*before, *after]):
            rows.extend(changes(before.get(key), after.get(key), f'{path}.{key}' if path else key))
        return rows
    if before == after:
        return []
    return [{'field': path, 'before': deepcopy(before), 'after': deepcopy(after)}]


def preview(text, document=None, use_model=False):
    if not isinstance(use_model, bool):
        raise ValueError('use_model must be boolean')
    original = {} if document is None else document
    result = propose(text, original)
    result['provenance'] = {'source': 'offline', 'method': 'explicit-label parser'}
    if use_model:
        generated = extract(text, original)
        result['provenance'] = generated['provenance']
        if generated['document'] is not None:
            candidate = generated['document']
            candidate['intake_notes'] = result['document']['intake_notes']
            errors = validate(candidate)
            if errors:
                raise ValueError('; '.join(errors))
            result.update(document=candidate, method='AI extraction proposal', warnings=generated['warnings'])
        else:
            result['warnings'].append('AI extraction unavailable (' + generated['provenance']['reason'] + '); used the offline parser.')
    if original.get('skipped'):
        result['warnings'].append('Existing skips remain recorded. Review skipped fields in the uncertainty panel when adding new details.')
    result['changes'] = changes(original, result['document'])
    result['gaps'] = gaps(result['document'])
    return result
