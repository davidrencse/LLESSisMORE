import math
import pytest

from lifeev.analysis_detail import analyze_details


def details(values, ps, bounds=None, cost=10):
    outcomes = [{"label": str(i)} for i in range(len(values))]
    for outcome, bound in zip(outcomes, bounds or []):
        outcome.update(bound)
    return analyze_details({"outcomes": outcomes}, "money", values, ps, cost)


def test_exact_decomposition():
    result = details([-20, 0, 100], [.25, .25, .5])
    assert [r["ev_contribution"] for r in result["contributions"]] == [-5, 0, 50]
    assert all(r["black_swan"] is None for r in result["contributions"])
    assert result["risk"] == {"gain_probability": .5, "zero_probability": .25,
        "expected_gain": 50, "expected_loss": 5, "conditional_gain": 100,
        "conditional_loss": 20, "downside_deviation": 10, "minimum": -20, "maximum": 100}
    assert result["cost"] == {"current": 10, "break_even_total": 55,
                              "additional_to_zero_ev": 45, "ev_change_per_cost_unit": -1}


def test_other_bounds_constrain_sensitivity_and_preserve_mass():
    bounds = [{"probability_low": .1, "probability_high": .9},
              {"probability_low": .3, "probability_high": .6},
              {"probability_low": .1, "probability_high": .2}]
    row = details([-100, 20, 80], [.2, .6, .2], bounds)["probability_sensitivity"][0]
    assert row["feasible_probability_low"] == pytest.approx(.2)
    assert row["feasible_probability_high"] == pytest.approx(.6)
    for q, ev in [(row["feasible_probability_low"], row["ev_at_feasible_low"]),
                  (row["feasible_probability_high"], row["ev_at_feasible_high"])]:
        adjusted = [q, (1-q)*.75, (1-q)*.25]
        assert math.fsum(adjusted) == pytest.approx(1)
        assert all(b["probability_low"] - 1e-12 <= p <= b["probability_high"] + 1e-12
                   for p, b in zip(adjusted, bounds))
        assert ev == pytest.approx(sum(p*v for p, v in zip(adjusted, [-100, 20, 80])))
    assert row["zero_ev_probability"] == pytest.approx(35/135)
    assert row["break_even_within_bounds"] is True


def test_deterministic_equal_and_zero_mass_outcomes():
    result = details([5, -1000], [1, 0])
    row = result["probability_sensitivity"][0]
    assert row["feasible_probability_low"] is None
    assert "undefined" in row["reason"]
    assert result["risk"]["minimum"] == 5
    assert result["risk"]["conditional_loss"] is None
    equal = details([3, 3], [.4, .6])["probability_sensitivity"][0]
    assert equal["zero_ev_probability"] is None
    assert "invariant" in equal["reason"]
    assert equal["ev_at_feasible_low"] == equal["ev_at_feasible_high"] == 3


def test_zero_other_mass_stays_zero_and_unknown_swan_preserved():
    result = details([-1, 1, 100], [.5, .5, 0])
    row = result["probability_sensitivity"][0]
    assert row["zero_ev_probability"] == .5
    assert row["ev_at_feasible_low"] == 1
    result = analyze_details({"outcomes": [{"black_swan": True}, {"black_swan": False}]},
                             "money", [-1, 1], [.5, .5], 0)
    assert [r["black_swan"] for r in result["contributions"]] == [True, False]


def test_finite_extreme_values_and_out_of_domain_break_even():
    result = details([-1e300, 1e300], [.5, .5], cost=0)
    assert math.isfinite(result["risk"]["downside_deviation"])
    assert result["probability_sensitivity"][0]["zero_ev_probability"] == .5
    row = details([1, 2], [.5, .5])["probability_sensitivity"][0]
    assert row["zero_ev_probability"] is None
    assert row["break_even_within_bounds"] is False
