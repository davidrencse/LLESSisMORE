"""Two-player simultaneous games typed as text.

One line per pair of choices; the first payoff is yours, the second theirs::

    If I leave and she stays: -3, 2
    If I stay and she moves on: me -6, her +1
    70% she stays            (optional: how likely you think each of their choices is)

Describes the entered payoffs only: each side's best responses, stable outcomes
(pure Nash equilibria), strictly dominant choices, worst case per choice, outcomes
neither side can improve without costing the other, and the mixed equilibrium of a
2x2 game. It never says which choice to make.
"""
import re

from .textnum import NUMBER, value

EPS = 1e-9
MAX_CHOICES = 30
_POSSESSIVE = {'she': 'her', 'he': 'his', 'they': 'their', 'it': 'its'}
_OBJECT = {'she': 'her', 'he': 'him', 'they': 'them', 'it': 'it'}
_WHO = r"(?P<who>the\s+[\w'’-]+|[\w'’-]+)"
_PAY = r"(?:[A-Za-z][\w'’-]*\s+)?(?P<SLOT>" + NUMBER + r")"
_CELL = re.compile(
    r"^\s*(?:[-*•]\s*)?if\s+(?:i|we)\s+(?P<mine>.+?)\s+(?:and|but|while)\s+" + _WHO + r"\s+(?P<theirs>.+?)\s*[:=→,]\s*"
    + _PAY.replace('SLOT', 'p1') + r"(?:\s*(?:,|/|;|\band\b)\s*" + _PAY.replace('SLOT', 'p2') + r")?\s*\.?\s*$", re.I)
_BELIEF = re.compile(r"^\s*(?:[-*•]\s*)?(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?:chance\s+)?(?:that\s+)?" + _WHO + r"\s+(?P<theirs>.+?)\s*\.?\s*$", re.I)
ASSUMPTIONS = [
    'Both sides choose once, at the same time, and know these payoffs.',
    'Each side’s payoffs are on its own scale; yours are never added to theirs.',
    'Stable outcomes are where neither side gains by changing alone. They are not predictions or advice.',
]


def _clean(text):
    return re.sub(r'\s+', ' ', text).strip().rstrip('.')


def _key(text):
    # "stays" and "stay" name the same choice.
    return ' '.join(w.rstrip('s') for w in _clean(text).casefold().split())


def _index(labels, label, add=True):
    keys = [_key(x) for x in labels]
    if _key(label) in keys:
        return keys.index(_key(label))
    if not add:
        return None
    labels.append(label)
    return len(labels) - 1


def parse_game(text):
    """Return (game description or None, indices of the lines it used)."""
    lines, cells, rows, cols, consumed, notes, who = text.splitlines(), {}, [], [], set(), [], None
    for k, line in enumerate(lines):
        match = None if '%' in line else _CELL.match(line)
        if not match:
            continue
        consumed.add(k)
        who = who or match['who']
        i, j = _index(rows, _clean(match['mine'])), _index(cols, _clean(match['theirs']))
        if len(rows) > MAX_CHOICES or len(cols) > MAX_CHOICES:
            raise ValueError(f'Games support at most {MAX_CHOICES} choices per player')
        if (i, j) in cells:
            notes.append(f'Two lines give payoffs for “{rows[i]}” with “{cols[j]}”; the later one is used.')
        cells[i, j] = (value(match['p1']), value(match['p2']) if match['p2'] else None)
    if not cells:
        return None, set()
    beliefs = {}
    for k, line in enumerate(lines):
        match = _BELIEF.match(line)
        j = match and _index(cols, _clean(match['theirs']), add=False)
        if j is not None and match:
            consumed.add(k)
            beliefs[j] = float(match['pct']) / 100
    return describe(rows, cols, cells, beliefs, who, notes), consumed


def _best(values):
    top = max(values)
    return [k for k, v in enumerate(values) if v >= top - EPS]


def _dominant(matrix):
    """Index of the row that beats every other row in every column, if any."""
    return next((i for i, row in enumerate(matrix) if len(matrix) > 1 and all(
        all(row[j] > other[j] + EPS for j in range(len(row))) for k, other in enumerate(matrix) if k != i)), None)


def _mixed(A, B):
    """Interior mixed equilibrium of a 2x2 game: each side's mix leaves the other indifferent."""
    da = A[0][0] - A[0][1] - A[1][0] + A[1][1]
    db = B[0][0] - B[1][0] - B[0][1] + B[1][1]
    if abs(da) < EPS or abs(db) < EPS:
        return None
    q, p = (A[1][1] - A[0][1]) / da, (B[1][1] - B[1][0]) / db
    if not (EPS < p < 1 - EPS and EPS < q < 1 - EPS):
        return None
    return {'me': [p, 1 - p], 'them': [q, 1 - q], 'payoffs': [A[0][0] * q + A[0][1] * (1 - q), B[0][0] * p + B[1][0] * (1 - p)]}


def describe(rows, cols, cells, beliefs=None, who=None, notes=None):
    beliefs, notes = beliefs or {}, list(notes or [])
    who = who or 'they'
    pronoun = who.casefold() in _POSSESSIVE
    player = who.capitalize() if pronoun else who[:1].upper() + who[1:]
    subject, possessive, obj = (who.casefold(), _POSSESSIVE[who.casefold()], _OBJECT[who.casefold()]) if pronoun else (player, player + '’s', player)
    n, m = len(rows), len(cols)
    payoffs = [[list(cells[i, j]) if (i, j) in cells else None for j in range(m)] for i in range(n)]
    missing = [f'Missing a line for: if I {rows[i]} and {subject} {cols[j]}.'
               for i in range(n) for j in range(m) if (i, j) not in cells]
    A = None if missing else [[payoffs[i][j][0] for j in range(m)] for i in range(n)]
    B = None if missing or any(payoffs[i][j][1] is None for i in range(n) for j in range(m)) else [[payoffs[i][j][1] for j in range(m)] for i in range(n)]
    if A and not B:
        missing.append(f'Add {possessive} payoff after yours on each line, for example “… : -3, 2”.')
    result = {'player': player, 'subject': subject, 'possessive': possessive, 'object': obj, 'rows': rows, 'cols': cols, 'payoffs': payoffs, 'complete': not missing, 'missing': missing, 'notes': notes,
              'best_responses': {'me': None, 'them': None}, 'dominant': {'me': None, 'them': None}, 'worst_case': {'me': None, 'them': None},
              'nash': None, 'pareto': None, 'mixed': None, 'constant_sum': None, 'beliefs': None, 'expected': None, 'assumptions': ASSUMPTIONS}
    if A:
        result['best_responses']['me'] = [_best([A[i][j] for i in range(n)]) for j in range(m)]
        result['worst_case']['me'] = [min(row) for row in A]
        result['dominant']['me'] = _dominant(A)
        if beliefs:
            total = sum(beliefs.values())
            if len(beliefs) == m and abs(total - 1) < 1e-6:
                result['beliefs'] = [beliefs[j] for j in range(m)]
                result['expected'] = [sum(beliefs[j] * A[i][j] for j in range(m)) for i in range(n)]
            else:
                notes.append(f'Your chances for {possessive} choices cover {len(beliefs)} of {m} and total {total * 100:g}%. Give one for each choice, totalling 100%.')
    if B:
        result['best_responses']['them'] = [_best(B[i]) for i in range(n)]
        transposed = [[B[i][j] for i in range(n)] for j in range(m)]
        result['worst_case']['them'] = [min(col) for col in transposed]
        result['dominant']['them'] = _dominant(transposed)
    if A and B:
        mine, theirs = result['best_responses']['me'], result['best_responses']['them']
        result['nash'] = [[i, j] for i in range(n) for j in range(m) if i in mine[j] and j in theirs[i]]
        result['pareto'] = [[i, j] for i in range(n) for j in range(m) if not any(
            A[k][l] >= A[i][j] - EPS and B[k][l] >= B[i][j] - EPS and (A[k][l] > A[i][j] + EPS or B[k][l] > B[i][j] + EPS)
            for k in range(n) for l in range(m))]
        sums = {round(A[i][j] + B[i][j], 9) for i in range(n) for j in range(m)}
        result['constant_sum'] = len(sums) == 1
        result['mixed'] = _mixed(A, B) if n == m == 2 else None
    return result
