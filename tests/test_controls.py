import unittest
from concurrent.futures import ThreadPoolExecutor

from lifeev.controls import Retriever, VersionCache, route


class ControlTests(unittest.TestCase):
    def test_routing_floor_and_escalation(self):
        self.assertEqual(route({})["tier"], "small")
        self.assertEqual(route({}, "large")["tier"], "large")
        complex_doc = {"correlations": [{"rho": .5}], "options": [{"outcomes": [{"black_swan": True}]}]}
        self.assertEqual(route(complex_doc, "small", .4)["tier"], "large")
        with self.assertRaises(ValueError):
            route({}, confidence=float("nan"))

    def test_cache_content_upstream_tier_and_copy(self):
        cache = VersionCache()
        d = {"weight_version": "1", "weights": {"money": 1}}
        cache.put(d, {"parent": 1}, "small", {"result": [1]})
        got = cache.get(d, {"parent": 1}, "small")
        got["result"].append(2)
        self.assertEqual(cache.get(d, {"parent": 1}, "small"), {"result": [1]})
        self.assertIsNone(cache.get(d, {"parent": 2}, "small"))
        self.assertIsNone(cache.get(d, {"parent": 1}, "large"))
        d["weights"]["money"] = 2
        self.assertIsNone(cache.get(d, {"parent": 1}, "small"))

    def test_confirmed_pins_and_incremental_updates(self):
        r = Retriever()
        r.upsert("a", "flight cost money")
        r.upsert("b", "family time")
        r.upsert("draft", "flight", False)
        self.assertEqual(r.search("flight")[0]["id"], "a")
        self.assertEqual(r.search("flight", pins=["b"], top_k=1)[0]["id"], "b")
        with self.assertRaises(ValueError):
            r.search("flight", pins=["draft"])
        with self.assertRaises(ValueError):
            r.search("flight", pins=["a", "b"], top_k=1)
        r.upsert("a", "changed", False)
        self.assertEqual([x["id"] for x in r.search("flight")], ["b"])
        self.assertIn("fallback", r.search("time")[0]["reranking_method"])

    def test_cache_lru_reads_and_replacements(self):
        cache = VersionCache(max_entries=2)
        cache.put("a", {}, "small", [1])
        cache.put("b", {}, "small", [2])
        self.assertEqual(cache.get("a", {}, "small"), [1])
        cache.put("c", {}, "small", [3])
        self.assertIsNone(cache.get("b", {}, "small"))
        cache.put("a", {}, "small", [4])
        cache.put("d", {}, "small", [5])
        self.assertIsNone(cache.get("c", {}, "small"))
        self.assertEqual(cache.get("a", {}, "small"), [4])

    def test_cache_byte_budget_and_replacement_accounting(self):
        cache = VersionCache(max_bytes=10)
        cache.put("a", {}, "small", "1234")  # Six serialized bytes.
        cache.put("b", {}, "small", "12")
        self.assertEqual(cache._size_bytes, 10)
        cache.put("a", {}, "small", "1")
        self.assertEqual(cache._size_bytes, 7)
        cache.put("c", {}, "small", "123")
        self.assertIsNone(cache.get("b", {}, "small"))
        self.assertEqual(cache._size_bytes, 8)
        self.assertEqual(cache._size_bytes, sum(map(len, cache._values.values())))

    def test_cache_oversized_put_preserves_entries(self):
        cache = VersionCache(max_bytes=5)
        cache.put("a", {}, "small", [1])
        cache.put("b", {}, "small", "too large")
        cache.put("a", {}, "small", "too large")
        self.assertEqual(cache.get("a", {}, "small"), [1])
        self.assertIsNone(cache.get("b", {}, "small"))
        self.assertEqual(cache._size_bytes, 3)

    def test_cache_concurrent_reads_writes_stay_bounded(self):
        cache = VersionCache(max_entries=7, max_bytes=80)
        def exercise(worker):
            for i in range(200):
                key = (worker * 200 + i) % 23
                cache.put(key, {}, "small", {"value": [i]})
                result = cache.get(key, {}, "small")
                if result is not None:
                    result["value"].append("local mutation")
                with cache._lock:
                    self.assertLessEqual(len(cache._values), 7)
                    self.assertLessEqual(cache._size_bytes, 80)
                    self.assertEqual(cache._size_bytes, sum(map(len, cache._values.values())))
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(exercise, range(8)))

    def test_cache_validates_limits_and_supports_disabled_cache(self):
        for argument in ("max_entries", "max_bytes"):
            for invalid in (-1, True, 1.5):
                with self.assertRaises(ValueError):
                    VersionCache(**{argument: invalid})
            cache = VersionCache(**{argument: 0})
            cache.put({}, {}, "small", {})
            self.assertIsNone(cache.get({}, {}, "small"))

    def test_provider_callbacks_reuse_unchanged_embeddings(self):
        calls = []
        def embed(text):
            calls.append(text)
            return [1, len(text)]
        r = Retriever(embed, lambda query, text: len(text))
        r.upsert("a", "abc")
        r.upsert("a", "abc")
        self.assertEqual(calls, ["abc"])
        self.assertEqual(r.search("x")[0]["embedding_method"], "provider")


if __name__ == "__main__":
    unittest.main()
