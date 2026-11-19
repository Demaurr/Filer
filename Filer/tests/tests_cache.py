import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from Filing import duplicates
from Filing.cache import HashCache, CACHE_FILE_NAME, CACHE_VERSION, RACY_WINDOW_NS
from Filing.duplicates import DuplicateFinder, PARTIAL_CHUNK
from Filing.journal import JOURNAL_DIR_NAME, list_journals, find_latest_journal

OLD = 1_500_000_000  # long ago, so entries are trusted (not "racy")


def make_file(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def hashing_calls(finder_call):
    """Run finder_call() and return how many partial and full hashes were really computed."""
    with mock.patch.object(duplicates, "_partial_hash", wraps=duplicates._partial_hash) as partial, \
            mock.patch.object(duplicates, "_full_hash", wraps=duplicates._full_hash) as full:
        result = finder_call()
    return result, partial.call_count, full.call_count


class TestHashCacheClass(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "cache", CACHE_FILE_NAME)
        self.file = make_file(os.path.join(self.tmp, "f.bin"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_path(self):
        self.assertEqual(HashCache.default_path(self.tmp), os.path.join(os.path.abspath(self.tmp), JOURNAL_DIR_NAME, CACHE_FILE_NAME))
        self.assertEqual(HashCache.default_path(self.tmp, "elsewhere"), os.path.join("elsewhere", CACHE_FILE_NAME))

    def test_store_and_lookup(self):
        cache = HashCache(self.path)
        self.assertIsNone(cache.lookup(self.file, 5, 100, "p"))
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "abc")
        self.assertEqual(cache.lookup(self.file, 5, OLD * 10 ** 9, "p"), "abc")
        self.assertIsNone(cache.lookup(self.file, 5, OLD * 10 ** 9, "f"))  # other kind not stored

    def test_partial_and_full_share_an_entry(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "P")
        cache.store(self.file, 5, OLD * 10 ** 9, "f", "F")
        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.lookup(self.file, 5, OLD * 10 ** 9, "p"), "P")
        self.assertEqual(cache.lookup(self.file, 5, OLD * 10 ** 9, "f"), "F")

    def test_changed_size_or_mtime_is_a_miss_and_replaces_entry(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "old")
        self.assertIsNone(cache.lookup(self.file, 6, OLD * 10 ** 9, "p"))
        self.assertIsNone(cache.lookup(self.file, 5, OLD * 10 ** 9 + 1, "p"))
        cache.store(self.file, 5, OLD * 10 ** 9 + 1, "p", "new")
        self.assertEqual(len(cache), 1)
        self.assertIsNone(cache.lookup(self.file, 5, OLD * 10 ** 9, "p"))
        self.assertEqual(cache.lookup(self.file, 5, OLD * 10 ** 9 + 1, "p"), "new")

    def test_recent_files_are_not_stored(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, time.time_ns(), "p", "abc")
        cache.store(self.file, 5, time.time_ns() - RACY_WINDOW_NS // 2, "p", "abc")
        self.assertEqual(len(cache), 0)
        cache.store(self.file, 5, time.time_ns() - RACY_WINDOW_NS * 2, "p", "abc")
        self.assertEqual(len(cache), 1)

    def test_hit_and_miss_counters(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "abc")
        cache.lookup(self.file, 5, OLD * 10 ** 9, "p")
        cache.lookup(self.file, 5, 1, "p")
        self.assertEqual((cache.hits, cache.misses), (1, 1))

    def test_save_and_load_roundtrip(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "abc")
        self.assertTrue(cache.save())
        self.assertFalse(cache.save())  # nothing changed since
        self.assertFalse(os.path.exists(self.path + ".tmp"))
        again = HashCache(self.path)
        self.assertEqual(again.lookup(self.file, 5, OLD * 10 ** 9, "p"), "abc")

    def test_nothing_is_written_when_empty(self):
        cache = HashCache(self.path)
        self.assertFalse(cache.save())
        self.assertFalse(os.path.exists(self.path))

    def test_bad_cache_files_are_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{not json", "[]", '{"version": 999, "entries": {}}', '{"version": %d, "entries": []}' % CACHE_VERSION,
                        '{"version": %d, "entries": {"a": "junk", "b": {"s": "x", "m": 1}}}' % CACHE_VERSION, ""):
            with open(self.path, "w", encoding="utf-8") as f:
                f.write(content)
            self.assertEqual(len(HashCache(self.path)), 0, msg=content)

    def test_prune_removes_only_missing_unseen_files(self):
        other = make_file(os.path.join(self.tmp, "other.bin"))
        gone = os.path.join(self.tmp, "gone.bin")
        cache = HashCache(self.path)
        for p in (self.file, other, gone):
            cache.store(p, 1, OLD * 10 ** 9, "p", "h")
        cache.prune([self.file])  # other: unseen but exists -> kept; gone: unseen and missing -> dropped
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.lookup(gone, 1, OLD * 10 ** 9, "p"))

    def test_clear_deletes_file(self):
        cache = HashCache(self.path)
        cache.store(self.file, 5, OLD * 10 ** 9, "p", "abc")
        cache.save()
        cache.clear()
        self.assertEqual(len(cache), 0)
        self.assertFalse(os.path.exists(self.path))


class CacheBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        self.big = bytes(range(256)) * (PARTIAL_CHUNK * 3 // 256)  # larger than 2 chunks -> needs a full hash
        make_file(os.path.join(self.root, "a", "small.txt"), b"small content")
        make_file(os.path.join(self.root, "b", "small_copy.txt"), b"small content")
        make_file(os.path.join(self.root, "a", "big.bin"), self.big)
        make_file(os.path.join(self.root, "b", "big_copy.bin"), self.big)
        make_file(os.path.join(self.root, "c", "unique.txt"), b"nothing alike")
        self.cache_file = os.path.join(self.root, JOURNAL_DIR_NAME, CACHE_FILE_NAME)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def finder(self, **kw):
        return DuplicateFinder(self.root, **kw)

    @staticmethod
    def summary(groups):
        return sorted((g.size, tuple(os.path.basename(p) for p in g.files)) for g in groups)


class TestDuplicateFinderCache(CacheBase):

    def test_first_run_hashes_and_writes_cache(self):
        groups, partial, full = hashing_calls(self.finder().find)
        self.assertEqual(len(groups), 2)
        self.assertGreater(partial, 0)
        self.assertGreater(full, 0)
        self.assertTrue(os.path.exists(self.cache_file))
        with open(self.cache_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], CACHE_VERSION)

    def test_second_run_reads_no_file_contents(self):
        first, _, _ = hashing_calls(self.finder().find)
        f = self.finder()
        second, partial, full = hashing_calls(f.find)
        self.assertEqual((partial, full), (0, 0))
        self.assertEqual(self.summary(second), self.summary(first))
        self.assertGreater(f.cache_hits, 0)

    def test_results_are_identical_with_and_without_cache(self):
        with_cache = self.finder().find()
        again = self.finder().find()
        without = self.finder(use_cache=False).find()
        self.assertEqual(self.summary(with_cache), self.summary(without))
        self.assertEqual(self.summary(again), self.summary(without))

    def test_use_cache_false_writes_nothing(self):
        self.finder(use_cache=False).find()
        self.assertFalse(os.path.exists(self.cache_file))

    def test_modified_file_is_hashed_again(self):
        self.finder().find()
        victim = os.path.join(self.root, "b", "small_copy.txt")
        make_file(victim, b"small CONTENT", mtime=OLD + 100)  # same size, new time, different content
        groups, partial, _ = hashing_calls(self.finder().find)
        self.assertEqual(partial, 1)  # only the modified file; the other small file came from the cache
        names = [os.path.basename(p) for g in groups for p in g.files]
        self.assertNotIn("small.txt", names)
        self.assertIn("big.bin", names)

    def test_new_file_is_hashed_and_deleted_file_is_pruned(self):
        self.finder().find()
        os.remove(os.path.join(self.root, "a", "big.bin"))
        make_file(os.path.join(self.root, "c", "another_small.txt"), b"small content")
        groups, partial, full = hashing_calls(self.finder().find)
        self.assertEqual(partial, 1)   # just the new file
        self.assertEqual(full, 0)      # the big pair is gone
        self.assertEqual(len([p for g in groups for p in g.files if "small" in p]), 3)
        with open(self.cache_file, encoding="utf-8") as f:
            keys = json.load(f)["entries"].keys()
        self.assertFalse(any("big.bin" in k and "big_copy" not in k for k in keys))

    def test_corrupt_cache_file_does_not_break_the_run(self):
        os.makedirs(os.path.dirname(self.cache_file))
        with open(self.cache_file, "w") as f:
            f.write("{garbage")
        groups = self.finder().find()
        self.assertEqual(len(groups), 2)
        with open(self.cache_file, encoding="utf-8") as f:
            json.load(f)  # rewritten as valid json

    def test_failing_cache_save_is_reported_not_fatal(self):
        f = self.finder()
        with mock.patch("Filing.cache.os.replace", side_effect=OSError("disk full")):
            groups = f.find()
        self.assertEqual(len(groups), 2)
        self.assertTrue(any("hash cache" in message for _, message in f.errors))

    def test_files_modified_just_now_are_never_cached(self):
        fresh = os.path.join(self.tmp, "fresh")
        make_file(os.path.join(fresh, "a.txt"), b"same", mtime=None)
        make_file(os.path.join(fresh, "b.txt"), b"same", mtime=None)
        DuplicateFinder(fresh).find()
        self.assertFalse(os.path.exists(os.path.join(fresh, JOURNAL_DIR_NAME, CACHE_FILE_NAME)))

    def test_custom_cache_dir(self):
        custom = os.path.join(self.tmp, "my_cache")
        self.finder(cache_dir=custom).find()
        self.assertTrue(os.path.exists(os.path.join(custom, CACHE_FILE_NAME)))
        self.assertFalse(os.path.exists(self.cache_file))

    def test_clear_cache(self):
        f = self.finder()
        f.find()
        f.clear_cache()
        self.assertFalse(os.path.exists(self.cache_file))
        _, partial, _ = hashing_calls(self.finder().find)
        self.assertGreater(partial, 0)

    def test_cache_file_is_not_treated_as_a_scanned_file_or_a_journal(self):
        self.finder().find()
        self.assertEqual(list_journals(os.path.join(self.root, JOURNAL_DIR_NAME)), [])
        self.assertIsNone(find_latest_journal(os.path.join(self.root, JOURNAL_DIR_NAME)))
        names = [os.path.basename(p) for g in self.finder().find() for p in g.files]
        self.assertNotIn(CACHE_FILE_NAME, names)

    def test_move_and_undo_still_work_with_a_cache(self):
        f = self.finder()
        moved = f.move_duplicates()
        self.assertEqual(len(moved), 2)
        result = f.undo_last_move()  # must pick the move journal, not the cache file
        self.assertEqual(len(result["restored"]), 2)

    def test_hash_failure_on_vanished_file_with_cache(self):
        f = self.finder()
        os.remove(os.path.join(self.root, "b", "small_copy.txt"))
        groups = f.find()
        self.assertEqual(len(f.errors), 1)
        self.assertEqual(len(groups), 1)  # the big pair remains


class TestLinkVerify(CacheBase):

    def test_stale_cache_cannot_cause_a_wrong_link(self):
        """A file edited without changing size or modification time fools the cache; verify catches it."""
        self.finder().find()  # fills the cache
        victim = os.path.join(self.root, "b", "small_copy.txt")
        make_file(victim, b"SMALL CONTENT", mtime=OLD)  # same size, same mtime, different content
        f = self.finder()
        groups = f.find()
        self.assertIn("small_copy.txt", [os.path.basename(p) for g in groups for p in g.files])  # cache is fooled
        try:
            linked = f.link_duplicates()
        except OSError:
            self.skipTest("file system does not support hard links")
        self.assertNotIn(victim, linked)
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"SMALL CONTENT")  # untouched
        self.assertTrue(any(p == victim and "differs" in m for p, m in f.errors))

    def test_verify_can_be_turned_off(self):
        f = self.finder(use_cache=False)
        planned = f.link_duplicates(dry_run=True, verify=False)
        self.assertEqual(len(planned), 2)


if __name__ == "__main__":
    unittest.main()
