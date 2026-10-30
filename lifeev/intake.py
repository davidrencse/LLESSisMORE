"""Conservative, offline intake. Proposals are drafts and never persisted here.

Explicit numeric syntax: ``Bankroll: money=100, time=20``;
``Cost Stay: money=10``; ``Outcome Stay / base: probability=1, money=20``.
Unknown prose stays verbatim in intake_notes; numbers are never inferred.
"""
from copy import deepcopy
import math
import re

from .skeleton import validate

MAX_TEXT = 20000
MAX_NOTES = 100


def propose(text, document=None):
    """Return document, reviewable changes, warnings and extraction method.

    Existing options match by case-insensitive label or ID. New options append;
    listing options never deletes existing details. Conflicting fields expose
    their old and proposed values in changes. The caller owns acceptance.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError('text must be nonempty text up to 20000 characters')
    source = {} if document is None else document
    errors = validate(source)
    if errors:
        raise ValueError('; '.join(errors))
    result = deepcopy(source)
    notes = result.get('intake_notes', [])
    if not isinstance(notes, list) or any(not isinstance(n, str) for n in notes):
        raise ValueError('intake_notes must be a list of text')
    if len(notes) >= MAX_NOTES or sum(map(len, notes)) + len(text) > 100000:
        raise ValueError('intake notes limit reached; existing text was preserved')
    changes, warnings = [], []

    def change(obj, key, value, path, evidence):
        if key not in obj or obj[key] != value:
            changes.append(dict(field=path, before=deepcopy(obj.get(key)),
                                after=deepcopy(value), evidence=evidence))
            obj[key] = value

    def option(name, create=False):
        options = result.get('options', [])
        for i, row in enumerate(options):
            if name.casefold() in (str(row.get('id', '')).casefold(), str(row.get('label', '')).casefold()):
                return i, row
        if create:
            if len(options) >= 30:
                warnings.append('Option limit reached; additional names remain in notes.')
                return None, None
            result.setdefault('options', options)
            stem = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'option'
            identifier, suffix = stem, 2
            ids = {row.get('id') for row in options}
            while identifier in ids:
                identifier, suffix = f'{stem}-{suffix}', suffix + 1
            row = {'id': identifier, 'label': name}
            options.append(row)
            changes.append(dict(field=f'options.{len(options)-1}', before=None,
                                after=deepcopy(row), evidence=name))
            return len(options)-1, row
        warnings.append(f'Unknown option {name!r}; detail remains in notes.')
        return None, None

    def numbers(raw):
        values = {}
        for part in re.split(r'[,;]', raw):
            match = re.fullmatch(r'\s*([\w -]{1,80})\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(%)?\s*', part)
            if not match:
                warnings.append(f'Unparsed numeric detail retained in notes: {part.strip()}')
                continue
            key, value = match[1].strip(), float(match[2])
            if match[3]:
                if not key.startswith('probability'):
                    warnings.append('Percent only supported for probabilities; detail retained in notes.')
                    continue
                value /= 100
            if not math.isfinite(value):
                warnings.append('Nonfinite number ignored; original retained in notes.')
                continue
            values[key] = value
        return values

    lines = text.splitlines()
    # Discover options before applying details, regardless of line order.
    for line in lines:
        match = re.match(r'^\s*options\s*:\s*(.+)$', line, re.I)
        narrative = re.search(r'\b(?:I am |I\x27m )?deciding between\s+(.+?)(?:[.!?]|$)', line, re.I)
        # "Should I A or B?" names options only when it contains an explicit "or".
        question = None if match or narrative else re.search(r'\bshould\s+(?:I|we)\s+(.+?\s+or\s+.+?)(?:[?.!]|$)', line, re.I)
        raw = match[1] if match else narrative[1] if narrative else question[1] if question else None
        if raw:
            names = re.split(r'\s+(?:vs\.?|versus|or)\s+(?:should\s+(?:I|we)\s+)?|\s*;\s*' + (r'|\s+and\s+' if narrative and not match else '') + (r'|\s*,\s*(?:or\s+)?(?:should\s+(?:I|we)\s+)?' if question else ''), raw, flags=re.I)
            for name in names:
                if name.strip():
                    option(name.strip(), True)
    for line in lines:
        match = re.match(r'^\s*(title|horizon|reversibility)\s*:\s*(.+)$', line, re.I)
        if match:
            change(result, match[1].lower(), match[2].strip(), match[1].lower(), line)
            continue
        match = re.match(r'^\s*(bankroll|weights|thresholds)\s*:\s*(.+)$', line, re.I)
        if match:
            field = match[1].lower()
            target = result.setdefault(field, {})
            for key, value in numbers(match[2]).items():
                change(target, key, value, f'{field}.{key}', line)
            continue
        match = re.match(r'^\s*(?:cost|costs)\s+(.+?)\s*:\s*(.+)$', line, re.I)
        if match:
            i, row = option(match[1].strip())
            if row is not None:
                for key, value in numbers(match[2]).items():
                    change(row.setdefault('costs', {}), key, value, f'options.{i}.costs.{key}', line)
            continue
        match = re.match(r'^\s*outcome\s+(.+?)\s*/\s*([^:]+):\s*(.+)$', line, re.I)
        if match:
            i, row = option(match[1].strip())
            if row is not None:
                outcomes = row.setdefault('outcomes', [])
                label = match[2].strip()
                j = next((j for j, out in enumerate(outcomes) if out.get('label', '').casefold() == label.casefold()), len(outcomes))
                if j == len(outcomes):
                    outcomes.append({'label': label})
                    changes.append(dict(field=f'options.{i}.outcomes.{j}', before=None, after={'label': label}, evidence=line))
                out = outcomes[j]
                for key, value in numbers(match[3]).items():
                    if key in ('probability', 'probability_low', 'probability_high'):
                        change(out, key, value, f'options.{i}.outcomes.{j}.{key}', line)
                    else:
                        change(out.setdefault('values', {}), key, value, f'options.{i}.outcomes.{j}.values.{key}', line)
    errors = validate(result)
    if errors:
        # Invalid claims remain available verbatim for clarification, never silently
        # normalized or replaced by invented probabilities or values.
        warnings.extend(['Structured extraction was not applied: ' + e for e in errors])
        result, changes = deepcopy(source), []
    notes = deepcopy(notes) + [text]
    change(result, 'intake_notes', notes, 'intake_notes', text)
    if not any(c['field'] != 'intake_notes' for c in changes):
        warnings.append('Context retained. Add explicit options or labeled details to structure the draft.')
    errors = validate(result)
    if errors:
        raise ValueError('; '.join(errors))
    return dict(document=result, changes=changes, warnings=warnings, method='deterministic')
