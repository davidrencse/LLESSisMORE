"""Plain-text quick analysis.

Turns one typed message into a decision document and, when every option has
chances and outcomes, its exact discrete statistics. Deterministic and offline:
it never calls a model, starts a question session, touches retrieval, routes a
compute tier, caches or writes storage. Missing numbers are reported, never
invented.

Recognized scenario syntax, one option per line (or a label line followed by
scenario lines)::

    Should I leave or keep pursuing?
    Leave: 60% I feel better (+5), 40% I regret it (-3)
    If I keep pursuing, 30% it works out (+8), 70% it doesn't (-4)
"""
import re
import uuid

from .game import parse_game
from .intake import propose
from .skeleton import assess, validate
from .stats import analyze
from .textnum import AMOUNT as _AMOUNT, NUMBER as _NUMBER, value as _value

MAX_TEXT = 20000
METHOD = 'offline text parser + exact discrete statistics'
_RESERVED = {'title', 'options', 'horizon', 'reversibility', 'bankroll', 'weights', 'thresholds', 'scale', 'measure', 'cost', 'costs', 'outcome'}
_STOP = {'i', 'we', 'just', 'the', 'a', 'an', 'to', 'my', 'it', 'her', 'him', 'them', 'me', 'should', 'or', 'and', 'if', 'with', 'on', 'in', 'of', 'for'}
# Score forms, in order: "(+5)", "= 5" / "worth 5", "+5", or a bare number ending the clause.
_SCENARIO = re.compile(
    r'(?P<chance>\d+(?:\.\d+)?)\s*%\s*(?:chance\s+)?(?:(?:that|of)\s+)?(?P<label>[^,;()\n%]*?)\s*'
    r'(?:\(\s*(?P<v1>' + _NUMBER + r')\s*\)|(?:=|:|→|->|\bworth\b|\bat\b)\s*(?P<v2>' + _NUMBER + r')'
    r'|(?P<v3>[+\-−]\s*' + _AMOUNT + r')|(?P<v4>' + _AMOUNT + r')(?=\s*(?:[,;.]|$)))',
    re.I)
_LABEL_LINE = re.compile(r'^\s*(?:[-*•]\s*)?(?:if\s+(?:i|we)\s+(?P<if>[^,:]+?)\s*[,:]|(?P<name>[^:%\n]{1,80}?)\s*:)\s*(?P<rest>.*)$', re.I)


def _words(text):
    return [w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in _STOP]


def _similar(a, b):
    return a == b or (len(a) >= 4 and len(b) >= 4 and a[:4] == b[:4])


def _match(name, options):
    exact = [i for i, o in enumerate(options) if o['label'].strip().casefold() == name.strip().casefold()]
    if exact:
        return exact[0]
    words = _words(name)
    scores = [sum(any(_similar(w, x) for x in _words(o['label'])) for w in words) for o in options]
    best = max(scores, default=0)
    return scores.index(best) if best else None


def _unread(text, found):
    """Clauses that mention a chance but were not read as a scenario."""
    spans = [m.span() for m in found]
    return [c[0].strip() for c in re.finditer(r'[^,;]+', text)
            if re.search(r'\d\s*%', c[0]) and not any(a < c.end() and c.start() < b for a, b in spans)]


def _label(text):
    text = text.strip().rstrip('.?!')
    return text[:1].upper() + text[1:]


def _result(document, missing, notes, ready=None):
    """Statistics for the whole document when complete, else for the options that are ready."""
    errors = validate(document)
    missing = missing or [f'Check: {e}' for e in errors]
    complete = not missing
    analysis, partial = None, False
    if complete:
        analysis = {**analyze(document), 'assessment': assess(document)}
    elif ready:
        subset = {**document, 'options': ready}
        if not validate(subset):
            analysis, partial = {**analyze(subset), 'assessment': assess(subset)}, True
    return {'document': document, 'complete': complete, 'partial': partial, 'missing': missing, 'notes': notes, 'analysis': analysis, 'method': METHOD}


def quick_document(document):
    """Run the statistics on an already structured document, with no other services."""
    missing = [f'“{o.get("label") or "Unnamed option"}”: add chances and outcomes.' for o in document.get('options', []) if not o.get('outcomes')]
    if not document.get('options'):
        missing.append('Add at least one option.')
    return _result(document, missing, [])


def quick_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError('text must be nonempty text up to 20000 characters')
    game, used = parse_game(text)
    rest = '\n'.join(line for k, line in enumerate(text.splitlines()) if k not in used)
    if game is None:
        return _decision(text)
    title = text.strip().splitlines()[0][:200]
    decision = _decision(rest) if '%' in rest else None
    if decision is None:
        # A game alone: the question line titles it; its choices are the game's rows and columns.
        document = {'title': title, 'game': {k: game[k] for k in ('player', 'rows', 'cols', 'payoffs', 'beliefs')}}
        return {'document': document, 'complete': game['complete'], 'partial': False, 'missing': game['missing'],
                'notes': game['notes'], 'analysis': None, 'game': game, 'method': METHOD}
    decision['document']['game'] = {k: game[k] for k in ('player', 'rows', 'cols', 'payoffs', 'beliefs')}
    decision['missing'] += game['missing']
    decision['notes'] += game['notes']
    decision['complete'] = decision['complete'] and game['complete']
    decision['game'] = game
    return decision


def _decision(text):
    base = propose(text)['document']
    options = base.get('options', [])
    for option in options:
        option['label'] = _label(option.get('label', ''))
    scale = next((m[1].strip() for m in re.finditer(r'^\s*(?:scale|measure)\s*:\s*(\S[^\n]{0,39})$', text, re.I | re.M)), None)
    notes, scenarios, current, pending = [], {}, None, None
    for line in text.splitlines():
        label_line, rest = _LABEL_LINE.match(line), line
        if label_line:
            name = (label_line['if'] or label_line['name'] or '').strip()
            if name.lower() in _RESERVED or (label_line['name'] and name.lower().startswith(('cost ', 'outcome '))):
                continue
            # "Leave: 60% ..." or a bare "Leave:" heading names an option; "Topic: prose" does not.
            if _SCENARIO.search(label_line['rest']) or not label_line['rest'].strip():
                pending, current, rest = name, None, label_line['rest']
        found = list(_SCENARIO.finditer(rest))
        notes += [f'Couldn’t read “{c[:120]}”. Write the score as a number, like (+5).' for c in _unread(rest, found)]
        if not found:
            continue
        if pending:
            current = _match(pending, options)
            if current is None:
                options.append({'id': str(uuid.uuid4()), 'label': _label(pending)})
                current = len(options) - 1
            pending = None
        if current is None:
            notes.append(f'Could not tell which option this line is about: “{line.strip()[:120]}”. Start it with the option name and a colon.')
            continue
        for match in found:
            raw = match['v1'] or match['v2'] or match['v3'] or match['v4']
            scenarios.setdefault(current, []).append({
                'label': _label(match['label']) or f'Scenario {len(scenarios.get(current, [])) + 1}',
                'probability': float(match['chance']) / 100, 'raw': raw, 'value': _value(raw)})
    measure = scale or ('money' if any('$' in s['raw'] for rows in scenarios.values() for s in rows) else 'score')
    if '.' in measure or measure in ('__proto__', 'constructor', 'prototype'):
        measure = 'score'
    missing, ready = [], []
    for i, option in enumerate(options):
        option.setdefault('id', str(uuid.uuid4()))
        option.setdefault('status_quo', False)
        if i in scenarios:
            option['outcomes'] = [{'label': s['label'], 'probability': s['probability'], 'values': {measure: s['value']}} for s in scenarios[i]]
            option['costs'] = {measure: 0}
            total = sum(s['probability'] for s in scenarios[i]) * 100
            if abs(total - 100) > 1e-6:
                missing.append(f'“{option["label"]}”: the chances add up to {total:g}%. They need to total 100%.')
            else:
                ready.append(option)
        elif not option.get('outcomes'):
            missing.append(f'“{option["label"]}” needs chances and outcomes.')
    if not options:
        missing.append('Name the choices, for example “Should I take the job or stay?”.')
    document = {**base, 'options': options}
    if scenarios:
        document.update(dimensions=[measure], weights={measure: 1})
    if not document.get('title'):
        document['title'] = text.strip().splitlines()[0][:200]
    return _result(document, missing, notes, ready)
