import unittest
from lifeev.skeleton import validate, gaps, assess, accept_override


class SkeletonTests(unittest.TestCase):
    def test_draft(self):
        self.assertEqual(validate({}), [])
        self.assertTrue(gaps({}))

    def test_untrusted_types(self):
        for doc in ({"options": [None]}, {"weights": {"money": True}}, {"correlations": [2]}, {"options": [{"outcomes": 1}]}, {"bankroll": {"money": float("nan")}}):
            self.assertTrue(validate(doc))

    def test_probability_feasibility(self):
        self.assertTrue(validate({"options": [{"outcomes": [{"probability": .8}, {"probability": .8}]}]}))
        self.assertEqual(validate({"options": [{"outcomes": [{"probability": .5}, {"probability": .5}]}]}), [])
        self.assertTrue(validate({"options": [{"outcomes": [{"probability_low": .8, "probability_high": .2}]}]}))

    def test_explicit_skips_visible(self):
        source = {}
        with self.assertRaises(ValueError):
            accept_override(source, "options", False)
        updated = accept_override(source, "options", True)
        self.assertNotIn("skipped", source)
        self.assertNotIn("options", [g["field"] for g in gaps(updated)])
        self.assertIn("options", assess(updated)["skipped"])
        self.assertIn("not statistical", assess(updated)["confidence_kind"])

    def test_context_and_empty_thresholds(self):
        doc = {"thresholds": {}, "correlations": [], "options": [{"id": "a", "outcomes": [{"label": "base"}]}]}
        fields = [g["field"] for g in gaps(doc)]
        self.assertNotIn("thresholds", fields)
        self.assertIn("status_quo", fields)
        self.assertIn("options.0.outcomes", fields)
        self.assertIn("options.0.costs", fields)


if __name__ == "__main__":
    unittest.main()
