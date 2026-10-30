"""Local synthetic allocation audit: python scripts/audit_memory.py.

No database or external provider is used. tracemalloc tracks Python allocations,
not native/GPU memory; timing measurements are separate from tracing overhead.
"""
import gc
import json
import math
from pathlib import Path
import random
import statistics
import sys
import timeit
import tracemalloc
from collections import Counter
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lifeev.simulation import _summary
from lifeev.quick import quick_text
from lifeev.controls import VersionCache, Retriever


def previous_summary(values):
    ordered = sorted(values)
    mean = math.fsum(values) / len(values)
    counts = Counter(values)
    return {'mean': mean, 'standard_deviation': math.sqrt(math.fsum((v - mean) ** 2 for v in values) / len(values)),
            'p05': ordered[math.ceil(.05 * len(values)) - 1],
            'median': ordered[math.ceil(.5 * len(values)) - 1],
            'p95': ordered[math.ceil(.95 * len(values)) - 1],
            'loss_probability': sum(v < 0 for v in values) / len(values),
            'distribution': [{'value': v, 'count': counts[v], 'probability': counts[v] / len(values)} for v in sorted(counts)]}


def peak(call):
    gc.collect()
    tracemalloc.start()
    call()
    _, maximum = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return maximum


def main():
    rng = random.Random(42)
    values = [float(rng.randrange(-50, 50)) for _ in range(100000)]
    assert previous_summary(values) == _summary(values)
    timings = {name: statistics.median(timeit.repeat(lambda: fn(values), repeat=5, number=3)) / 3
               for name, fn in [('before', previous_summary), ('after', _summary)]}
    peaks = {name: peak(lambda: fn(values)) for name, fn in [('before', previous_summary), ('after', _summary)]}
    cache, retriever = VersionCache(max_entries=8, max_bytes=64000), Retriever()
    text = 'Stay: 50% better (+3), 50% worse (-2)'

    def batch():
        for i in range(100):
            quick_text(text)
            cache.put({'title': str(i)}, {}, 'small', {'data': 'x' * 1000})
            retriever.sync('one', i, lambda: 'current decision ' + str(i))
            retriever.search('decision')

    batch()
    tracemalloc.start()
    retained = []
    for _ in range(4):
        batch()
        gc.collect()
        retained.append(tracemalloc.get_traced_memory()[0])
    tracemalloc.stop()
    corpus = Retriever()
    for i in range(2000):
        corpus.upsert(str(i), 'decision topic ' + str(i))
    expected = corpus.search('decision', pins=['1999'])
    search_peak = {'after': peak(lambda: corpus.search('decision', pins=['1999']))}
    with patch('lifeev.controls.heapq.nsmallest', side_effect=lambda n, items, key: sorted(items, key=key)[:n]):
        assert corpus.search('decision', pins=['1999']) == expected
        search_peak['before'] = peak(lambda: corpus.search('decision', pins=['1999']))
    print(json.dumps({'summary_seconds': timings, 'summary_peak_bytes': peaks,
                     'retrieval_2000_records_peak_bytes': search_peak,
                     'retained_bytes_after_100_request_batches': retained,
                     'cache_entries': len(cache._values), 'retrieval_records': len(retriever._records)}, indent=2))


if __name__ == '__main__':
    main()
