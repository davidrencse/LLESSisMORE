import unittest
from unittest import mock

from fastapi.testclient import TestClient

from lifeev.api import create_app
from lifeev.quick import quick_text
from lifeev.storage import Store
from cryptography.fernet import Fernet


class QuickTextTests(unittest.TestCase):
    def test_one_message_produces_statistics(self):
        result = quick_text('girlfriend decision: should i just leave her or keep pursueing\n'
                            'Leave: 60% I feel better (+5), 40% I regret it (-3)\n'
                            "If I keep pursuing, 30% it works out (+8), 70% it doesn't (-4)")
        self.assertTrue(result['complete'])
        self.assertEqual([o['label'] for o in result['document']['options']], ['Just leave her', 'Keep pursueing'])
        evs = [o['dimensions']['score']['ev'] for o in result['analysis']['options']]
        self.assertAlmostEqual(evs[0], 1.8)
        self.assertAlmostEqual(evs[1], -0.4)
        self.assertEqual(result['document']['options'][0]['costs'], {'score': 0})

    def test_heading_lines_money_and_explicit_zero(self):
        result = quick_text('Should I take the job or stay?\nTake the job:\n- 50% great fit = $20,000\n- 50% bad fit (-$5,000)\nStay: 100% nothing changes (0)')
        self.assertTrue(result['complete'])
        self.assertEqual(result['document']['dimensions'], ['money'])
        self.assertAlmostEqual(result['analysis']['options'][0]['dimensions']['money']['ev'], 7500)

    def test_missing_numbers_are_reported_not_invented(self):
        result = quick_text('girlfriend decision: should i just leave her or keep pursueing')
        self.assertFalse(result['complete'])
        self.assertIsNone(result['analysis'])
        self.assertEqual(len(result['missing']), 2)
        self.assertTrue(all('outcomes' not in o for o in result['document']['options']))
        partial = quick_text('Leave: 60% better (+5), 30% worse (-3)')
        self.assertIn('90%', partial['missing'][0])

    def test_short_option_names_match_their_lines(self):
        result = quick_text('Should I stay or go?\nStay: 100% same (0)\nGo: 50% better (+4), 50% worse (-2)')
        self.assertTrue(result['complete'])
        self.assertEqual([o['label'] for o in result['document']['options']], ['Stay', 'Go'])

    def test_unsigned_scores_and_k_m_amounts(self):
        result = quick_text('Should I sell or hold?\nSell: 100% cash out $5k\nHold: 50% doubles (+$10k), 50% halves (-$2.5k)\nGo: 60% better 4, 40% worse -2')
        self.assertTrue(result['complete'])
        evs = [o['dimensions']['money']['ev'] for o in result['analysis']['options']]
        self.assertEqual(evs[:2], [5000, 3750])
        self.assertAlmostEqual(evs[2], 1.6)

    def test_unreadable_clauses_are_reported_and_ready_options_still_analyzed(self):
        result = quick_text('Should I stay or go?\nStay: 100% same (0)\nGo: 60% better five, 40% worse (-2)')
        self.assertFalse(result['complete'])
        self.assertTrue(result['partial'])
        self.assertIn('60% better five', result['notes'][0])
        self.assertIn('40%', result['missing'][0])
        self.assertEqual([o['label'] for o in result['analysis']['options']], ['Stay'])
        self.assertFalse(quick_text('Should I stay or go?')['partial'])

    def test_unattributed_lines_are_noted(self):
        result = quick_text('60% fine (+1), 40% bad (-1)')
        self.assertFalse(result['complete'])
        self.assertTrue(result['notes'])


class QuickEndpointTests(unittest.TestCase):
    def test_endpoint_starts_no_session_model_or_write(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as folder:
            store = Store(pathlib.Path(folder) / 'db.sqlite', Fernet.generate_key())
            with mock.patch('lifeev.controls.route') as route, TestClient(create_app(store)) as client:
                body = client.post('/api/quick', json={'text': 'Stay: 100% same (0)\nGo: 50% up (+2), 50% down (-1)'}).json()
                self.assertTrue(body['complete'])
                self.assertEqual(body['method'], 'offline text parser + exact discrete statistics')
                route.assert_not_called()
                again = client.post('/api/quick', json={'document': body['document']}).json()
                self.assertEqual(again['analysis']['options'][1]['dimensions']['score']['ev'], .5)
                self.assertEqual(client.get('/api/nodes').json(), [])
                self.assertEqual(client.get('/api/sessions').json(), [])
                self.assertEqual(client.post('/api/quick', json={'text': ''}).status_code, 422)
