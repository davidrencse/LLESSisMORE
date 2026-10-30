"""Illustrative inputs, never automatically persisted."""

def example():
    document = {
        "title": "A six-month learning project",
        "options": [
            {"id": "status-quo", "label": "Keep current schedule", "status_quo": True,
             "costs": {"money": 0, "time": 0}, "outcomes": [
                 {"label": "Downside", "probability": .2, "probability_low": .1, "probability_high": .3, "values": {"money": -200, "time": -10}},
                 {"label": "Base", "probability": .6, "probability_low": .5, "probability_high": .7, "values": {"money": 0, "time": 0}},
                 {"label": "Upside", "probability": .2, "probability_low": .1, "probability_high": .3, "values": {"money": 400, "time": 10}}]},
            {"id": "course", "label": "Take a part-time course", "status_quo": False,
             "costs": {"money": 1200, "time": 80}, "outcomes": [
                 {"label": "Downside", "probability": .25, "probability_low": .15, "probability_high": .35, "values": {"money": 0, "time": 0}},
                 {"label": "Base", "probability": .5, "probability_low": .4, "probability_high": .6, "values": {"money": 1800, "time": 20}},
                 {"label": "Upside", "probability": .24, "probability_low": .14, "probability_high": .34, "values": {"money": 4500, "time": 30}},
                 {"label": "Unexpected additional expense", "probability": .01, "probability_low": 0, "probability_high": .03, "values": {"money": -5000, "time": -40}, "black_swan": True}]}],
        "weights": {"money": 1, "time": 10}, "weight_version": "illustrative-v1",
        "bankroll": {"money": 15000, "time": 500}, "correlations": [],
        "horizon": "6 months", "reversibility": "Course fee is non-refundable; time commitment can stop.",
        "thresholds": {"money": -2500}, "skipped": []
    }
    for option in document['options']:
        for outcome in option['outcomes']:
            outcome.setdefault('black_swan', False)
    return document
