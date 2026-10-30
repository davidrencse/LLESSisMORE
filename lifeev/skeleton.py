"""Draft validation and transparent, heuristic decision completeness."""
from copy import deepcopy
import math

REQUIRED = ["title", "options", "weights", "weight_version", "bankroll", "correlations", "horizon", "reversibility", "thresholds", "status_quo"]
MAX_OPTIONS = 30
MAX_OUTCOMES = 100


def _finite(value):
    try:
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def validate(document):
    """Return errors, allowing absent fields in an unfinished draft."""
    errors = []
    def error(path, message):
        errors.append(f"{path}: {message}")
    def number(value, path, low=None, high=None):
        if not _finite(value):
            error(path, "must be a finite number")
            return False
        if low is not None and value < low or high is not None and value > high:
            error(path, "outside allowed range")
            return False
        return True
    def mapping(value, path, nonnegative=False):
        if not isinstance(value, dict):
            error(path, "must be an object")
            return
        for key, val in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 80:
                error(path, "dimension names must be nonempty text up to 80 characters")
            if isinstance(key, str) and '.' in key:
                error(path, "dimension names cannot contain periods")
            number(val, f"{path}.{key}", 0 if nonnegative else None)
    if not isinstance(document, dict):
        return ["document: must be an object"]
    # Bound arbitrary metadata as well as schema lists; never recurse indefinitely.
    budget = [0]
    def bounded(value, depth=0):
        budget[0] += 1
        if depth > 12 or budget[0] > 20000:
            raise ValueError("document exceeds size/depth limits")
        if isinstance(value, dict):
            for key, val in value.items():
                if not isinstance(key, str) or len(key) > 200:
                    raise ValueError("document keys must be short strings")
                bounded(val, depth + 1)
        elif isinstance(value, list):
            if len(value) > 1000:
                raise ValueError("document list exceeds size limit")
            for val in value:
                bounded(val, depth + 1)
        elif isinstance(value, str):
            if len(value) > 20000:
                raise ValueError("document text exceeds size limit")
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if not _finite(value):
                raise ValueError("document numbers must be finite")
        elif value is not None and not isinstance(value, bool):
            raise ValueError("document must contain JSON values")
    try:
        bounded(document)
    except (ValueError, RecursionError) as exc:
        return [str(exc)]
    for field in ("title", "weight_version", "horizon", "reversibility"):
        if field in document and not isinstance(document[field], str):
            error(field, "must be text")
    for field in ("weights", "bankroll", "thresholds"):
        if field in document:
            mapping(document[field], field, field != "thresholds")
    if "skipped" in document and (not isinstance(document["skipped"], list) or any(not isinstance(x, str) for x in document["skipped"])):
        error("skipped", "must be a list of field names")
    for field in ('skip_provenance', 'override_acceptances'):
        if field in document:
            records = document[field]
            if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                error(field, 'must be a list of provenance objects')
    options = document.get("options", [])
    if not isinstance(options, list):
        error("options", "must be a list")
        options = []
    if len(options) > MAX_OPTIONS:
        error("options", "too many options")
    ids = set()
    for i, option in enumerate(options):
        path = f"options.{i}"
        if not isinstance(option, dict):
            error(path, "must be an object")
            continue
        for key in ("id", "label"):
            if key in option and (not isinstance(option[key], str) or not option[key].strip()):
                error(f"{path}.{key}", "must be nonempty text")
        if isinstance(option.get("id"), str):
            if option["id"] in ids:
                error(path + ".id", "must be unique")
            ids.add(option["id"])
        if "status_quo" in option and not isinstance(option["status_quo"], bool):
            error(path + ".status_quo", "must be boolean")
        if "costs" in option:
            mapping(option["costs"], path + ".costs")
        outcomes = option.get("outcomes", [])
        if not isinstance(outcomes, list):
            error(path + ".outcomes", "must be a list")
            continue
        if len(outcomes) > MAX_OUTCOMES:
            error(path + ".outcomes", "too many outcomes")
        lows, highs = [], []
        for j, outcome in enumerate(outcomes):
            op = f"{path}.outcomes.{j}"
            if not isinstance(outcome, dict):
                error(op, "must be an object")
                continue
            if "label" in outcome and not isinstance(outcome["label"], str):
                error(op + ".label", "must be text")
            for key in ("probability", "probability_low", "probability_high"):
                if key in outcome:
                    number(outcome[key], op + "." + key, 0, 1)
            vals = [outcome.get(k) for k in ("probability_low", "probability", "probability_high")]
            valid = lambda v: _finite(v) and 0 <= v <= 1
            if valid(vals[0]) and valid(vals[1]) and vals[1] < vals[0]:
                error(op, "probability is below its lower bound")
            if valid(vals[1]) and valid(vals[2]) and vals[1] > vals[2]:
                error(op, "probability is above its upper bound")
            if all(valid(v) for v in vals) and not vals[0] <= vals[1] <= vals[2]:
                error(op, "probability must lie within ordered bounds")
            if valid(vals[0]) and valid(vals[2]) and vals[0] > vals[2]:
                error(op, "probability bounds reversed")
            lows.append(vals[0] if valid(vals[0]) else vals[1] if valid(vals[1]) else 0)
            highs.append(vals[2] if valid(vals[2]) else vals[1] if valid(vals[1]) else 1)
            if "values" in outcome:
                mapping(outcome["values"], op + ".values")
            if "black_swan" in outcome and not isinstance(outcome["black_swan"], bool):
                error(op + ".black_swan", "must be boolean")
        if outcomes and (sum(lows) > 1 + 1e-9 or sum(highs) < 1 - 1e-9):
            error(path + ".outcomes", "probabilities cannot sum to one within bounds")
    correlations = document.get("correlations", [])
    if not isinstance(correlations, list):
        error("correlations", "must be a list")
    else:
        for i, item in enumerate(correlations):
            path = f"correlations.{i}"
            if not isinstance(item, dict):
                error(path, "must be an object")
                continue
            if "node_id" in item and not isinstance(item["node_id"], str):
                error(path + ".node_id", "must be text")
            for key in ("rho", "exposure"):
                if key in item:
                    number(item[key], path + "." + key, -1 if key == "rho" else None, 1 if key == "rho" else None)
    return errors


def gaps(document):
    if not isinstance(document, dict):
        return [{"field": "document", "question": "Provide a decision object.", "impact": 100}]
    result = []
    skipped = document.get("skipped", [])
    skipped = skipped if isinstance(skipped, list) else []
    def add(field, question, impact):
        if not any(field == s or field.startswith(s + ".") for s in skipped if isinstance(s, str)):
            result.append(dict(field=field, question=question, impact=impact))
    for field in REQUIRED:
        if field == "status_quo":
            continue
        if field not in document or document[field] is None or document[field] == "" or (field in ("options", "weights", "bankroll") and not document[field]):
            add(field, f"Provide {field.replace('_', ' ')} for this decision.", {"options": 100, "weights": 95, "bankroll": 85}.get(field, 60))
    options = document.get("options", [])
    if not isinstance(options, list):
        return sorted(result, key=lambda g: -g["impact"])
    if not any(isinstance(o, dict) and o.get("status_quo") is True for o in options):
        add("status_quo", "Which option represents the status quo?", 95)
    for i, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        path = f"options.{i}"
        for key in ("id", "label", "costs"):
            if key not in option or option[key] in (None, ""):
                add(path + "." + key, f"Provide {key} for option {i + 1}.", 75)
        dimensions = set(document.get("weights", {})) if isinstance(document.get("weights"), dict) else {"money", "time"}
        if isinstance(option.get("costs"), dict):
            for dimension in dimensions:
                if dimension not in option["costs"]:
                    add(f"{path}.costs.{dimension}", f"Provide {dimension} costs for option {i + 1}, including zero if none.", 80)
        outcomes = option.get("outcomes", [])
        if not isinstance(outcomes, list) or not outcomes:
            add(path + ".outcomes", f"Describe base, upside and downside outcomes for option {i + 1}.", 100)
            continue
        labels = {str(o.get("label", "")).lower().strip() for o in outcomes if isinstance(o, dict)}
        if not all(labels & group for group in ({"base", "base case"}, {"up", "upside", "best case"}, {"down", "downside", "worst case"})):
            add(path + ".outcomes", f"Include labeled base, upside and downside cases for option {i + 1}.", 90)
        for j, outcome in enumerate(outcomes):
            if isinstance(outcome, dict):
                for key in ("label", "probability", "probability_low", "probability_high", "values", "black_swan"):
                    if key not in outcome or outcome[key] is None:
                        add(f"{path}.outcomes.{j}.{key}", f"Provide {key.replace('_', ' ')} for outcome {j + 1} of option {i + 1}.", 88 if key.startswith("probability") else 80)
                if isinstance(outcome.get("values"), dict):
                    for dimension in dimensions:
                        if dimension not in outcome["values"]:
                            add(f"{path}.outcomes.{j}.values.{dimension}", f"Provide the {dimension} value before costs.", 85)
    for i, correlation in enumerate(document.get('correlations', []) or []):
        if isinstance(correlation, dict):
            for key in ('node_id', 'rho', 'exposure'):
                if key not in correlation or correlation[key] is None or correlation[key] == '':
                    add(f'correlations.{i}.{key}', f'Provide {key.replace("_", " ")} for correlation {i + 1}.', 82)
    return sorted(result, key=lambda g: -g["impact"])


def assess(document):
    missing = gaps(document)
    skipped = document.get("skipped", []) if isinstance(document, dict) else []
    skipped = skipped if isinstance(skipped, list) else []
    errors = validate(document)
    completeness = max(0.0, 1 - len(missing) / max(len(REQUIRED), len(missing) + len(REQUIRED)))
    confidence = max(0.0, completeness - min(.8, len(skipped) * .08) - min(.8, len(errors) * .1))
    return dict(confidence=round(confidence, 3), completeness=round(completeness, 3), skipped=list(skipped), gaps=missing, errors=errors, confidence_kind="heuristic completeness score; not statistical confidence")


def accept_override(document, field, accepted):
    """Record an explicit uncertainty acceptance, never silently change inputs."""
    if not isinstance(accepted, bool) or accepted is not True:
        raise ValueError("override requires explicit acceptance")
    if not isinstance(field, str) or not field.strip():
        raise ValueError("field must be nonempty text")
    errors = validate(document)
    if errors:
        raise ValueError("; ".join(errors))
    result = deepcopy(document)
    result.setdefault("skipped", [])
    if field not in result["skipped"]:
        result["skipped"].append(field)
    result.setdefault("override_acceptances", []).append({"field": field, "accepted": True, "provenance": "explicit acceptance"})
    return result
