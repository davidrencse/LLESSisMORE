"""Descriptive decomposition and proportional probability sensitivity."""
import math


def _finite(value):
    if not math.isfinite(value):
        raise ValueError("analysis detail exceeds finite numeric limits")
    return value


def analyze_details(option, dimension, values, probabilities, cost):
    """Analyze a complete net-value distribution, preserving outcome order.

    Bounds are feasibility constraints supplied by the user, not confidence
    intervals. Sensitivity changes one probability and preserves the original
    relative mixture of all remaining outcomes, including their zero masses.
    """
    outcomes = option["outcomes"]
    if not outcomes or len(outcomes) != len(values) or len(values) != len(probabilities):
        raise ValueError("complete matching outcomes, values and probabilities required")
    try:
        return _details(outcomes, values, probabilities, cost)
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("analysis detail exceeds finite numeric limits") from exc


def _details(outcomes, values, ps, cost):
    mean = _finite(math.fsum(p * v for p, v in zip(ps, values)))
    gains = math.fsum(p for p, v in zip(ps, values) if v > 0)
    losses = math.fsum(p for p, v in zip(ps, values) if v < 0)
    gain = _finite(math.fsum(p * v for p, v in zip(ps, values) if v > 0))
    loss = _finite(math.fsum(p * -v for p, v in zip(ps, values) if v < 0))
    active = [v for p, v in zip(ps, values) if p > 0]
    # Scaling avoids overflow from squaring large, finite losses.
    scale = max([0.] + [-v for v in active if v < 0])
    downside = 0. if scale == 0 else _finite(scale * math.sqrt(math.fsum(
        p * ((-v) / scale) ** 2 for p, v in zip(ps, values) if v < 0)))
    rows = []
    for i, (outcome, p, value) in enumerate(zip(outcomes, ps, values)):
        row = {"label": outcome.get("label"), "probability": p,
               "feasible_probability_low": None, "feasible_probability_high": None,
               "ev_at_feasible_low": None, "ev_at_feasible_high": None,
               "zero_ev_probability": None, "break_even_within_bounds": None, "reason": None}
        rows.append(row)
        rest_mass = math.fsum(q for j, q in enumerate(ps) if j != i)
        if rest_mass == 0:
            row["reason"] = "Other outcomes have zero probability; their relative mixture is undefined."
            continue
        mixture = [(j, q / rest_mass) for j, q in enumerate(ps) if j != i]
        rest_ev = _finite(math.fsum(weight * values[j] for j, weight in mixture))
        lo = outcome.get("probability_low")
        hi = outcome.get("probability_high")
        lo, hi = (0. if lo is None else lo), (1. if hi is None else hi)
        for j, weight in mixture:
            lower = outcomes[j].get("probability_low")
            upper = outcomes[j].get("probability_high")
            lower, upper = (0. if lower is None else lower), (1. if upper is None else upper)
            if weight == 0:
                if lower > 0:
                    lo, hi = 1., 0.
                    break
            else:
                lo = max(lo, 1 - upper / weight)
                hi = min(hi, 1 - lower / weight)
        if lo > hi + 1e-12:
            row["reason"] = "No feasible probability preserves the other outcomes' relative mixture."
            continue
        hi = max(lo, hi)
        row.update(feasible_probability_low=lo, feasible_probability_high=hi,
                   ev_at_feasible_low=_finite(math.fsum((lo * value, (1 - lo) * rest_ev))),
                   ev_at_feasible_high=_finite(math.fsum((hi * value, (1 - hi) * rest_ev))))
        if value == rest_ev:
            row["reason"] = "EV is invariant; there is no unique break-even probability."
            continue
        # Scale before subtracting, so opposite extreme values remain finite.
        scale_ev = max(abs(value), abs(rest_ev))
        zero = -(rest_ev / scale_ev) / (value / scale_ev - rest_ev / scale_ev)
        if 0 <= zero <= 1:
            row["zero_ev_probability"] = zero
            row["break_even_within_bounds"] = lo - 1e-12 <= zero <= hi + 1e-12
        else:
            row["break_even_within_bounds"] = False
            row["reason"] = "Zero EV requires a probability outside [0, 1]."
    return {
        "contributions": [{"label": o.get("label"), "probability": p, "net_value": v,
                           "ev_contribution": _finite(p * v),
                           "black_swan": o.get("black_swan") if isinstance(o.get("black_swan"), bool) else None}
                          for o, p, v in zip(outcomes, ps, values)],
        "risk": {"gain_probability": gains, "zero_probability": math.fsum(p for p, v in zip(ps, values) if v == 0),
                 "expected_gain": gain, "expected_loss": loss,
                 "conditional_gain": None if gains == 0 else _finite(gain / gains),
                 "conditional_loss": None if losses == 0 else _finite(loss / losses),
                 "downside_deviation": downside, "minimum": min(active), "maximum": max(active)},
        "cost": {"current": cost, "break_even_total": _finite(math.fsum((cost, mean))),
                 "additional_to_zero_ev": mean, "ev_change_per_cost_unit": -1},
        "probability_sensitivity": rows}
