"""Auditable routing, content-addressed caching, and offline retrieval controls.

The default retrieval backend is a hashed-vector / lexical-pair fallback. It is
not a learned embedding model or cross encoder. Provider callbacks are optional.
"""
import hashlib
import heapq
import json
import math
import re
from collections import Counter, OrderedDict
from threading import Lock, RLock


TIERS = ("small", "medium", "large")


def route(document, force_tier=None, confidence=1):
    """Choose a compute tier; a forced tier is a floor, never a safety bypass."""
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("confidence must be between zero and one")
    if force_tier is not None and force_tier not in TIERS:
        raise ValueError("force_tier must be small, medium, or large")
    options = document.get("options", [])
    outcomes = [o for option in options for o in option.get("outcomes", [])]
    reasons = []
    score = len(options) + len(outcomes) / 2
    text = json.dumps(document, sort_keys=True)
    if len(text) > 8000:
        score += 4
        reasons.append('Large input requires additional context capacity')
    if re.search(r'\b(uncertain|unknown|ambiguous|complex|irreversible)\b', text, re.I):
        score += 2
        reasons.append('Uncertainty language increases routing complexity')
    if len(options) > 3:
        reasons.append("Multiple alternatives increase comparison complexity")
    if document.get("correlations"):
        score += 6
        reasons.append("Correlated exposures require additional computation")
    if any(o.get("black_swan") for o in outcomes):
        score += 4
        reasons.append("Black-swan outcomes require tail analysis")
    if any(o.get("probability_low") != o.get("probability_high") for o in outcomes):
        score += 2
        reasons.append("Probability intervals require uncertainty analysis")
    if confidence < .7:
        score += 6
        reasons.append("Low confidence escalates computation")
    tier_index = 2 if score >= 12 else 1 if score >= 5 else 0
    if force_tier is not None:
        requested = TIERS.index(force_tier)
        if requested < tier_index:
            reasons.append("Complexity automatically escalated the requested tier")
        else:
            tier_index = requested
            reasons.append("Explicit tier floor applied")
    return {"tier": TIERS[tier_index], "score": score,
            "reasons": reasons or ["Simple independent decision"]}


class VersionCache:
    """Thread-safe LRU of JSON values keyed by content, versions, and tier.

    Retained serialized payloads are bounded by max_bytes; max_entries bounds
    fixed-size digest keys and container overhead. Oversized puts are ignored.
    """
    def __init__(self, max_entries=128, max_bytes=16 * 1024 * 1024):
        for name, limit in (("max_entries", max_entries), ("max_bytes", max_bytes)):
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._values = OrderedDict()
        self._size_bytes = 0
        self._lock = Lock()

    @staticmethod
    def _key(document, upstream, tier):
        payload = json.dumps([document, upstream, tier], sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()

    def get(self, document, upstream, tier):
        key = self._key(document, upstream, tier)
        with self._lock:
            payload = self._values.get(key)
            if payload is None:
                return None
            self._values.move_to_end(key)
        return json.loads(payload)

    def put(self, document, upstream, tier, value):
        key = self._key(document, upstream, tier)
        payload = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if not self._max_entries or len(payload) > self._max_bytes:
            return
        with self._lock:
            previous = self._values.pop(key, None)
            if previous is not None:
                self._size_bytes -= len(previous)
            while self._values and (len(self._values) >= self._max_entries or
                                    self._size_bytes + len(payload) > self._max_bytes):
                _, evicted = self._values.popitem(last=False)
                self._size_bytes -= len(evicted)
            self._values[key] = payload
            self._size_bytes += len(payload)


def _tokens(text):
    return re.findall(r"\w+", text.casefold())


def _hashed_vector(text):
    vector = [0.] * 256
    for token, count in Counter(_tokens(text)).items():
        digest = hashlib.sha256(token.encode()).digest()
        vector[int.from_bytes(digest[:2], "big") % len(vector)] += count * (1 if digest[2] & 1 else -1)
    return vector


def _cosine(a, b, a_sq=None, b_sq=None):
    if len(a) != len(b):
        raise ValueError("Embedding dimensions differ")
    norm = math.sqrt((sum(x*x for x in a) if a_sq is None else a_sq) * (sum(x*x for x in b) if b_sq is None else b_sq))
    return sum(x*y for x, y in zip(a, b)) / norm if norm else 0.


class Retriever:
    """Incremental index. Only confirmed records can enter search context.

    embed(text)->numeric vector and rerank(query,text)->numeric score may be
    supplied independently; provenance labels always describe the actual path.
    """
    def __init__(self, embed=None, rerank=None):
        self._embed = embed or _hashed_vector
        self._rerank = rerank
        self._records = {}
        self._stamps = {}
        self.lock = RLock()  # held by callers that sync and search as one step
        self.embedding_method = "provider" if embed else "offline_hashed_vector_fallback"
        self.reranking_method = "provider" if rerank else "lexical_pair_fallback"

    def upsert(self, id, text, confirmed=True):
        if not isinstance(id, str) or not id or not isinstance(text, str):
            raise ValueError("id and text must be strings; id must be nonempty")
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed must be boolean")
        self._stamps.pop(id, None)
        previous = self._records.get(id)
        if previous and previous["text"] == text:  # vector already embedded and validated
            previous["confirmed"] = confirmed
            return
        vector = list(self._embed(text))
        if not vector or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
            raise ValueError("Embedding must be a finite numeric vector")
        self._records[id] = {"id": id, "text": text, "confirmed": confirmed, "vector": vector,
                             "sq": sum(x*x for x in vector)}

    def known(self, id, stamp):
        return self._stamps.get(id) == stamp

    def sync(self, id, stamp, load, confirmed=True):
        """Upsert load() only when `stamp` differs from the last one synced for id; load() may return None to unindex.
        Returns whether id is indexed. A direct upsert() invalidates the stamp."""
        if self._stamps.get(id) == stamp:
            return id in self._records
        text = load()
        if text is None:
            self._records.pop(id, None)
        else:
            self.upsert(id, text, confirmed)
        self._stamps[id] = stamp
        return text is not None

    def retain(self, ids, seen=None):
        """Drop every record outside ids (and every stamp outside seen, default ids)."""
        for identifier in [i for i in self._records if i not in ids]:
            del self._records[identifier]
        keep = ids if seen is None else seen
        for identifier in [i for i in self._stamps if i not in keep]:
            del self._stamps[identifier]

    def search(self, query, pins=None, top_k=5):
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
            raise ValueError("top_k must be a nonnegative integer")
        if not isinstance(query, str):
            raise ValueError("query must be text")
        if pins is not None and (not isinstance(pins, list) or any(not isinstance(p, str) for p in pins)):
            raise ValueError("pins must be a list of IDs")
        pins = list(dict.fromkeys(pins or []))
        for pin in pins:
            if pin not in self._records or not self._records[pin]["confirmed"]:
                raise ValueError("Pins must reference confirmed indexed content")
        if len(pins) > top_k:
            raise ValueError("top_k cannot exclude pinned content")
        if top_k == 0:
            return []
        query_vector = list(self._embed(query))
        query_sq = sum(x*x for x in query_vector)
        query_tokens = set(_tokens(query))
        pinned = {}
        pin_ids = set(pins)
        def candidates():
            for record in self._records.values():
                if not record["confirmed"]:
                    continue
                pair = (_cosine(query_vector, record["vector"], query_sq, record.get("sq")), record)
                if record['id'] in pin_ids:
                    pinned[record['id']] = pair
                yield pair
        # Shortlist by vector similarity, then rerank query/document pairs.
        order = lambda pair: (-pair[0], pair[1]['id'])
        shortlist = heapq.nsmallest(max(top_k * 4, 20), candidates(), key=order)
        selected = {record['id'] for _, record in shortlist}
        shortlist += sorted((pair for id, pair in pinned.items() if id not in selected), key=order)
        results = []
        for vector_score, record in shortlist:
            words = set(_tokens(record["text"]))
            score = self._rerank(query, record["text"]) if self._rerank else len(query_tokens & words) / max(1, len(query_tokens))
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ValueError("Reranking must return a finite numeric score")
            results.append({"id": record["id"], "text": record["text"], "confirmed": True,
                            "pinned": record["id"] in pins, "score": score, "vector_score": vector_score,
                            "embedding_method": self.embedding_method, "reranking_method": self.reranking_method})
        results.sort(key=lambda r: (0 if r["pinned"] else 1, pins.index(r["id"]) if r["pinned"] else 0,
                                    -r["score"], -r["vector_score"], r["id"]))
        return results[:top_k]
