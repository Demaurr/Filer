"""
Tests for the supported cache hook of DuplicateFinder: cache=..., HashStore, finder.cache,
finder.cache_stats and finder.files.

All files are created by the tests inside their own temporary folder.
"""
import os
import shutil
import tempfile
import threading
import unittest
from unittest import mock

import Filing
from Filing import duplicates, DuplicateFinder, HashCache, HashStore, FileStatsCollector
from Filing.duplicates import PARTIAL_CHUNK
from Filing.journal import JOURNAL_DIR_NAME

OLD = 1_500_000_000


def make_file(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    os.utime(path, (mtime, mtime))
    return path


class FakeStore:
    """A minimal HashStore that remembers everything in a dict and records every call."""

    def __init__(self):
        self.data = {}
        self.lookups = []
        self.stores = []
        self.pruned = []
        self.saves = 0
        self.clears = 0
        self._lock = threading.Lock()

    def lookup(self, path, size, mtime_ns, kind):
        with self._lock:
            self.lookups.append((path, kind))
            return self.data.get((path, size, mtime_ns, kind))

    def store(self, path, size, mtime_ns, kind, digest):
        with self._lock:
            self.stores.append((path, kind))
            self.data[(path, size, mtime_ns, kind)] = digest

    def prune(self, seen_paths):
        self.pruned.append(sorted(seen_paths))

    def save(self):
        self.saves += 1
        return True

    def clear(self):
        self.clears += 1
        self.data.clear()


def hashing_calls(call):
    """Run call() and return (result, partial hashes computed, full hashes computed)."""
    with mock.patch.object(duplicates, "_partial_hash", wraps=duplicates._partial_hash) as partial, \
            mock.patch.object(duplicates, "_full_hash", wraps=duplicates._full_hash) as full:
        result = call()
    return result, partial.call_count, full.call_count


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filer_cachehook_test_")
        self.root = os.path.join(self.tmp, "data")
        big = bytes(range(256)) * ((2 * PARTIAL_CHUNK) // 256 + 8)  # bigger than 128 KB: reaches the full-hash stage
        make_file(os.path.join(self.root, "a", "small.txt"), b"small content")
        make_file(os.path.join(self.root, "b", "small_copy.txt"), b"small content")
        make_file(os.path.join(self.root, "a", "big.bin"), big)
        make_file(os.path.join(self.root, "b", "big_copy.bin"), big)
        make_file(os.path.join(self.root, "c", "unique.txt"), b"only one of me")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def finder(self, **kw):
        kw.setdefault("verbose", False)
        return DuplicateFinder(self.root, **kw)

    @staticmethod
    def summary(groups):
        return [(g.size, sorted(os.path.basename(p) for p in g.files)) for g in groups]


class TestProtocol(Base):

    def test_hashcache_and_fake_are_hashstores(self):
        self.assertIsInstance(HashCache(os.path.join(self.tmp, "c.json")), HashStore)
        self.assertIsInstance(FakeStore(), HashStore)

    def test_objects_that_are_not_stores_are_rejected(self):
        for bad in (object(), "cache", 5, {}, [], lambda: None):
            with self.assertRaises(TypeError, msg=repr(bad)):
                self.finder(cache=bad)

    def test_a_store_missing_one_method_is_rejected(self):
        for missing in ("lookup", "store", "prune", "save", "clear"):
            class Partial(FakeStore):
                pass
            setattr(Partial, missing, None)
            with self.assertRaises(TypeError, msg=missing):
                self.finder(cache=Partial())

    def test_exports(self):
        self.assertIs(Filing.HashStore, HashStore)
        self.assertIn("HashStore", Filing.__all__)


class TestCustomStore(Base):

    def test_results_equal_with_and_without_store(self):
        with_store = self.finder(cache=FakeStore()).find()
        plain = self.finder(use_cache=False).find()
        self.assertEqual(self.summary(with_store), self.summary(plain))
        self.assertEqual(len(with_store), 2)

    def test_store_is_filled_then_reused_by_a_new_finder(self):
        store = FakeStore()
        _, partial, full = hashing_calls(lambda: self.finder(cache=store).find())
        self.assertGreater(partial, 0)
        self.assertGreater(full, 0)
        self.assertGreater(len(store.stores), 0)
        again = self.finder(cache=store)
        groups, partial, full = hashing_calls(again.find)
        self.assertEqual((partial, full), (0, 0))  # nothing read again
        self.assertEqual(len(groups), 2)
        self.assertGreater(again.cache_hits, 0)

    def test_changed_file_is_hashed_again(self):
        store = FakeStore()
        self.finder(cache=store).find()
        make_file(os.path.join(self.root, "a", "small.txt"), b"different now", mtime=OLD + 50)
        groups, partial, _ = hashing_calls(self.finder(cache=store).find)
        self.assertGreater(partial, 0)
        self.assertEqual(len(groups), 1)  # only the big pair is still a duplicate

    def test_finder_exposes_the_store(self):
        store = FakeStore()
        self.assertIs(self.finder(cache=store).cache, store)

    def test_store_wins_over_use_cache_false(self):
        store = FakeStore()
        f = self.finder(cache=store, use_cache=False)
        f.find()
        self.assertIs(f.cache, store)
        self.assertGreater(len(store.lookups), 0)

    def test_no_files_are_written_for_a_custom_store(self):
        self.finder(cache=FakeStore()).find()
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))

    def test_prune_and_save_are_called_once_with_every_scanned_path(self):
        store = FakeStore()
        f = self.finder(cache=store)
        f.find()
        self.assertEqual(store.saves, 1)
        self.assertEqual(len(store.pruned), 1)
        scanned = sorted(os.path.join(s["Source Folder"], s["File Name"]) for s in f.files)
        self.assertEqual(store.pruned[0], scanned)

    def test_a_shared_store_serves_two_finders(self):
        other_root = os.path.join(self.tmp, "other")
        make_file(os.path.join(other_root, "x", "one.txt"), b"shared")
        make_file(os.path.join(other_root, "y", "two.txt"), b"shared")
        store = FakeStore()
        self.finder(cache=store).find()
        entries = len(store.data)
        groups = DuplicateFinder(other_root, cache=store, verbose=False).find()
        self.assertEqual(len(groups), 1)
        self.assertGreater(len(store.data), entries)

    def test_a_real_hashcache_can_be_passed_explicitly(self):
        path = os.path.join(self.tmp, "elsewhere", "my_cache.json")
        cache = HashCache(path)
        f = self.finder(cache=cache)
        f.find()
        self.assertTrue(os.path.exists(path))
        self.assertIs(f.cache, cache)
        again = self.finder(cache=HashCache(path))
        _, partial, full = hashing_calls(again.find)
        self.assertEqual((partial, full), (0, 0))

    def test_lookups_and_counters_agree_under_threads(self):
        for n in range(40):
            make_file(os.path.join(self.root, "many", f"f{n}.txt"), b"same content for all")
        store = FakeStore()
        f = self.finder(cache=store, max_workers=8)
        f.find()
        stats = f.cache_stats
        self.assertEqual(stats["hits"] + stats["misses"], len(store.lookups))
        first_lookups = len(store.lookups)
        second = self.finder(cache=store, max_workers=8)
        second.find()
        self.assertEqual(second.cache_stats["misses"], 0)
        self.assertEqual(second.cache_stats["hits"], len(store.lookups) - first_lookups)
        self.assertGreater(second.cache_stats["hits"], 0)


class TestMisbehavingStore(Base):
    """A custom store is outside code. Whatever it does, the search must stay correct."""

    def expected(self):
        return self.summary(self.finder(use_cache=False).find())

    def test_lookup_that_raises(self):
        class Bad(FakeStore):
            def lookup(self, *args):
                raise RuntimeError("database offline")
        f = self.finder(cache=Bad())
        self.assertEqual(self.summary(f.find()), self.expected())
        self.assertTrue(any("lookup failed" in message and "database offline" in message for _, message in f.errors))

    def test_store_that_raises(self):
        class Bad(FakeStore):
            def store(self, *args):
                raise RuntimeError("disk full")
        f = self.finder(cache=Bad())
        self.assertEqual(self.summary(f.find()), self.expected())
        self.assertTrue(any("store failed" in message for _, message in f.errors))

    def test_save_that_raises(self):
        class Bad(FakeStore):
            def save(self):
                raise OSError("read-only")
        f = self.finder(cache=Bad())
        self.assertEqual(self.summary(f.find()), self.expected())
        self.assertTrue(any("could not save hash cache" in message for _, message in f.errors))

    def test_prune_that_raises_with_a_non_oserror(self):
        class Bad(FakeStore):
            def prune(self, seen):
                raise KeyError("boom")
        f = self.finder(cache=Bad())
        self.assertEqual(self.summary(f.find()), self.expected())
        self.assertTrue(any("could not save hash cache" in message for _, message in f.errors))

    def test_garbage_digests_are_treated_as_a_miss(self):
        for garbage in (123, "", ["abc"], b"bytes", object(), 0, False):
            class Garbage(FakeStore):
                def lookup(self, *args):
                    return garbage
            f = self.finder(cache=Garbage())
            self.assertEqual(self.summary(f.find()), self.expected(), msg=repr(garbage))
            self.assertEqual(f.cache_stats["hits"], 0, msg=repr(garbage))

    def test_a_lying_store_cannot_cause_a_wrong_link(self):
        """If a store claims two different files are identical, linking still compares the bytes."""
        make_file(os.path.join(self.root, "a", "left.dat"), b"AAAA1111")
        make_file(os.path.join(self.root, "b", "right.dat"), b"BBBB2222")  # same size, different content

        class Liar(FakeStore):
            def lookup(self, path, size, mtime_ns, kind):
                return "same-digest-for-everything"

        f = self.finder(cache=Liar())
        groups = f.find()
        listed = [os.path.basename(p) for g in groups for p in g.files]
        self.assertIn("left.dat", listed)  # fooled: trusted digests are trusted for finding
        planned = f.link_duplicates(dry_run=True)
        names = {os.path.basename(k) for k in planned}
        self.assertNotIn("left.dat", names)
        self.assertNotIn("right.dat", names)
        self.assertTrue(any("differs" in message for _, message in f.errors))
        for name in ("left.dat", "right.dat"):  # nothing was changed on disk
            self.assertTrue(os.path.exists(os.path.join(self.root, "a" if name == "left.dat" else "b", name)))


class TestClearCache(Base):

    def test_caller_store_is_not_cleared_without_force(self):
        store = FakeStore()
        f = self.finder(cache=store)
        f.find()
        before = dict(store.data)
        with self.assertRaisesRegex(ValueError, "force=True"):
            f.clear_cache()
        self.assertEqual(store.clears, 0)
        self.assertEqual(store.data, before)

    def test_force_clears_the_caller_store(self):
        store = FakeStore()
        f = self.finder(cache=store)
        f.find()
        f.clear_cache(force=True)
        self.assertEqual(store.clears, 1)
        self.assertEqual(store.data, {})

    def test_the_finder_never_clears_a_caller_store_on_its_own(self):
        store = FakeStore()
        f = self.finder(cache=store)
        f.find()
        f.find()
        f.move_duplicates(dry_run=True)
        self.assertEqual(store.clears, 0)

    def test_built_in_cache_is_still_cleared_without_force(self):
        f = self.finder()
        f.find()
        path = f.cache_stats["path"]
        self.assertTrue(os.path.exists(path))
        f.clear_cache()
        self.assertFalse(os.path.exists(path))

    def test_clear_when_caching_is_off_removes_a_leftover_file(self):
        self.finder().find()
        path = HashCache.default_path(self.root)
        self.assertTrue(os.path.exists(path))
        self.finder(use_cache=False).clear_cache()
        self.assertFalse(os.path.exists(path))


class TestInspection(Base):

    def test_default_cache_is_a_hashcache(self):
        f = self.finder()
        self.assertIsInstance(f.cache, HashCache)
        self.assertIsInstance(f.cache, HashStore)

    def test_cache_is_none_when_off(self):
        f = self.finder(use_cache=False)
        self.assertIsNone(f.cache)
        stats = f.cache_stats
        self.assertEqual((stats["enabled"], stats["hits"], stats["misses"], stats["entries"]), (False, 0, 0, 0))
        f.find()
        self.assertEqual((f.cache_stats["hits"], f.cache_stats["misses"]), (0, 0))

    def test_cache_stats_before_and_after(self):
        f = self.finder()
        self.assertEqual((f.cache_stats["hits"], f.cache_stats["misses"]), (0, 0))
        f.find()
        stats = f.cache_stats
        self.assertTrue(stats["enabled"])
        self.assertEqual(stats["hits"], 0)
        self.assertGreater(stats["misses"], 0)
        self.assertGreater(stats["entries"], 0)
        self.assertTrue(stats["path"].endswith("hash_cache.json"))
        g = self.finder()
        g.find()
        self.assertGreater(g.cache_stats["hits"], 0)
        self.assertEqual(g.cache_stats["misses"], 0)
        self.assertEqual(g.cache_hits, g.cache_stats["hits"])  # the old attribute still works

    def test_counters_restart_on_every_find(self):
        f = self.finder()
        f.find()
        misses = f.cache_stats["misses"]
        f.find()
        self.assertEqual(f.cache_stats["misses"], 0)
        self.assertGreater(misses, 0)
        self.assertGreater(f.cache_stats["hits"], 0)

    def test_store_without_len_reports_unknown_entries(self):
        f = self.finder(cache=FakeStore())
        f.find()
        self.assertIsNone(f.cache_stats["entries"])
        self.assertIsNone(f.cache_stats["path"])

    def test_files_is_a_read_only_tuple_of_the_scanned_records(self):
        f = self.finder()
        self.assertIsInstance(f.files, tuple)
        self.assertEqual(len(f.files), 5)
        names = sorted(r["File Name"] for r in f.files)
        self.assertEqual(names, ["big.bin", "big_copy.bin", "small.txt", "small_copy.txt", "unique.txt"])
        with self.assertRaises(AttributeError):
            f.files = ()
        self.assertFalse(hasattr(f.files, "append"))

    def test_files_changing_the_tuple_does_not_change_the_finder(self):
        f = self.finder()
        snapshot = list(f.files)
        _ = f.files[1:]
        self.assertEqual(list(f.files), snapshot)

    def test_files_reflects_file_stats_passed_in(self):
        collector = FileStatsCollector(self.root, all_files=True, verbose=False)
        subset = collector.file_stats[:2]
        f = DuplicateFinder(self.root, file_stats=subset, verbose=False)
        self.assertEqual(list(f.files), subset)


if __name__ == "__main__":
    unittest.main()
