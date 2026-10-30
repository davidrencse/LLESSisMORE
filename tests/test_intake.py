import unittest
from lifeev.intake import propose
from lifeev.skeleton import validate


class IntakeTests(unittest.TestCase):
    def test_narrative_and_preservation(self):
        text = 'I am deciding between stay and change. This matters to my family.'
        proposal = propose(text)
        self.assertEqual([o['label'] for o in proposal['document']['options']], ['stay', 'change'])
        self.assertEqual(proposal['document']['intake_notes'], [text])
        self.assertNotIn('bankroll', proposal['document'])

    def test_should_question_names_options(self):
        proposal = propose('girlfriend decision: should i just leave her or keep pursueing')
        self.assertEqual([o['label'] for o in proposal['document']['options']], ['just leave her', 'keep pursueing'])
        proposal = propose('Should I take the job, freelance, or should I stay?')
        self.assertEqual([o['label'] for o in proposal['document']['options']], ['take the job', 'freelance', 'stay'])
        self.assertNotIn('options', propose('Should I move to Lisbon?')['document'])

    def test_incremental_conflict_and_no_mutation(self):
        source = {'title': 'Old', 'options': [{'id': 'stay', 'label': 'Stay', 'costs': {'money': 3}}]}
        proposal = propose('Title: New\nOptions: Stay vs Go\nCost Stay: money=5\nBankroll: money=100', source)
        self.assertEqual(source['title'], 'Old')
        conflict = next(c for c in proposal['changes'] if c['field'] == 'options.0.costs.money')
        self.assertEqual((conflict['before'], conflict['after']), (3, 5))
        self.assertEqual(validate(proposal['document']), [])

    def test_explicit_outcomes(self):
        result = propose('Options: Stay or Go\nOutcome Stay / base: probability=70%, money=10\nOutcome Stay / downside: probability=30%, money=-2')
        self.assertEqual(result['document']['options'][0]['outcomes'][0]['probability'], .7)
        self.assertEqual(validate(result['document']), [])

    def test_invalid_values_retained_as_notes(self):
        text = 'Options: Stay\nBankroll: money=-10'
        result = propose(text)
        self.assertTrue(result['warnings'])
        self.assertNotIn('bankroll', result['document'])
        self.assertEqual(result['document']['intake_notes'], [text])

    def test_limits_and_invalid_input(self):
        for text in ('', 'a' * 20001, None):
            with self.assertRaises(ValueError):
                propose(text)
        with self.assertRaises(ValueError):
            propose('hello', {'options': None})


if __name__ == '__main__':
    unittest.main()
