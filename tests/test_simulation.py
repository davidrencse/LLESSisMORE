import copy
import unittest
from unittest.mock import patch
from threading import Event, Thread

from lifeev.simulation import MAX_SIMULATION_VALUES, SimulationBusy, simulate


def document():
    return {'weights': {'money': 1, 'time': 2}, 'options': [
        {'id': 'z', 'label': 'Z', 'costs': {'money': 2, 'time': 1}, 'outcomes': [
            {'probability': .5, 'values': {'money': 0, 'time': 2}},
            {'probability': .5, 'values': {'money': 10, 'time': 4}}]},
        {'id': 'a', 'label': 'A', 'costs': {'money': 1, 'time': 0}, 'outcomes': [
            {'probability': 1, 'values': {'money': 5, 'time': 0}}]}]}


class SimulationTests(unittest.TestCase):
    def test_seed_order_net_values_and_no_mutation(self):
        d = document()
        original = copy.deepcopy(d)
        result = simulate([d], 200, 9)
        self.assertEqual(result, simulate([d], 200, 9))
        self.assertEqual(d, original)
        self.assertEqual([o['id'] for o in result['options']], ['z', 'a'])
        first = result['options'][0]['dimensions']
        self.assertEqual([x['value'] for x in first['money']['distribution']], [-2, 8])
        self.assertEqual([x['value'] for x in first['weighted_utility']['distribution']], [0, 14])
        self.assertEqual(sum(x['count'] for x in first['money']['distribution']), 200)
        self.assertEqual(result['options'][1]['dimensions']['money']['mean'], 4)
        self.assertEqual(result['pairs'], [])

    def test_pairs_are_distinct_option_combinations(self):
        result = simulate([document(), document()], samples=100, linked=True)
        self.assertEqual([p['option_ids'] for p in result['pairs']], [['z', 'z'], ['z', 'a'], ['a', 'z'], ['a', 'a']])
        self.assertEqual(result['pairs'][-1]['dimensions']['money']['mean'], 8)
        self.assertTrue(any('independently' in a for a in result['assumptions']))

    def test_scope_and_bounds(self):
        for docs, kwargs in [([], {}), ([document()] * 3, {}), ([document()] * 2, {}),
                              ([dict(document(), branch_count=3)], {}),
                              ([dict(document(), children=[1, 2, 3])], {})]:
            with self.assertRaises(ValueError):
                simulate(docs, **kwargs)
        for samples in [99, 100001, True, 100.5]:
            with self.assertRaises(ValueError):
                simulate([document()], samples=samples)

    def test_total_budget_rejected_before_preparation_or_rng(self):
        d = document()
        d['options'] = [copy.deepcopy(d['options'][0]) for _ in range(30)]
        with patch('lifeev.simulation._prepare') as prepare, patch('lifeev.simulation.random.Random') as rng:
            with self.assertRaisesRegex(ValueError, 'reduce samples or options'):
                simulate([d, d], samples=100000, linked=True)
            prepare.assert_not_called()
            rng.assert_not_called()

    def test_budget_boundary_and_sample_count(self):
        d = document()
        # 10 option marginals * 3 dimensions * 100000 samples = exact budget.
        d['options'] = [copy.deepcopy(d['options'][1]) for _ in range(10)]
        with patch('lifeev.simulation._summary', return_value={}):
            for samples in [99999, 100000]:
                result = simulate([d], samples=samples)
                self.assertEqual(result['samples'], samples)
                self.assertEqual(result['resource_budget']['values'], 30 * samples)
                self.assertLessEqual(result['resource_budget']['values'], MAX_SIMULATION_VALUES)
        d['options'].append(copy.deepcopy(d['options'][0]))
        with self.assertRaisesRegex(ValueError, 'budget exceeded'):
            simulate([d], samples=100000)

    def test_shape_bounds_before_preparation(self):
        for d in [dict(document(), options=[{}] * 31),
                  dict(document(), options=[{'outcomes': [{}] * 101}])]:
            with patch('lifeev.simulation._prepare') as prepare:
                with self.assertRaises(ValueError):
                    simulate([d])
                prepare.assert_not_called()

    def test_output_budget_rejected_even_when_work_fits(self):
        d = document()
        for option in d['options']:
            option['outcomes'] = [{'probability': .01, 'values': {'money': i, 'time': i * 2}}
                                  for i in range(100)]
        # 2.4M summarized values fit the work budget, but the response could
        # retain 121,200 distribution dictionaries across both dimensions/utility.
        with patch('lifeev.simulation._prepare') as prepare:
            with self.assertRaisesRegex(ValueError, 'output budget exceeded'):
                simulate([d, d], samples=100000, linked=True)
            prepare.assert_not_called()

    def test_concurrency_gate_and_release_after_failure(self):
        entered, release = Event(), Event()
        errors = []

        def fail_prepare(document):
            entered.set()
            release.wait(5)
            raise ValueError('preparation failed')

        def run():
            try:
                simulate([document()], samples=100)
            except Exception as error:
                errors.append(error)

        with patch('lifeev.simulation._prepare', side_effect=fail_prepare):
            worker = Thread(target=run)
            worker.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(SimulationBusy):
                    simulate([document()], samples=100)
            finally:
                release.set()
                worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(str(errors[0]), 'preparation failed')
        self.assertEqual(simulate([document()], samples=100)['samples'], 100)
        with self.assertRaises(ValueError):
            simulate([dict(document(), options=[])], samples=100)
        self.assertEqual(simulate([document()], samples=100)['samples'], 100)

    def test_reject_unsupported_dependence_and_invalid_inputs(self):
        d = document()
        d['correlations'] = [{'rho': .2}]
        with self.assertRaisesRegex(ValueError, 'joint outcome model'):
            simulate([d])
        for value in [-.1, float('nan'), True, .2]:
            d = document()
            d['options'][0]['outcomes'][0]['probability'] = value
            with self.assertRaises(ValueError):
                simulate([d])
        d = document()
        del d['options'][0]['costs']['money']
        with self.assertRaises(ValueError):
            simulate([d])


if __name__ == '__main__':
    unittest.main()
