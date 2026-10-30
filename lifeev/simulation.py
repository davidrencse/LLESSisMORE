"""Seeded discrete Monte Carlo summaries, with no option ranking.

Each pair is a separate hypothetical combination of options, never a mixture of
mutually exclusive options. Pair node outcomes are sampled independently.
"""
import bisect
import math
import random
from collections import Counter
from threading import BoundedSemaphore

from .skeleton import MAX_OPTIONS, MAX_OUTCOMES


MAX_SIMULATION_VALUES = 3_000_000
MAX_SIMULATION_RESULT_POINTS = 100_000
_SIMULATION_GATE = BoundedSemaphore(1)


class SimulationBusy(RuntimeError):
    """Another simulation is active in this process; callers may retry later."""


def _preflight(documents, samples):
    """Bound preparation and all marginal/pair summary values before allocation."""
    counts, outcome_counts = [], []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("Simulation documents must be objects")
        options = document.get("options")
        if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
            raise ValueError(f"Simulation requires 1 to {MAX_OPTIONS} options; reduce options")
        for option in options:
            if not isinstance(option, dict):
                raise ValueError("Simulation options must be objects")
            outcomes = option.get("outcomes")
            if not isinstance(outcomes, list) or not 1 <= len(outcomes) <= MAX_OUTCOMES:
                raise ValueError(f"Simulation requires 1 to {MAX_OUTCOMES} outcomes per option")
            if any(not isinstance(outcome, dict) for outcome in outcomes):
                raise ValueError("Simulation outcomes must be objects")
        counts.append(len(options))
        outcome_counts.append([len(option['outcomes']) for option in options])
    pairs = counts[0] * counts[1] if len(counts) == 2 else 0
    values = (sum(counts) + pairs) * 3 * samples
    if values > MAX_SIMULATION_VALUES:
        raise ValueError(f"Simulation work budget exceeded ({values} values; maximum "
                         f"{MAX_SIMULATION_VALUES}); reduce samples or options")
    # Each distinct marginal or pair value becomes a dictionary in the response.
    # Bound that retained output separately from work on temporary sample arrays.
    points = sum(min(samples, outcomes) for counts_for_node in outcome_counts for outcomes in counts_for_node)
    if len(outcome_counts) == 2:
        points += sum(min(samples, left * right) for left in outcome_counts[0] for right in outcome_counts[1])
    points *= 3
    if points > MAX_SIMULATION_RESULT_POINTS:
        raise ValueError(f'Simulation output budget exceeded ({points} possible distribution points; '
                         f'maximum {MAX_SIMULATION_RESULT_POINTS}); reduce samples, options, or outcomes')
    return {"values": values, "maximum_values": MAX_SIMULATION_VALUES,
            "maximum_result_points": MAX_SIMULATION_RESULT_POINTS, "possible_result_points": points}


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    return float(value)


def _summary(values):
    mean = math.fsum(values) / len(values)
    counts = Counter(values)
    ordered = sorted(counts)
    cumulative, total = [], 0
    for value in ordered:
        total += counts[value]
        cumulative.append(total)
    def quantile(q):
        return ordered[bisect.bisect_left(cumulative, math.ceil(q * len(values)))]
    return {'mean': mean, 'standard_deviation': math.sqrt(math.fsum((v - mean) ** 2 for v in values) / len(values)),
            'p05': quantile(.05),
            'median': quantile(.5),
            'p95': quantile(.95),
            'loss_probability': sum(v < 0 for v in values) / len(values),
            'distribution': [{'value': v, 'count': counts[v], 'probability': counts[v] / len(values)} for v in ordered]}


def _prepare(document):
    count = document.get('branch_count', document.get('branching_count', 0))
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError('branch_count must be a nonnegative integer')
    if count >= 3 or any(isinstance(document.get(k), list) and len(document[k]) >= 3 for k in ('children', 'branches')):
        raise ValueError('Monte Carlo disabled at three or more branches')
    for correlation in document.get('correlations', []):
        if _number(correlation.get('rho'), 'correlation rho') != 0:
            raise ValueError('Nonzero correlation requires a joint outcome model; independent sampling is unsupported for this input')
    weights = {dim: _number(document.get('weights', {}).get(dim, default), f'{dim} weight') for dim, default in [('money', 1), ('time', 0)]}
    options = document.get('options')
    if not isinstance(options, list) or not options:
        raise ValueError('Simulation requires at least one option')
    prepared = []
    for option in options:
        outcomes = option.get('outcomes')
        if not isinstance(outcomes, list) or not outcomes:
            raise ValueError('Simulation requires discrete outcomes')
        costs = {dim: _number(option.get('costs', {}).get(dim), f'{dim} cost') for dim in weights}
        probabilities, values = [], []
        for outcome in outcomes:
            p = _number(outcome.get('probability'), 'probability')
            if not 0 <= p <= 1:
                raise ValueError('probability must be in [0, 1]')
            probabilities.append(p)
            net = {dim: _number(outcome.get('values', {}).get(dim), f'{dim} value') - costs[dim] for dim in weights}
            net['weighted_utility'] = math.fsum(net[dim] * weights[dim] for dim in weights)
            if not all(math.isfinite(v) for v in net.values()):
                raise ValueError('Net values and utility must be finite')
            values.append(net)
        if not math.isclose(math.fsum(probabilities), 1, rel_tol=0, abs_tol=1e-9):
            raise ValueError('Outcome probabilities must sum to one')
        cumulative, total = [], 0.
        for p in probabilities:
            total += p
            cumulative.append(total)
        cumulative[-1] = 1.
        prepared.append((option, cumulative, values))
    return prepared


def simulate(documents, samples=10000, seed=42, linked=False):
    """Summarize option marginals and each independent pair in input order.

    The caller verifies graph linkage and global branching from storage; linked
    explicitly attests a direct parent-child pair when passing plain documents.
    """
    if not isinstance(documents, list) or not 1 <= len(documents) <= 2:
        raise ValueError('Select one node or two directly linked nodes only')
    if len(documents) == 2 and linked is not True:
        raise ValueError('Two nodes must be directly linked')
    if isinstance(samples, bool) or not isinstance(samples, int) or not 100 <= samples <= 100000:
        raise ValueError('samples must be an integer from 100 to 100000')
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('seed must be an integer')
    if not _SIMULATION_GATE.acquire(blocking=False):
        raise SimulationBusy('Another simulation is running; retry later')
    try:
        budget = _preflight(documents, samples)
        return _simulate(documents, samples, seed, budget)
    finally:
        _SIMULATION_GATE.release()


def _simulate(documents, samples, seed, budget):
    prepared = [_prepare(document) for document in documents]
    rng = random.Random(seed)
    dimensions = ('money', 'time', 'weighted_utility')
    draws, results = [], []
    for node_index, options in enumerate(prepared):
        node_draws = []
        for option, cumulative, values in options:
            sampled = [values[bisect.bisect_right(cumulative, rng.random())] for _ in range(samples)]
            node_draws.append(sampled)
            results.append({'node_index': node_index, 'id': option.get('id'), 'label': option.get('label'),
                            'dimensions': {dim: _summary([v[dim] for v in sampled]) for dim in dimensions}})
        draws.append(node_draws)
    pairs = []
    if len(prepared) == 2:
        for i, (left, _, _) in enumerate(prepared[0]):
            for j, (right, _, _) in enumerate(prepared[1]):
                pairs.append({'option_ids': [left.get('id'), right.get('id')], 'node_indices': [0, 1],
                              'dimensions': {dim: _summary([a[dim] + b[dim] for a, b in zip(draws[0][i], draws[1][j])]) for dim in dimensions}})
    return {'method': 'seeded_discrete_monte_carlo', 'samples': samples, 'seed': seed,
            'options': results, 'pairs': pairs, 'resource_budget': budget,
            'assumptions': ['Stated probabilities define mutually exclusive exhaustive outcomes; probability uncertainty is not sampled.',
                            'Outcome values are net of option costs in each dimension.',
                            'Each option and option pair is a separate scenario; options are not ranked or mixed.',
                            'Linked nodes are sampled independently; nonzero correlations require a joint outcome model and are rejected.',
                            'Pair totals assume additive values in matching units and weighted utilities on comparable scales.']}
