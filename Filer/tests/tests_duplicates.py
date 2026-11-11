import csv
import os
import shutil
import tempfile
import time
import unittest

from Filing.duplicates import DuplicateFinder, DuplicateGroup, PARTIAL_CHUNK
from Filing.filestatser import FileStatsCollector
from Filing.journal import JOURNAL_DIR_NAME


def make_file(path, content=b"x", mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def tree(folder):
    found = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != JOURNAL_DIR_NAME]
        found += [os.path.relpath(os.path.join(base, f), folder).replace("\\", "/") for f in files]
    return sorted(found)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        old, mid, new = 1_500_000_000, 1_600_000_000, 1_700_000_000
        make_file(os.path.join(self.root, "a", "doc.txt"), b"same content", mid)
        make_file(os.path.join(self.root, "b", "copy_of_doc.txt"), b"same content", old)       # oldest
        make_file(os.path.join(self.root, "c", "deeper", "doc.txt"), b"same content", new)     # newest
        make_file(os.path.join(self.root, "a", "unique.txt"), b"different!!!")                 # same size, different content
        make_file(os.path.join(self.root, "a", "other.bin"), b"abc")
        make_file(os.path.join(self.root, "b", "other_copy.bin"), b"abc")
        make_file(os.path.join(self.root, "a", "empty1.txt"), b"")
        make_file(os.path.join(self.root, "b", "empty2.txt"), b"")
        self.before = tree(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def finder(self, **kw):
        return DuplicateFinder(self.root, **kw)

    @staticmethod
    def names(group):
        return sorted(os.path.basename(p) for p in group.files)


class TestFinding(Base):

    def test_finds_exact_duplicates_only(self):
        groups = self.finder().find()
        self.assertEqual(len(groups), 2)
        self.assertEqual(self.names(groups[0]), ["copy_of_doc.txt", "doc.txt", "doc.txt"])
        self.assertEqual(self.names(groups[1]), ["other.bin", "other_copy.bin"])

    def test_same_size_different_content_is_not_a_duplicate(self):
        for group in self.finder().find():
            self.assertNotIn("unique.txt", self.names(group))

    def test_empty_files_are_ignored(self):
        for group in self.finder().find():
            self.assertTrue(all("empty" not in n for n in self.names(group)))

    def test_groups_sorted_by_wasted_space(self):
        groups = self.finder().find()
        self.assertGreaterEqual(groups[0].wasted_bytes, groups[1].wasted_bytes)
        self.assertEqual(groups[0].wasted_bytes, len(b"same content") * 2)

    def test_counts(self):
        f = self.finder()
        f.find()
        self.assertEqual(f.duplicate_count, 3)
        self.assertEqual(f.wasted_bytes, len(b"same content") * 2 + 3)

    def test_no_duplicates(self):
        solo = os.path.join(self.tmp, "solo")
        make_file(os.path.join(solo, "a.txt"), b"1")
        make_file(os.path.join(solo, "b.txt"), b"22")
        f = DuplicateFinder(solo)
        self.assertEqual(f.find(), [])
        self.assertEqual(f.duplicate_count, 0)
        self.assertEqual(f.generate_duplicates_csv(), -1)

    def test_big_files_that_differ_only_in_the_middle_are_not_duplicates(self):
        size = PARTIAL_CHUNK * 4
        base = bytearray(b"A" * size)
        changed = bytearray(base)
        changed[size // 2] = ord("B")  # first and last 64 KB identical, middle differs
        big = os.path.join(self.tmp, "big")
        make_file(os.path.join(big, "one.bin"), bytes(base))
        make_file(os.path.join(big, "two.bin"), bytes(changed))
        make_file(os.path.join(big, "three.bin"), bytes(base))
        groups = DuplicateFinder(big).find()
        self.assertEqual(len(groups), 1)
        self.assertEqual(self.names(groups[0]), ["one.bin", "three.bin"])

    def test_medium_files_use_the_whole_file(self):
        size = PARTIAL_CHUNK + 1000  # bigger than one chunk, covered fully by first+last chunk
        a = bytearray(b"Z" * size)
        b = bytearray(a)
        b[PARTIAL_CHUNK - 1] = ord("Y")
        medium = os.path.join(self.tmp, "medium")
        make_file(os.path.join(medium, "a.bin"), bytes(a))
        make_file(os.path.join(medium, "b.bin"), bytes(b))
        self.assertEqual(DuplicateFinder(medium).find(), [])

    def test_skip_folders(self):
        groups = self.finder(skip_folders=["b"]).find()
        self.assertEqual(len(groups), 1)
        self.assertEqual(self.names(groups[0]), ["doc.txt", "doc.txt"])

    def test_min_size(self):
        groups = self.finder(min_size=5).find()
        self.assertEqual(len(groups), 1)
        self.assertEqual(self.names(groups[0])[0], "copy_of_doc.txt")

    def test_filter_options_are_passed_to_the_scan(self):
        groups = self.finder(name_glob="*.bin").find()
        self.assertEqual(len(groups), 1)
        self.assertEqual(self.names(groups[0]), ["other.bin", "other_copy.bin"])

    def test_can_reuse_file_stats(self):
        collector = FileStatsCollector(self.root, all_files=True)
        groups = DuplicateFinder(self.root, file_stats=collector.file_stats).find()
        self.assertEqual(len(groups), 2)

    def test_unreadable_files_are_reported_not_fatal(self):
        f = self.finder()
        victim = os.path.join(self.root, "b", "copy_of_doc.txt")
        original_stats = list(f.files)
        os.remove(victim)  # vanished between scan and hash
        f = self.finder(file_stats=original_stats)
        groups = f.find()
        self.assertEqual(len(f.errors), 1)
        self.assertEqual(f.errors[0][0], victim)
        self.assertEqual(self.names(groups[0]), ["doc.txt", "doc.txt"])

    def test_group_dataclass(self):
        group = DuplicateGroup(size=10, digest="x", files=["a", "b", "c"])
        self.assertEqual(group.wasted_bytes, 20)


class TestReporting(Base):

    def test_csv_report(self):
        f = self.finder()
        out = os.path.join(self.tmp, "dupes.csv")
        f.generate_duplicates_csv(out)
        with open(out, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 5)
        actions = [r["Action"] for r in rows]
        self.assertEqual(actions.count("Keep"), 2)
        self.assertEqual(actions.count("Duplicate"), 3)
        kept = [r["File Path"] for r in rows if r["Action"] == "Keep" and r["File Path"].endswith(".txt")]
        self.assertEqual([os.path.basename(k) for k in kept], ["copy_of_doc.txt"])  # oldest

    def test_print_summary_runs(self):
        self.finder().print_summary()


class TestKeepRules(Base):

    def kept(self, keep):
        f = self.finder()
        group = f.find()[0]
        return f._choose_keeper(group.files, keep)

    def test_oldest(self):
        self.assertTrue(self.kept("oldest").endswith("copy_of_doc.txt"))

    def test_newest(self):
        self.assertIn("deeper", self.kept("newest"))

    def test_first(self):
        self.assertEqual(self.kept("first"), min(self.finder().find()[0].files))

    def test_shortest_path(self):
        self.assertNotIn("deeper", self.kept("shortest_path"))

    def test_invalid_keep(self):
        with self.assertRaises(ValueError):
            self.finder().move_duplicates(keep="random", dry_run=True)


class TestMovingDuplicates(Base):

    def test_dry_run_changes_nothing(self):
        plan = self.finder().move_duplicates(dry_run=True)
        self.assertEqual(len(plan), 3)
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "Duplicates")))

    def test_moves_extra_copies_and_keeps_one(self):
        f = self.finder()
        moved = f.move_duplicates()
        self.assertEqual(len(moved), 3)
        remaining = tree(self.root)
        self.assertIn("b/copy_of_doc.txt", remaining)          # oldest kept
        self.assertNotIn("a/doc.txt", remaining)
        self.assertNotIn("c/deeper/doc.txt", remaining)
        self.assertIn("a/other.bin", remaining)                # a/ is older (or ties and sorts first)
        duplicates_folder = os.path.join(self.root, "Duplicates")
        self.assertEqual(len(os.listdir(duplicates_folder)), 3)
        self.assertEqual(len(remaining), len(self.before))  # nothing deleted

    def test_same_names_in_duplicates_folder_are_renamed(self):
        self.finder().move_duplicates()
        names = sorted(os.listdir(os.path.join(self.root, "Duplicates")))
        self.assertEqual([n for n in names if n.startswith("doc")], ["doc(1).txt", "doc.txt"])

    def test_undo(self):
        f = self.finder()
        f.move_duplicates()
        result = f.undo_last_move()
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "Duplicates")))

    def test_custom_destination(self):
        dest = os.path.join(self.tmp, "elsewhere")
        self.finder().move_duplicates(destination_folder=dest)
        self.assertEqual(len(os.listdir(dest)), 3)

    def test_rescan_ignores_duplicates_folder(self):
        self.finder().move_duplicates()
        self.assertEqual(self.finder().find(), [])

    def test_nothing_to_move(self):
        solo = os.path.join(self.tmp, "solo")
        make_file(os.path.join(solo, "a.txt"), b"1")
        self.assertEqual(DuplicateFinder(solo).move_duplicates(), {})

    def test_keep_newest(self):
        self.finder().move_duplicates(keep="newest")
        remaining = tree(self.root)
        self.assertIn("c/deeper/doc.txt", remaining)
        self.assertNotIn("b/copy_of_doc.txt", remaining)


class TestLinkingDuplicates(Base):

    def test_files_written_just_before_the_search_can_be_linked(self):
        """A coarse system clock may report a time slightly BEFORE the newest file timestamp."""
        f = self.finder()
        f.find()
        stamp = f._found_at + 0.012  # the files look 12 ms younger than the clock reading taken after the search
        for path in ("a/doc.txt", "b/copy_of_doc.txt", "c/deeper/doc.txt", "a/other.bin", "b/other_copy.bin"):
            os.utime(os.path.join(self.root, *path.split("/")), (stamp, stamp))
        planned = f.link_duplicates(dry_run=True)
        self.assertEqual(len(planned), 3)
        self.assertEqual(f.errors, [])

    def test_files_changed_after_the_search_are_still_refused(self):
        f = self.finder()
        f.find()
        target = os.path.join(self.root, "a", "doc.txt")
        later = f._found_at + 5
        os.utime(target, (later, later))
        planned = f.link_duplicates(dry_run=True)
        self.assertNotIn(target, planned)
        self.assertTrue(any(p == target and "changed since the search" in m for p, m in f.errors))

    def test_dry_run_changes_nothing(self):
        f = self.finder()
        planned = f.link_duplicates(dry_run=True)
        self.assertEqual(len(planned), 3)
        self.assertFalse(os.path.samefile(os.path.join(self.root, "a", "doc.txt"),
                                          os.path.join(self.root, "b", "copy_of_doc.txt")))

    def test_links_replace_copies_without_deleting_paths(self):
        f = self.finder()
        try:
            linked = f.link_duplicates()
        except OSError:
            self.skipTest("file system does not support hard links")
        if not linked:
            self.skipTest("file system does not support hard links")
        self.assertEqual(len(linked), 3)
        self.assertEqual(tree(self.root), self.before)  # every path still exists
        keeper = os.path.join(self.root, "b", "copy_of_doc.txt")
        for extra in (os.path.join(self.root, "a", "doc.txt"), os.path.join(self.root, "c", "deeper", "doc.txt")):
            self.assertTrue(os.path.samefile(keeper, extra))
            with open(extra, "rb") as fh:
                self.assertEqual(fh.read(), b"same content")
        self.assertEqual([n for n in os.listdir(os.path.join(self.root, "a")) if n.endswith(".filer_link_tmp")], [])

    def test_second_run_has_nothing_left_to_link(self):
        f = self.finder()
        if not f.link_duplicates():
            self.skipTest("file system does not support hard links")
        again = self.finder()
        again.find()
        self.assertEqual(again.link_duplicates(), {})

    def test_files_changed_since_the_search_are_skipped(self):
        f = self.finder()
        f.find()
        victim = os.path.join(self.root, "a", "doc.txt")
        make_file(victim, b"edited after the search!")
        time.sleep(0.01)
        linked = f.link_duplicates()
        self.assertNotIn(victim, linked)
        with open(victim, "rb") as fh:
            self.assertEqual(fh.read(), b"edited after the search!")
        self.assertTrue(any(path == victim for path, _ in f.errors))


if __name__ == "__main__":
    unittest.main()
