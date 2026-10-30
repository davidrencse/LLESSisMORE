"""Exact discrete descriptive statistics; no ranking or recommendations.

Option metrics: dimensions (net EV, variance, SD, quantiles, lower-tail CVaR,
loss/threshold/black-swan probabilities), weighted_utility, probability_ev_bounds,
sensitivity, kelly, risk_of_ruin, correlation_adjustment, unavailable.
Bounds describe feasible probabilities, never statistical confidence intervals.
"""
import math
from .analysis_detail import analyze_details

CALCULATION_VERSION = 'lless-discrete-v3'


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def chain_probabilities(probabilities, depth_cap=12):
    """Multiply conditional chain probabilities in log space, capped explicitly."""
    if not isinstance(depth_cap, int) or isinstance(depth_cap, bool) or depth_cap < 1:
        raise ValueError("depth_cap must be a positive integer")
    probabilities = list(probabilities)
    if len(probabilities) > depth_cap:
        raise ValueError("chain exceeds depth cap")
    values = [_number(p, "probability") for p in probabilities]
    if any(p < 0 or p > 1 for p in values):
        raise ValueError("probability must be in [0, 1]")
    return 0.0 if 0 in values else math.exp(math.fsum(math.log(p) for p in values))


def _quantile(pairs, q, ordered=False):
    total = 0.0
    for value, probability in pairs if ordered else sorted(pairs):
        total += probability
        if total >= q:
            return value
    return max(v for v, _ in pairs)


def _metrics(values, probabilities, threshold=None):
    pairs = sorted((v, p) for v, p in zip(values, probabilities) if p > 0)
    mean = math.fsum(v * p for v, p in pairs)
    variance = math.fsum(p * (v - mean) ** 2 for v, p in pairs)
    remaining, tail = .05, 0.0
    for value, probability in pairs:
        mass = min(remaining, probability)
        tail += mass * value
        remaining -= mass
        if remaining <= 0:
            break
    tail01 = 0.0
    remaining01 = .01
    for value, probability in pairs:
        mass = min(remaining01, probability)
        tail01 += mass * value
        remaining01 -= mass
        if remaining01 <= 0:
            break
    return {"ev": mean, "variance": variance, "standard_deviation": math.sqrt(variance),
            "p05": _quantile(pairs, .05, True), "p10": _quantile(pairs, .1, True),
            "p25": _quantile(pairs, .25, True), "median": _quantile(pairs, .5, True),
            "p75": _quantile(pairs, .75, True), "p90": _quantile(pairs, .9, True),
            "p95": _quantile(pairs, .95, True), "cvar_lower_05": tail / .05,
            "cvar_lower_01": tail01 / .01,
            "expected_drawdown": math.fsum(p * max(0, -v) for v, p in pairs),
            "max_drawdown": max(max(0, -v) for v, p in pairs),
            "drawdown_kind": "one-period loss from starting value; not path-dependent peak-to-trough drawdown",
            "loss_probability": math.fsum(p for v, p in pairs if v < 0),
            "threshold_probability": None if threshold is None else math.fsum(p for v, p in pairs if v <= threshold)}


def _bounds(values, lows, highs, maximize):
    probabilities = list(lows)
    remaining = max(0.0, 1 - math.fsum(lows))
    for i in sorted(range(len(values)), key=lambda i: values[i], reverse=maximize):
        mass = min(remaining, highs[i] - lows[i])
        probabilities[i] += mass
        remaining -= mass
    return math.fsum(v * p for v, p in zip(values, probabilities))


def _kelly(values, probabilities, bankroll):
    if bankroll <= 0:
        return None
    # Search only solvent exposures. Searching [0, 1] directly can leave both
    # probes insolvent and discard a small but profitable feasible interval.
    active = [(v, p) for v, p in zip(values, probabilities) if p > 0]
    hi = min([1.] + [bankroll / -v for v, _ in active if v < 0])
    def growth(f):
        if f == 0:
            return 0.0
        terms = []
        for v, p in active:
            ratio = (f * v) / bankroll
            if ratio <= -1:
                return -math.inf
            if math.isfinite(ratio):
                log_growth = math.log1p(ratio)
            else:
                # Positive wealth ratios can overflow although their logarithm
                # and every input are finite.
                log_growth = math.log(f) + math.log(v) - math.log(bankroll)
            terms.append(p * log_growth)
        return math.fsum(terms)
    lo = 0.
    endpoint = hi
    for _ in range(100):
        a, b = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if growth(a) < growth(b):
            lo = a
        else:
            hi = b
    f = max([0., endpoint, (lo + hi) / 2], key=growth)
    return {"fraction": f, "half_fraction": f / 2, "quarter_fraction": f / 4,
            "expected_log_growth": growth(f),
            "half_expected_log_growth": growth(f / 2), "quarter_expected_log_growth": growth(f / 4),
            "domain": "0 <= fraction <= 1; fraction of the specified monetary exposure, not fraction of bankroll; no leverage"}


def analyze(document, approximation=False):
    assumptions = ["Outcomes are mutually exclusive and exhaustive; stated probabilities are inputs, not empirical confidence.",
                   "Outcome benefits are reduced by option costs in the same units.",
                   "CVaR averages the worst 1% or 5% of net values, splitting probability mass at the boundary."]
    result = {"options": [], "confidence": {"value": None, "reason": "No calibrated confidence model; completeness is not statistical confidence."},
              "assumptions": assumptions, "method": "exact discrete distribution", "calculation_version": CALCULATION_VERSION}
    if approximation:
        result["method"] += "; approximation requested but exact calculation retained"
    weights = document.get("weights")
    if weights is not None:
        weights = {d: _number(w, "weight") for d, w in weights.items()}
    thresholds = {d: _number(v, "threshold") for d, v in document.get("thresholds", {}).items()}
    bankrolls = {d: _number(v, "bankroll") for d, v in document.get("bankroll", {}).items()}
    for option in document.get("options", []):
        out = {"id": option.get("id"), "label": option.get("label"), "dimensions": {}, "weighted_utility": None,
               "probability_ev_bounds": {}, "sensitivity": {}, "kelly": None, "risk_of_ruin": None,
               "correlation_adjustment": None, "stack_commitment": {}, "uncertainty_ev_bounds": {}, "analysis_details": {}, "unavailable": {}}
        result["options"].append(out)
        outcomes = option.get("outcomes", [])
        costs = {d: _number(v, "cost") for d, v in option.get("costs", {}).items()}
        out["stack_commitment"] = {d: {"fraction": cost / bankrolls[d],
            "kind": "specified cost divided by bankroll in the same dimension"}
            for d, cost in costs.items() if bankrolls.get(d, 0) > 0}
        for o in outcomes:
            for v in o.get("values", {}).values():
                _number(v, "outcome value")
            for key in ("probability", "probability_low", "probability_high"):
                if o.get(key) is not None and not 0 <= _number(o[key], key) <= 1:
                    raise ValueError(f"{key} must be in [0, 1]")
            if o.get("probability_low") is not None and o.get("probability_high") is not None and o["probability_low"] > o["probability_high"]:
                raise ValueError("probability bounds are reversed")
            if o.get("probability") is not None:
                if o.get("probability_low") is not None and o["probability"] < o["probability_low"]:
                    raise ValueError("probability is below its range")
                if o.get("probability_high") is not None and o["probability"] > o["probability_high"]:
                    raise ValueError("probability is above its range")
        if outcomes:
            if math.fsum(o.get("probability_low") or 0 for o in outcomes) > 1 + 1e-12 or math.fsum(1 if o.get("probability_high") is None else o["probability_high"] for o in outcomes) < 1 - 1e-12:
                raise ValueError("probability ranges cannot sum to one")
        complete_p = bool(outcomes) and all(o.get("probability") is not None for o in outcomes)
        ps = [float(o["probability"]) for o in outcomes] if complete_p else None
        if ps is not None and not math.isclose(math.fsum(ps), 1, abs_tol=1e-9, rel_tol=0):
            raise ValueError("outcome probabilities must sum to one")
        dims = set(costs) | {d for o in outcomes for d in o.get("values", {})}
        net = {}
        for d in sorted(dims):
            if d not in costs or not outcomes or any(d not in o.get("values", {}) for o in outcomes):
                out["dimensions"][d] = None
                out["unavailable"][d] = "Explicit cost and every outcome value are required."
                continue
            values = net[d] = [o["values"][d] - costs[d] for o in outcomes]
            if ps is not None:
                out["dimensions"][d] = _metrics(values, ps, thresholds.get(d))
                known_swan = all(isinstance(o.get("black_swan"), bool) for o in outcomes)
                out["dimensions"][d]["black_swan_probability"] = (math.fsum(p for o, p in zip(outcomes, ps) if o.get("black_swan", False)) if known_swan else None)
                out["dimensions"][d]["black_swan_ev_contribution"] = (math.fsum(p * v for o, p, v in zip(outcomes, ps, values) if o.get("black_swan", False)) if known_swan else None)
                out["sensitivity"][d] = {"additional_cost_to_zero_ev": out["dimensions"][d]["ev"], "unit": d}
                out["analysis_details"][d] = analyze_details(option, d, values, ps, costs[d])
            else:
                out["dimensions"][d] = None
            if all(o.get("probability_low") is not None and o.get("probability_high") is not None for o in outcomes):
                lows, highs = [o["probability_low"] for o in outcomes], [o["probability_high"] for o in outcomes]
                if math.fsum(lows) > 1 + 1e-12 or math.fsum(highs) < 1 - 1e-12:
                    raise ValueError("probability ranges cannot sum to one")
                out["probability_ev_bounds"][d] = {"low": _bounds(values, lows, highs, False), "high": _bounds(values, lows, highs, True), "kind": "feasible probability EV bounds; not confidence interval"}
            # A support envelope is a heuristic sensitivity bound, never confidence.
            relevant_skips = [field for field in document.get("skipped", [])
                              if field in ("options", "probabilities", "costs", "outcomes")
                              or field.startswith("options.") or field.startswith("probabilities.")]
            has_ranges = all(o.get("probability_low") is not None and o.get("probability_high") is not None for o in outcomes)
            if relevant_skips or ps is None or not has_ranges:
                low, high = min(values), max(values)
                reason = "relevant skips or missing probability uncertainty inputs"
            else:
                low = out["probability_ev_bounds"][d]["low"]
                high = out["probability_ev_bounds"][d]["high"]
                reason = "specified feasible probability ranges"
            out["uncertainty_ev_bounds"][d] = {"low": low, "high": high,
                "kind": "heuristic EV sensitivity bounds within stated outcome support; not calibrated confidence",
                "reason": reason, "unknown_tail_support": "unbounded or unspecified tails are not modeled",
                "approximation_widening": 0.0, "calculation": "exact discrete; no numerical approximation error"}
        if ps is None:
            out["unavailable"]["probabilities"] = "Every outcome probability is required."
        if weights and ps is not None and all(d in net for d, w in weights.items() if w != 0):
            utility = [math.fsum(w * net[d][i] for d, w in weights.items() if w != 0) for i in range(len(outcomes))]
            out["weighted_utility"] = _metrics(utility, ps)
        else:
            out["unavailable"]["weighted_utility"] = "Explicit weights, probabilities, costs and values required."
        if ps is not None and "money" in net and bankrolls.get("money", 0) > 0:
            b = bankrolls["money"]
            out["kelly"] = _kelly(net["money"], ps, b)
            out["risk_of_ruin"] = {"probability": math.fsum(p for v, p in zip(net["money"], ps) if b + v <= 0), "kind": "one-period bankroll-exhaustion proxy; not eventual ruin"}
        else:
            out["unavailable"]["kelly_and_ruin"] = "Positive monetary bankroll and complete monetary distribution required."
        correlations = [c for c in document.get("correlations", [])
                        if c.get("option_id", c.get("node_id")) == option.get("id")]
        if len(correlations) == 1 and out["dimensions"].get("money") is not None and correlations[0].get("exposure_kind") == "monetary_standard_deviation":
            c = correlations[0]
            rho, exposure = _number(c.get("rho"), "rho"), _number(c.get("exposure"), "exposure")
            if not -1 <= rho <= 1 or exposure < 0:
                raise ValueError("rho must be in [-1, 1] and standard deviation exposure nonnegative")
            m = out["dimensions"]["money"]
            covariance_impact = 2 * rho * m["standard_deviation"] * exposure
            out["correlation_adjustment"] = {"covariance_variance_impact": covariance_impact,
                "combined_variance": max(0.0, m["variance"] + exposure ** 2 + covariance_impact),
                "marginal_ev_change": 0.0,
                "kind": "variance of option plus external monetary exposure; external exposure explicitly a standard deviation"}
        else:
            out["unavailable"]["correlation_adjustment"] = "A single matched rho and exposure explicitly typed as monetary_standard_deviation is required; joint outcomes and tail probabilities are not inferred."

    if document.get("skipped"):
        assumptions.append("Relevant skipped inputs widen heuristic EV bounds to the full stated outcome support; unknown tails remain unspecified.")
    return result
