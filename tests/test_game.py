import unittest

from lifeev.game import describe, parse_game
from lifeev.quick import quick_text

RELATIONSHIP = ('girlfriend decision: should i leave her or stay?\n'
                'If I leave and she stays: -3, 2\nIf I leave and she moves on: 4, 3\n'
                'If I stay and she stays: 5, 5\nIf I stay and she moves on: me -6, her +1')


class GameTests(unittest.TestCase):
    def test_coordination_game_from_text(self):
        result = quick_text(RELATIONSHIP)
        game = result['game']
        self.assertTrue(result['complete'])
        self.assertIsNone(result['analysis'])
        self.assertEqual((game['player'], game['rows'], game['cols']), ('She', ['leave', 'stay'], ['stays', 'moves on']))
        self.assertEqual(game['best_responses'], {'me': [[1], [0]], 'them': [[1], [0]]})
        self.assertEqual(game['nash'], [[0, 1], [1, 0]])
        self.assertEqual(game['pareto'], [[1, 0]])
        self.assertEqual(game['worst_case'], {'me': [-3, -6], 'them': [2, 1]})
        self.assertEqual(game['dominant'], {'me': None, 'them': None})
        self.assertAlmostEqual(game['mixed']['me'][0], .8)
        self.assertAlmostEqual(game['mixed']['them'][0], 5 / 9)
        self.assertAlmostEqual(game['mixed']['payoffs'][0], 1 / 9)
        self.assertEqual(result['document']['game']['payoffs'][1][1], [-6, 1])

    def test_prisoners_dilemma_dominance(self):
        game = quick_text('If I confess and he confesses: -5, -5\nIf I confess and he stays silent: 0, -10\n'
                          'If I stay silent and he confesses: -10, 0\nIf I stay silent and he stays silent: -1, -1')['game']
        self.assertEqual(game['nash'], [[0, 0]])
        self.assertEqual(game['dominant'], {'me': 0, 'them': 0})
        self.assertNotIn([0, 0], game['pareto'])
        self.assertIsNone(game['mixed'])

    def test_matching_pennies_has_only_a_mixed_balance_and_is_constant_sum(self):
        game = quick_text('If I pick heads and they pick heads: 1, -1\nIf I pick heads and they pick tails: -1, 1\n'
                          'If I pick tails and they pick heads: -1, 1\nIf I pick tails and they pick tails: 1, -1')['game']
        self.assertEqual(game['nash'], [])
        self.assertTrue(game['constant_sum'])
        self.assertEqual(game['mixed']['me'], [.5, .5])
        self.assertEqual(game['mixed']['payoffs'], [0, 0])

    def test_beliefs_give_expected_payoffs(self):
        game = quick_text(RELATIONSHIP + '\n70% she stays\n30% she moves on')['game']
        self.assertEqual(game['beliefs'], [.7, .3])
        self.assertAlmostEqual(game['expected'][0], -.9)
        self.assertAlmostEqual(game['expected'][1], 1.7)
        partial = quick_text(RELATIONSHIP + '\n70% she stays')
        self.assertIsNone(partial['game']['expected'])
        self.assertIn('cover 1 of 2', partial['notes'][0])

    def test_missing_cells_and_payoffs_are_reported_not_filled(self):
        result = quick_text('If I go and she stays: 3\nIf I stay and she stays: 1\nIf I go and she leaves: 2')
        game = result['game']
        self.assertFalse(result['complete'])
        self.assertEqual(game['missing'][0], 'Missing a line for: if I stay and she leaves.')
        self.assertIsNone(game['nash'])
        self.assertIsNone(game['payoffs'][1][1])
        one_sided = quick_text('If I go and Sam stays: 3\nIf I stay and Sam stays: 1')
        self.assertEqual(one_sided['game']['best_responses']['me'], [[0]])
        self.assertIn('Sam’s payoff', one_sided['missing'][0])

    def test_plural_and_repeated_lines(self):
        game, used = parse_game('If I go and she stay: 1, 1\nIf I go and she stays: 2, 2\nunrelated text')
        self.assertEqual(game['cols'], ['stay'])
        self.assertEqual(game['payoffs'][0][0], [2, 2])
        self.assertIn('later one', game['notes'][0])
        self.assertEqual(used, {0, 1})

    def test_games_and_chance_scenarios_can_share_one_message(self):
        result = quick_text(RELATIONSHIP + '\nShould I move or stay?\nMove: 50% better (+2), 50% worse (-1)')
        self.assertIsNotNone(result['game'])
        self.assertEqual([o['label'] for o in result['analysis']['options']], ['Move'])

    def test_text_without_game_lines_is_unchanged(self):
        self.assertIsNone(parse_game('Should I stay or go?\nStay: 100% same (0)')[0])
        self.assertNotIn('game', quick_text('Should I stay or go?\nStay: 100% same (0)\nGo: 50% up (+2), 50% down (-1)'))

    def test_describe_ties_are_all_best_responses(self):
        game = describe(['a', 'b'], ['x'], {(0, 0): (1, 0), (1, 0): (1, 0)})
        self.assertEqual(game['best_responses']['me'], [[0, 1]])
        self.assertEqual(game['nash'], [[0, 0], [1, 0]])
