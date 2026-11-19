"""
Tests for compare_snapshots and FileStatsCollector.compare / compare_with_csv.

Everything here uses made-up records or temporary folders created by the test itself.
No test reads or writes any file that is not created inside its own temporary folder.
"""
import csv
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

import Filing
from Filing import compare_snapshots, Change, FileStatsCollector
from Filing.compare import TAGS

OLD = 1_500_000_000


def rec(folder, name, size=10, date="2026-01-01 10:00:00", **extra):
    return {"File Name": name, "File Size (Bytes)": size, "Modification Date": date,
            "Source Folder": folder, **extra}


def tags(changes):
    return {c.path: c.tag for c in changes}


def p(*parts):
    return os.path.join(*parts)


class TestCompareBasics(unittest.TestCase):

    def test_all_tags(self):
        old = [rec("d", "same.txt"), rec("d", "changed.txt", 5), rec("d", "gone.txt")]
        new = [rec("d", "same.txt"), rec("d", "changed.txt", 6), rec("d", "fresh.txt")]
        result = tags(compare_snapshots(old, new))
        self.assertEqual(result, {p("d", "same.txt"): "Same", p("d", "changed.txt"): "Modified",
                                  p("d", "fresh.txt"): "New", p("d", "gone.txt"): "Deleted"})

    def test_only_diff_hides_same(self):
        old = [rec("d", "a"), rec("d", "b")]
        new = [rec("d", "a"), rec("d", "b", 99)]
        self.assertEqual(tags(compare_snapshots(old, new, only_diff=True)), {p("d", "b"): "Modified"})

    def test_order_is_new_files_first_then_deleted(self):
        old = [rec("d", "x"), rec("d", "y")]
        new = [rec("d", "b"), rec("d", "a"), rec("d", "y")]
        self.assertEqual([c.path for c in compare_snapshots(old, new)],
                         [p("d", "b"), p("d", "a"), p("d", "y"), p("d", "x")])

    def test_empty_inputs(self):
        self.assertEqual(compare_snapshots([], []), [])
        self.assertEqual(tags(compare_snapshots([], [rec("d", "a")])), {p("d", "a"): "New"})
        self.assertEqual(tags(compare_snapshots([rec("d", "a")], [])), {p("d", "a"): "Deleted"})

    def test_generators_are_accepted(self):
        old = (r for r in [rec("d", "a")])
        new = (r for r in [rec("d", "a")])
        self.assertEqual(tags(compare_snapshots(old, new)), {p("d", "a"): "Same"})

    def test_dict_of_records_uses_its_values(self):
        old = {"anything": rec("d", "a")}
        self.assertEqual(tags(compare_snapshots(old, [rec("d", "a")])), {p("d", "a"): "Same"})

    def test_change_object(self):
        old, new = rec("d", "a", 1), rec("d", "a", 2)
        change = compare_snapshots([old], [new])[0]
        self.assertIsInstance(change, Change)
        self.assertIs(change.old, old)
        self.assertIs(change.new, new)
        self.assertIs(change.record, new)
        self.assertEqual(change.as_row(), {**new, "Tag": "Modified"})
        self.assertEqual(change.to_dict()["tag"], "Modified")
        deleted = compare_snapshots([old], [])[0]
        self.assertIs(deleted.record, old)
        with self.assertRaises(Exception):
            change.tag = "Same"  # frozen

    def test_inputs_are_not_modified(self):
        old, new = [rec("d", "a", 1)], [rec("d", "a", 2)]
        before = ([dict(r) for r in old], [dict(r) for r in new])
        compare_snapshots(old, new)
        self.assertEqual((old, new), before)

    def test_all_tags_are_known(self):
        old = [rec("d", "a"), rec("d", "b", 1), rec("e", "m")]
        new = [rec("d", "a"), rec("d", "b", 2), rec("f", "m")]
        for change in compare_snapshots(old, new, detect_moves=True):
            self.assertIn(change.tag, TAGS)


class TestValueHandling(unittest.TestCase):

    def test_csv_text_sizes_match_integer_sizes(self):
        """A CSV row has the size as text; a live record has an int. They must compare equal."""
        self.assertEqual(tags(compare_snapshots([rec("d", "a", "2048")], [rec("d", "a", 2048)])),
                         {p("d", "a"): "Same"})

    def test_size_with_spaces(self):
        self.assertEqual(compare_snapshots([rec("d", "a", " 5 ")], [rec("d", "a", 5)])[0].tag, "Same")

    def test_modification_date_text_compared(self):
        changes = compare_snapshots([rec("d", "a", date="2026-01-01 10:00:00")],
                                    [rec("d", "a", date="2026-01-01 10:00:01")])
        self.assertEqual(changes[0].tag, "Modified")

    def test_mtime_ns_is_used_when_both_sides_have_it(self):
        old = [rec("d", "a", mtime_ns=1)]
        new = [rec("d", "a", mtime_ns=2)]  # same displayed date, different exact time
        self.assertEqual(compare_snapshots(old, new)[0].tag, "Modified")

    def test_mtime_ns_ignored_when_only_one_side_has_it(self):
        old = [rec("d", "a")]
        new = [rec("d", "a", mtime_ns=123)]
        self.assertEqual(compare_snapshots(old, new)[0].tag, "Same")

    def test_compare_only_size(self):
        old, new = [rec("d", "a", 5, "2026-01-01 10:00:00")], [rec("d", "a", 5, "2030-01-01 10:00:00")]
        self.assertEqual(compare_snapshots(old, new, compare=("size",))[0].tag, "Same")
        self.assertEqual(compare_snapshots(old, new, compare="size")[0].tag, "Same")
        self.assertEqual(compare_snapshots(old, new)[0].tag, "Modified")

    def test_compare_only_mtime(self):
        old, new = [rec("d", "a", 5)], [rec("d", "a", 6)]
        self.assertEqual(compare_snapshots(old, new, compare=("mtime",))[0].tag, "Same")

    def test_missing_mtime_data_is_an_error_when_mtime_is_compared(self):
        old = [{"File Name": "a", "Source Folder": "d", "File Size (Bytes)": 1}]
        with self.assertRaisesRegex(ValueError, "Modification Date"):
            compare_snapshots(old, old)
        # but fine when only sizes are compared
        self.assertEqual(compare_snapshots(old, old, compare=("size",))[0].tag, "Same")

    def test_bad_compare_option(self):
        for bad in ((), ("size", "owner"), ["colour"], ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                compare_snapshots([], [], compare=bad)

    def test_invalid_sizes(self):
        for bad in ("abc", "", None, True, -1, "1.5", "-3"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                compare_snapshots([rec("d", "a", bad)], [rec("d", "a", 1)])

    def test_invalid_records(self):
        good = [rec("d", "a")]
        for bad in ([{"File Name": "a"}], [{"Source Folder": "d"}], [None], ["text"], [{}],
                    [rec("d", "")], [rec(5, "a")], [rec("d", None)]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                compare_snapshots(bad, good)
            with self.assertRaises(ValueError, msg=repr(bad)):
                compare_snapshots(good, bad)

    def test_error_names_the_side(self):
        with self.assertRaisesRegex(ValueError, "old record #2"):
            compare_snapshots([rec("d", "a"), {"File Name": "b"}], [])
        with self.assertRaisesRegex(ValueError, "new record #1"):
            compare_snapshots([], [{"File Name": "b"}])

    def test_same_path_twice_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "old snapshot lists .* more than once"):
            compare_snapshots([rec("d", "a"), rec("d", "a", 2)], [])
        with self.assertRaisesRegex(ValueError, "new snapshot lists"):
            compare_snapshots([], [rec("d", "a"), rec("d", "a")])

    @unittest.skipUnless(os.name == "nt", "case-insensitive paths are a Windows rule")
    def test_paths_ignore_case_and_separator_style_on_windows(self):
        old = [rec("D:\\Photos", "A.jpg")]
        new = [rec("d:/photos", "a.JPG")]
        self.assertEqual(compare_snapshots(old, new)[0].tag, "Same")

    def test_redundant_path_parts_are_normalised(self):
        old = [rec(p("d", "x", ".."), "a")]
        new = [rec("d", "a")]
        self.assertEqual(compare_snapshots(old, new)[0].tag, "Same")


class TestMoveDetection(unittest.TestCase):

    def test_moves_are_off_by_default(self):
        old, new = [rec("a", "f.txt")], [rec("b", "f.txt")]
        self.assertEqual(sorted(c.tag for c in compare_snapshots(old, new)), ["Deleted", "New"])

    def test_simple_move(self):
        old, new = [rec("a", "f.txt")], [rec("b", "f.txt")]
        changes = compare_snapshots(old, new, detect_moves=True)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0].tag, "Moved")
        self.assertEqual(changes[0].path, p("b", "f.txt"))  # new location
        self.assertEqual(changes[0].old["Source Folder"], "a")
        self.assertEqual(changes[0].new["Source Folder"], "b")

    def test_rename_is_not_a_move_without_a_hash(self):
        old, new = [rec("a", "one.txt")], [rec("a", "two.txt")]
        self.assertEqual(sorted(c.tag for c in compare_snapshots(old, new, detect_moves=True)), ["Deleted", "New"])

    def test_rename_with_equal_hash_is_a_move(self):
        old, new = [rec("a", "one.txt", Hash="abc")], [rec("a", "two.txt", Hash="abc")]
        changes = compare_snapshots(old, new, detect_moves=True)
        self.assertEqual([c.tag for c in changes], ["Moved"])

    def test_different_hash_is_not_a_move(self):
        old, new = [rec("a", "f", Hash="abc")], [rec("b", "f", Hash="xyz")]
        self.assertEqual(sorted(c.tag for c in compare_snapshots(old, new, detect_moves=True)), ["Deleted", "New"])

    def test_different_size_or_time_is_not_a_move(self):
        for new_record in (rec("b", "f", 99), rec("b", "f", 10, "2030-01-01 00:00:00")):
            changes = compare_snapshots([rec("a", "f")], [new_record], detect_moves=True)
            self.assertEqual(sorted(c.tag for c in changes), ["Deleted", "New"])

    def test_each_file_is_paired_only_once(self):
        old = [rec("a", "f"), rec("b", "f")]
        new = [rec("c", "f")]
        changes = compare_snapshots(old, new, detect_moves=True)
        self.assertEqual(sorted(c.tag for c in changes), ["Deleted", "Moved"])
        old = [rec("a", "f")]
        new = [rec("c", "f"), rec("d", "f")]
        self.assertEqual(sorted(c.tag for c in compare_snapshots(old, new, detect_moves=True)), ["Moved", "New"])

    def test_unchanged_files_are_never_moves(self):
        old = [rec("a", "f"), rec("b", "f")]
        changes = compare_snapshots(old, old, detect_moves=True)
        self.assertEqual({c.tag for c in changes}, {"Same"})

    def test_moves_with_only_diff(self):
        old = [rec("a", "f"), rec("a", "keep")]
        new = [rec("b", "f"), rec("a", "keep")]
        changes = compare_snapshots(old, new, only_diff=True, detect_moves=True)
        self.assertEqual([c.tag for c in changes], ["Moved"])

    def test_text_and_int_sizes_pair_up(self):
        old, new = [rec("a", "f", "10")], [rec("b", "f", 10)]
        self.assertEqual([c.tag for c in compare_snapshots(old, new, detect_moves=True)], ["Moved"])


class TestNoDiskAccess(unittest.TestCase):

    def test_compare_snapshots_touches_no_file(self):
        """The function must work on data alone: make every file-system call fail loudly."""
        old = [rec("/nonexistent/a", "f", 1), rec("/nonexistent/a", "g", 2)]
        new = [rec("/nonexistent/b", "f", 1), rec("/nonexistent/a", "g", 3)]

        def boom(*args, **kwargs):
            raise AssertionError("file system touched")

        with mock.patch("os.stat", boom), mock.patch("os.scandir", boom), mock.patch("os.listdir", boom), \
                mock.patch("builtins.open", boom), mock.patch("os.path.exists", boom), \
                mock.patch("os.path.getsize", boom), mock.patch("os.path.getmtime", boom), \
                mock.patch("os.path.abspath", boom), mock.patch("os.path.realpath", boom):
            changes = compare_snapshots(old, new, detect_moves=True)
        self.assertEqual(sorted(c.tag for c in changes), ["Modified", "Moved"])


class TestRandomised(unittest.TestCase):
    """Compare against an obviously-correct slow reference on many random inputs (fixed seed)."""

    @staticmethod
    def reference(old, new):
        old_map = {os.path.join(r["Source Folder"], r["File Name"]): r for r in old}
        new_map = {os.path.join(r["Source Folder"], r["File Name"]): r for r in new}
        out = {}
        for path in set(old_map) | set(new_map):
            if path not in new_map:
                out[path] = "Deleted"
            elif path not in old_map:
                out[path] = "New"
            else:
                a, b = old_map[path], new_map[path]
                same = int(a["File Size (Bytes)"]) == int(b["File Size (Bytes)"]) and \
                    a["Modification Date"] == b["Modification Date"]
                out[path] = "Same" if same else "Modified"
        return out

    @staticmethod
    def random_snapshot(rng, names):
        chosen = rng.sample(names, rng.randint(0, len(names)))
        return [rec(name[0], name[1:], rng.choice([1, 2, 3]),
                    rng.choice(["2026-01-01 00:00:00", "2026-01-02 00:00:00"])) for name in chosen]

    def test_matches_reference(self):
        rng = random.Random(1234)
        names = [f"{folder}{n}" for folder in "abc" for n in range(8)]
        for _ in range(300):
            old, new = self.random_snapshot(rng, names), self.random_snapshot(rng, names)
            # keep text sizes in the mix
            for r in old:
                if rng.random() < 0.5:
                    r["File Size (Bytes)"] = str(r["File Size (Bytes)"])
            got = tags(compare_snapshots(old, new))
            self.assertEqual(got, self.reference(old, new))

    def test_swapping_sides_swaps_new_and_deleted(self):
        rng = random.Random(99)
        names = [f"{folder}{n}" for folder in "ab" for n in range(10)]
        swap = {"New": "Deleted", "Deleted": "New", "Same": "Same", "Modified": "Modified"}
        for _ in range(100):
            old, new = self.random_snapshot(rng, names), self.random_snapshot(rng, names)
            forward = tags(compare_snapshots(old, new))
            backward = tags(compare_snapshots(new, old))
            self.assertEqual({k: swap[v] for k, v in forward.items()}, backward)

    def test_comparing_with_itself_is_all_same(self):
        rng = random.Random(7)
        names = [f"a{n}" for n in range(20)]
        snap = self.random_snapshot(rng, names)
        self.assertTrue(all(c.tag == "Same" for c in compare_snapshots(snap, snap)))
        self.assertEqual(compare_snapshots(snap, snap, only_diff=True), [])

    def test_move_detection_never_loses_or_invents_files(self):
        rng = random.Random(5)
        names = [f"{folder}{n}" for folder in "abc" for n in range(6)]
        for _ in range(200):
            old, new = self.random_snapshot(rng, names), self.random_snapshot(rng, names)
            plain = compare_snapshots(old, new)
            with_moves = compare_snapshots(old, new, detect_moves=True)
            counts = lambda cs, tag: sum(1 for c in cs if c.tag == tag)
            moved = counts(with_moves, "Moved")
            self.assertEqual(counts(with_moves, "New") + moved, counts(plain, "New"))
            self.assertEqual(counts(with_moves, "Deleted") + moved, counts(plain, "Deleted"))
            self.assertEqual(counts(with_moves, "Same"), counts(plain, "Same"))
            self.assertEqual(counts(with_moves, "Modified"), counts(plain, "Modified"))


class TestCollectorCompare(unittest.TestCase):
    """FileStatsCollector.compare / compare_with_csv, on a temporary folder built by the test."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filer_compare_test_")
        self.root = os.path.join(self.tmp, "data")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.out)
        self.write("a.txt", b"aaa")
        self.write("sub/b.txt", b"bbbb")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, relative, content, mtime=OLD):
        path = os.path.join(self.root, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        os.utime(path, (mtime, mtime))
        return path

    def scan(self):
        return FileStatsCollector(self.root, all_files=True, verbose=False)

    def test_compare_with_another_collector(self):
        before = self.scan()
        self.write("a.txt", b"changed!")
        self.write("c.txt", b"new")
        os.remove(os.path.join(self.root, "sub", "b.txt"))
        after = self.scan()
        result = {os.path.basename(c.path): c.tag for c in after.compare(before)}
        self.assertEqual(result, {"a.txt": "Modified", "c.txt": "New", "b.txt": "Deleted"})

    def test_compare_with_list_of_records_and_only_diff(self):
        before = self.scan().file_stats
        after = self.scan()
        self.assertEqual(after.compare(before, only_diff=True), [])
        self.assertEqual({c.tag for c in after.compare(before)}, {"Same"})

    def test_compare_passes_options_on(self):
        before = self.scan()
        os.rename(os.path.join(self.root, "a.txt"), os.path.join(self.root, "sub", "a.txt"))
        after = self.scan()
        self.assertEqual(sorted(c.tag for c in after.compare(before, only_diff=True)), ["Deleted", "New"])
        self.assertEqual([c.tag for c in after.compare(before, only_diff=True, detect_moves=True)], ["Moved"])

    def test_compare_does_not_touch_the_disk(self):
        before, after = self.scan(), self.scan()
        with mock.patch("os.stat", side_effect=AssertionError("stat")), \
                mock.patch("os.scandir", side_effect=AssertionError("scandir")), \
                mock.patch("builtins.open", side_effect=AssertionError("open")):
            self.assertEqual(len(after.compare(before)), 2)

    def test_compare_with_csv(self):
        csv_path = os.path.join(self.out, "report.csv")
        first = self.scan()
        first.generate_file_stats_csv(csv_path)
        self.write("a.txt", b"changed!")
        self.write("new.txt", b"n")
        second = self.scan()
        result = {os.path.basename(c.path): c.tag for c in second.compare_with_csv(csv_path)}
        self.assertEqual(result, {"a.txt": "Modified", "new.txt": "New", "b.txt": "Same"})
        only = second.compare_with_csv(csv_path, only_diff=True)
        self.assertEqual(sorted(os.path.basename(c.path) for c in only), ["a.txt", "new.txt"])

    def test_compare_with_csv_writes_nothing(self):
        csv_path = os.path.join(self.out, "report.csv")
        c = self.scan()
        c.generate_file_stats_csv(csv_path)
        with open(csv_path, "rb") as f:
            before = f.read()
        listing = sorted(os.listdir(self.out))
        c.compare_with_csv(csv_path)
        with open(csv_path, "rb") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(sorted(os.listdir(self.out)), listing)

    def test_compare_with_missing_csv(self):
        with self.assertRaises(FileNotFoundError):
            self.scan().compare_with_csv(os.path.join(self.out, "nope.csv"))

    def test_compare_with_csv_that_is_not_a_report(self):
        bad = os.path.join(self.out, "bad.csv")
        with open(bad, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([["x", "y"], ["1", "2"]])
        with self.assertRaises(ValueError):
            self.scan().compare_with_csv(bad)

    def test_compare_with_csv_with_duplicate_rows(self):
        csv_path = os.path.join(self.out, "dup.csv")
        c = self.scan()
        c.generate_file_stats_csv(csv_path)
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            lines = f.read().splitlines()
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(lines + [lines[1]]) + "\n")
        with self.assertRaisesRegex(ValueError, "more than once"):
            c.compare_with_csv(csv_path)

    def test_difference_csv_still_works(self):
        """generate_difference_file_csv now runs on compare_snapshots; its output must be unchanged."""
        csv_path = os.path.join(self.out, "report.csv")
        diff_path = os.path.join(self.out, "diff.csv")
        self.scan().generate_file_stats_csv(csv_path)
        self.write("a.txt", b"changed!")
        self.write("new.txt", b"n")
        os.remove(os.path.join(self.root, "sub", "b.txt"))
        after = self.scan()
        after.generate_difference_file_csv(csv_path, output_path=diff_path)
        created = [n for n in os.listdir(self.root) if n.endswith(".csv")]
        self.assertEqual(len(created), 1)  # written next to the data (existing behaviour)
        with open(os.path.join(self.root, created[0]), newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r["File Name"]: r["Tag"] for r in rows},
                         {"a.txt": "Modified", "new.txt": "New", "b.txt": "Deleted"})
        self.assertEqual(list(rows[0].keys()), ['File Name', 'File Type', 'File Size (Bytes)',
                                               'File Size (Human Readable)', 'Creation Date',
                                               'Modification Date', 'Source Folder', 'Tag'])

    def test_difference_csv_only_diff(self):
        csv_path = os.path.join(self.out, "report.csv")
        self.scan().generate_file_stats_csv(csv_path)
        self.write("a.txt", b"changed!")
        after = self.scan()
        after.generate_difference_file_csv(csv_path, only_diff=True)  # rewrites the report in place
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r["File Name"], r["Tag"]) for r in rows], [("a.txt", "Modified")])


class TestPackageSurface(unittest.TestCase):

    def test_names_are_exported(self):
        for name in ("compare_snapshots", "Change"):
            self.assertIn(name, Filing.__all__)
            self.assertTrue(hasattr(Filing, name))


if __name__ == "__main__":
    unittest.main()
