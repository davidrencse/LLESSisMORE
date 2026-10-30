import math
import unittest

from lifeev.stats import analyze, chain_probabilities, _kelly


def decision():
    return {"weights": {"money": 1, "time": 2}, "bankroll": {"money": 100},
            "thresholds": {"money": 0}, "options": [{"id": "a", "label": "A",
            "costs": {"money": 10, "time": 1}, "outcomes": [
                {"probability": .02, "probability_low": .01, "probability_high": .1,
                 "values": {"money": -100, "time": -2}, "black_swan": True},
                {"probability": .98, "probability_low": .9, "probability_high": .99,
                 "values": {"money": 20, "time": 3}, "black_swan": False}]}]}


class StatisticsTests(unittest.TestCase):
    def test_discrete_fractional_tail_and_dimensions(self):
        o = analyze(decision())["options"][0]
        m = o["dimensions"]["money"]
        self.assertAlmostEqual(m["ev"], 7.6)
        self.assertAlmostEqual(m["cvar_lower_05"], -38)
        self.assertEqual(m["p05"], 10)
        self.assertEqual(m["black_swan_probability"], .02)
        self.assertEqual(m["threshold_probability"], .02)
        self.assertAlmostEqual(o["weighted_utility"]["ev"], 11.4)
        self.assertEqual(o["risk_of_ruin"]["probability"], .02)
        self.assertAlmostEqual(o["probability_ev_bounds"]["money"]["low"], -2)
        self.assertAlmostEqual(o["probability_ev_bounds"]["money"]["high"], 8.8)

    def test_missing_is_unavailable(self):
        d = decision()
        del d["options"][0]["costs"]["money"]
        o = analyze(d)["options"][0]
        self.assertIsNone(o["dimensions"]["money"])
        self.assertIsNone(o["weighted_utility"])
        self.assertIsNone(o["kelly"])

    def test_invalid_probabilities(self):
        for p in (-.1, 1.1, math.nan, True, .4):
            d = decision()
            d["options"][0]["outcomes"][0]["probability"] = p
            with self.assertRaises(ValueError):
                analyze(d)

    def test_kelly_uses_money_only(self):
        d = decision()
        a = analyze(d)["options"][0]["kelly"]
        d["weights"] = {"time": 1000}
        self.assertEqual(a, analyze(d)["options"][0]["kelly"])
        self.assertTrue(0 <= a["fraction"] < 100 / 110)
        self.assertGreater(a["expected_log_growth"], 0)

    def test_extended_metrics(self):
        d = decision()
        m = analyze(d)["options"][0]["dimensions"]["money"]
        for key in ("p10", "p25", "p75", "p90"):
            self.assertEqual(m[key], 10)
        self.assertEqual(m["cvar_lower_01"], -110)
        self.assertAlmostEqual(m["black_swan_ev_contribution"], -2.2)
        self.assertAlmostEqual(m["expected_drawdown"], 2.2)
        self.assertEqual(m["max_drawdown"], 110)
        o = analyze(d)["options"][0]
        self.assertEqual(o["stack_commitment"]["money"]["fraction"], .1)
        self.assertEqual(o["kelly"]["half_fraction"], o["kelly"]["fraction"] / 2)
        self.assertEqual(o["kelly"]["quarter_fraction"], o["kelly"]["fraction"] / 4)

    def test_unassessed_swan_flags_remain_unknown(self):
        d = decision()
        del d["options"][0]["outcomes"][1]["black_swan"]
        self.assertIsNone(analyze(d)["options"][0]["dimensions"]["money"]["black_swan_probability"])
        d["options"][0]["outcomes"][1]["black_swan"] = None
        self.assertIsNone(analyze(d)["options"][0]["dimensions"]["money"]["black_swan_ev_contribution"])

    def test_support_uncertainty_and_exact_approximation(self):
        d = decision()
        baseline = analyze(d)["options"][0]["uncertainty_ev_bounds"]["money"]
        self.assertAlmostEqual(baseline["low"], -2)
        d["skipped"] = ["options.0.outcomes"]
        bound = analyze(d, approximation=True)["options"][0]["uncertainty_ev_bounds"]["money"]
        self.assertEqual((bound["low"], bound["high"]), (-110, 10))
        self.assertEqual(bound["approximation_widening"], 0)
        self.assertIn("not calibrated confidence", bound["kind"])

    def test_correlation_requires_sd_interpretation(self):
        d = decision()
        d["correlations"] = [{"node_id": "a", "rho": -1, "exposure": 10}]
        self.assertIsNone(analyze(d)["options"][0]["correlation_adjustment"])
        d["correlations"][0]["exposure_kind"] = "monetary_standard_deviation"
        o = analyze(d)["options"][0]
        expected = (o["dimensions"]["money"]["standard_deviation"] - 10) ** 2
        self.assertAlmostEqual(o["correlation_adjustment"]["combined_variance"], expected)

    def test_quantile_mass_boundaries_and_zero_probability(self):
        d = {"options": [{"costs": {"money": 0}, "outcomes": [
            {"probability": 0, "values": {"money": -10000}},
            {"probability": .005, "values": {"money": -20}},
            {"probability": .095, "values": {"money": -10}},
            {"probability": .15, "values": {"money": 0}},
            {"probability": .5, "values": {"money": 5}},
            {"probability": .25, "values": {"money": 10}}]}]}
        m = analyze(d)["options"][0]["dimensions"]["money"]
        self.assertEqual(m["p10"], -10)
        self.assertEqual(m["p25"], 0)
        self.assertEqual(m["p75"], 5)
        self.assertEqual(m["p90"], 10)
        self.assertAlmostEqual(m["cvar_lower_01"], -15)
        self.assertEqual(m["max_drawdown"], 20)

    def test_kelly_searches_small_solvent_interval(self):
        # The log-optimal exposure is 0.49 / 1e12. Both original probes
        # on [0, 1] would bankrupt the losing outcome.
        k = _kelly([1e12, -1e12], [.745, .255], 1)
        self.assertAlmostEqual(k["fraction"] * 1e12, .49, places=6)
        self.assertGreater(k["expected_log_growth"], 0)
        self.assertEqual(_kelly([-1e12], [1], 1)["fraction"], 0)

    def test_kelly_overflowing_return_and_zero_mass(self):
        k = _kelly([1e100, -1e200], [1, 0], 1e-300)
        self.assertEqual(k["fraction"], 1)
        self.assertTrue(math.isfinite(k["expected_log_growth"]))
        self.assertAlmostEqual(k["expected_log_growth"], 400 * math.log(10))
        k = _kelly([1, -1], [.75, .25], 1e-300)
        self.assertAlmostEqual(k["fraction"] / 1e-300, .5, places=6)

    def test_partial_probability_ranges_must_be_feasible(self):
        d = {"options": [{"costs": {"focus": 0}, "outcomes": [
            {"probability_low": .6, "values": {"focus": 1}},
            {"probability_low": .6, "values": {"focus": 2}}]}]}
        with self.assertRaisesRegex(ValueError, "cannot sum to one"):
            analyze(d)

    def test_custom_dimension_support_and_approximation(self):
        d = {"weights": {"focus": 2}, "thresholds": {"focus": 1},
             "options": [{"costs": {"focus": 3}, "outcomes": [
                 {"probability": .25, "values": {"focus": 1}},
                 {"probability": .75, "values": {"focus": 7}}]}]}
        exact, requested = analyze(d), analyze(d, approximation=True)
        o = requested["options"][0]
        self.assertEqual(exact["options"], requested["options"])
        self.assertEqual(o["dimensions"]["focus"]["ev"], 2.5)
        self.assertEqual(o["dimensions"]["focus"]["threshold_probability"], .25)
        self.assertEqual(o["weighted_utility"]["ev"], 5)
        self.assertIsNone(o["kelly"])
        b = o["uncertainty_ev_bounds"]["focus"]
        self.assertEqual((b["low"], b["high"]), (-2, 4))
        self.assertEqual(b["approximation_widening"], 0)
        self.assertIsNone(requested["confidence"]["value"])

    def test_tail_keeps_small_boundary_mass(self):
        p = .05 - 1e-15
        d = {"options": [{"costs": {"money": 0}, "outcomes": [
            {"probability": p, "values": {"money": 0}},
            {"probability": 1 - p, "values": {"money": 1e15}}]}]}
        m = analyze(d)["options"][0]["dimensions"]["money"]
        self.assertEqual(m["p05"], 1e15)
        self.assertAlmostEqual(m["cvar_lower_05"], (.05 - p) * 1e15 / .05)

    def test_chain(self):
        self.assertAlmostEqual(chain_probabilities([.5, .2]), .1)
        self.assertEqual(chain_probabilities([]), 1)
        self.assertEqual(chain_probabilities([0, .3]), 0)
        with self.assertRaises(ValueError):
            chain_probabilities([.5] * 13)
        with self.assertRaises(ValueError):
            chain_probabilities([2])


if __name__ == "__main__":
    unittest.main()
