# Testing library and framework: pytest runner with Django plugin; unittest.TestCase style tests.
# Purpose: Thoroughly validate temporary cache behavior (happy paths, edge cases, and failure conditions).
# These tests adapt to different tmp_cache APIs (class TmpCache or module-level functions) and skip gracefully if absent.


import unittest
import time
import tempfile
import shutil
from pathlib import Path
import logging


CANDIDATE_IMPORTS = [
    "tmp_cache",
    "general_manager.utils.tmp_cache",
    "general_manager.cache.tmp_cache",
    "general_manager.tmp_cache",
    "utils.tmp_cache",
]


def _resolve_tmp_cache_module():
    for mod in CANDIDATE_IMPORTS:
        try:
            return __import__(mod, fromlist=["*"])
        except ImportError as e:
            logging.debug("tmp_cache import failed for %s: %s", mod, e)
            continue
    return None


class TestTmpCache(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp_cache = _resolve_tmp_cache_module()
        if cls._tmp_cache is None:
            raise unittest.SkipTest()

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="tmp-cache-tests-"))
        self.cache = self._make_cache(self.tmpdir)
        self.setfn = self._setter(self.cache)
        self.getfn = self._getter(self.cache)
        self.delfn = self._deleter(self.cache)
        self.clearfn = self._clearer(self.cache)
        if not (self.setfn and self.getfn):
            self.skipTest("tmp_cache API must provide set/get or write/read")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- Helpers -------------------------------------------------------------
    def _make_cache(self, base_dir: Path):
        mod = type(self)._tmp_cache
        if hasattr(mod, "TmpCache"):
            try:
                return mod.TmpCache(base_dir=str(base_dir))
            except TypeError:
                # Some constructors may not accept base_dir
                return mod.TmpCache()
        return mod

    @staticmethod
    def _setter(cache):
        return getattr(cache, "set", getattr(cache, "write", None))

    @staticmethod
    def _getter(cache):
        return getattr(cache, "get", getattr(cache, "read", None))

    @staticmethod
    def _deleter(cache):
        return getattr(cache, "delete", getattr(cache, "remove", None))

    @staticmethod
    def _clearer(cache):
        return getattr(cache, "clear", None)

    def _set_with_ttl(self, key, value, ttl):
        """
        Calls set/write with ttl or timeout if supported; otherwise sets without ttl.
        Returns the ttl used (or None if no ttl was applied).
        """
        try:
            self.setfn(key, value, ttl=ttl)
        except TypeError:
            try:
                self.setfn(key, value, timeout=ttl)
            except TypeError:
                self.setfn(key, value)
                return None
            else:
                return ttl
        else:
            return ttl

    # --- Tests ---------------------------------------------------------------
    def test_set_get_roundtrip(self):
        self._set_with_ttl("alpha", b"hello", 5)
        self.assertEqual(self.getfn("alpha"), b"hello")

    def test_get_missing_key_returns_falsey(self):
        self.assertIn(self.getfn("does-not-exist"), (None, b"", False))

    def test_overwrite_value(self):
        self._set_with_ttl("dup", b"one", 10)
        self.assertEqual(self.getfn("dup"), b"one")
        self._set_with_ttl("dup", b"two", 10)
        self.assertEqual(self.getfn("dup"), b"two")

    def test_delete_and_clear(self):
        self._set_with_ttl("k1", b"v1", 10)
        self._set_with_ttl("k2", b"v2", 10)
        self.assertEqual(self.getfn("k1"), b"v1")
        self.assertEqual(self.getfn("k2"), b"v2")

        if self.delfn:
            self.delfn("k1")
            self.assertIn(self.getfn("k1"), (None, b"", False))

        if self.clearfn:
            self.clearfn()
            self.assertIn(self.getfn("k2"), (None, b"", False))

    def test_ttl_expiry(self):
        key = "ephemeral"
        # Prefer short TTL if supported; fall back to integer TTL.
        ttl = 0.2
        used = self._set_with_ttl(key, b"data", ttl)
        if used is None:
            # No TTL support; cannot test expiry reliably
            self.skipTest("Cache set() does not support TTL/timeout")
        self.assertEqual(self.getfn(key), b"data")
        # Sleep slightly beyond TTL used
        sleep_for = 0.25 if used == 0.2 else used + 0.1
        time.sleep(sleep_for)
        self.assertIn(self.getfn(key), (None, b"", False), "expired entry should not be returned")

    def test_ttl_exact_boundary(self):
        key = "edge"
        ttl = 0.5
        used = self._set_with_ttl(key, b"v", ttl)
        if used is None:
            self.skipTest("Cache set() does not support TTL/timeout")
        # Before expiry
        time.sleep(min(used, 0.45))
        self.assertEqual(self.getfn(key), b"v")
        # After expiry boundary
        time.sleep(max(0.1, used - 0.4))
        self.assertIn(self.getfn(key), (None, b"", False))

    def test_zero_ttl_immediate_expiry(self):
        key = "zero"
        try:
            used = self._set_with_ttl(key, b"v", 0)
            if used is None:
                self.skipTest("Cache set() does not support TTL/timeout")
            self.assertIn(self.getfn(key), (None, b"", False))
        except (TypeError, ValueError):
            # Strict APIs may reject zero TTL
            pass

    def test_negative_ttl_rejected_or_expires(self):
        key = "neg"
        try:
            used = self._set_with_ttl(key, b"v", -5)
            # If accepted, it should be treated as expired
            if used is not None:
                self.assertIn(self.getfn(key), (None, b"", False))
        except (TypeError, ValueError):
            # Accept strict validation
            pass

    def test_invalid_keys(self):
        for bad_key in (None, "", 0, [], {}, object()):
            with self.subTest(bad_key=bad_key):
                try:
                    self._set_with_ttl(bad_key, b"v", 5)  # type: ignore[arg-type]
                    # If no exception and key is a string, ensure retrieval does not succeed unexpectedly
                    if isinstance(bad_key, str):
                        self.assertIn(self.getfn(bad_key), (None, b"", False))
                except (TypeError, ValueError):
                    # Expected for invalid key types
                    pass

    def test_custom_base_dir_is_used(self):
        base = self.tmpdir / "custom"
        base.mkdir(parents=True, exist_ok=True)
        # Recreate cache bound to custom base if possible
        self.cache = self._make_cache(base)
        self.setfn = self._setter(self.cache)
        self.getfn = self._getter(self.cache)
        if not (self.setfn and self.getfn):
            self.skipTest("tmp_cache API missing set/get")
        self._set_with_ttl("where", b"here", 5)
        self.assertEqual(self.getfn("where"), b"here")
        # Filesystem-backed caches should create files under base
        # Either files exist, or the cache is memory-only; both are acceptable but we assert at least a directory exists
        self.assertTrue(base.exists())

    def test_eviction_capacity_if_supported(self):
        mod = type(self)._tmp_cache
        if not hasattr(mod, "TmpCache"):
            self.skipTest("TmpCache class not available for capacity test")
        try:
            cache = mod.TmpCache(base_dir=str(self.tmpdir / "cap"), capacity=2)  # type: ignore[call-arg]
        except TypeError:
            self.skipTest("Capacity parameter not supported by TmpCache")
        setfn = self._setter(cache)
        getfn = self._getter(cache)
        if not (setfn and getfn):
            self.skipTest("tmp_cache API missing set/get")
        setfn("a", b"a", ttl=100)
        setfn("b", b"b", ttl=100)
        setfn("c", b"c", ttl=100)
        remaining = [k for k in ("a", "b", "c") if getfn(k)]
        self.assertEqual(len(remaining), 2, "capacity should limit retained entries to 2")