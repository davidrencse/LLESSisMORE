"""Numbers as people type them: signs (including −), $, thousands commas and k/m suffixes."""
import re

AMOUNT = r'\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*[km]\b)?'
NUMBER = r'[+\-−]?\s*' + AMOUNT


def value(raw):
    raw = raw.replace('−', '-').strip()
    scale = {'k': 1e3, 'm': 1e6}.get(raw[-1:].lower(), 1)
    return float(re.sub(r'[\s$,kKmM]', '', raw)) * scale
