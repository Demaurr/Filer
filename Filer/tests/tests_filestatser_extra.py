"""
Extra tests for FileStatsCollector. They build their own temporary folders, so they do not
depend on the working directory or on the sample files in tests/have_files.

Run from the repository root:
    PYTHONPATH=Filer python -m unittest discover -s Filer/tests -p "tests_*.py"
"""
import csv
import os
import re
import shutil
import tempfile
import time
import unittest
from collections import Counter
from types import SimpleNamespace

from Filing.filestatser import FileStatsCollector

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def make_file(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


class TempTreeTestCase(unittest.TestCase):
    """Creates a temp tree:
        root/a.txt, root/b.MP3
        root/sub/c.txt
        root/.git/config.txt
        root/.github/workflow.txt
        root/node_modules/pkg/index.txt
        root/deep/node_modules/lib.txt
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        for rel, content in [
            ("a.txt", b"aaaa"),
            ("b.MP3", b"bb"),
            ("sub/c.txt", b"c"),
            (".git/config.txt", b"g"),
            (".github/workflow.txt", b"gh"),
            ("node_modules/pkg/index.txt", b"n"),
            ("deep/node_modules/lib.txt", b"l"),
        ]:
            make_file(os.path.join(self.root, *rel.split("/")), content)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, collector):
        return sorted(s["File Name"] for s in collector.file_stats)

    def rel_paths(self, collector):
        return sorted(
            os.path.relpath(os.path.join(s["Source Folder"], s["File Name"]), self.root).replace("\\", "/")
            for s in collector.file_stats
        )


class TestGathering(TempTreeTestCase):

    def test_all_files_collects_everything(self):
        c = FileStatsCollector(self.root, all_files=True)
        self.assertEqual(c.total_files, 7)

    def test_media_filter_is_case_insensitive(self):
        c = FileStatsCollector(self.root, media_extensions=[".mp3"])
        self.assertEqual(self.names(c), ["b.MP3"])
        c = FileStatsCollector(self.root, media_extensions=[".TXT"], skip_folders=[".git", ".github", "node_modules"])
        self.assertEqual(self.names(c), ["a.txt", "c.txt"])

    def test_no_matching_extension_gives_no_stats(self):
        c = FileStatsCollector(self.root, media_extensions=[".zzz"])
        self.assertEqual(c.file_stats, [])
        self.assertEqual(c.total_files, 0)

    def test_stat_record_format(self):
        c = FileStatsCollector(self.root, media_extensions=[".mp3"])
        stat = c.file_stats[0]
        self.assertEqual(
            list(stat.keys()),
            ["File Name", "File Type", "File Size (Bytes)", "File Size (Human Readable)",
             "Creation Date", "Modification Date", "Source Folder"],
        )
        self.assertEqual(stat["File Type"], ".mp3")  # lower-cased
        self.assertEqual(stat["File Size (Bytes)"], 2)
        self.assertEqual(stat["File Size (Human Readable)"], "2.00 B")
        self.assertRegex(stat["Creation Date"], DATE_RE)
        self.assertRegex(stat["Modification Date"], DATE_RE)
        self.assertEqual(stat["Source Folder"], self.root)

    def test_missing_root_raises(self):
        with self.assertRaises(FileNotFoundError):
            FileStatsCollector(os.path.join(self.tmp, "does_not_exist"))

    def test_empty_folder(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        c = FileStatsCollector(empty, all_files=True)
        self.assertEqual(c.file_stats, [])
        self.assertEqual(c.generate_file_stats_csv(), -1)
        self.assertEqual(c.generate_file_stats_html(), -1)

    def test_allow_empty_creates_header_only_csv(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        c = FileStatsCollector(empty, all_files=True)
        out = os.path.join(self.tmp, "empty_stats.csv")
        self.assertEqual(c.generate_file_stats_csv(out, allow_empty=True), 0)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows, [["File Name", "File Type", "File Size (Bytes)", "File Size (Human Readable)",
                                 "Creation Date", "Modification Date", "Source Folder"]])

    def test_allow_empty_without_csv_path_uses_default_name_in_root(self):
        empty = os.path.join(self.tmp, "empty2")
        os.makedirs(empty)
        c = FileStatsCollector(empty, all_files=True)
        self.assertEqual(c.generate_file_stats_csv(allow_empty=True), 0)
        created = [n for n in os.listdir(empty) if n.startswith("empty2_files_stats_") and n.endswith(".csv")]
        self.assertEqual(len(created), 1)

    def test_empty_without_allow_empty_creates_nothing(self):
        empty = os.path.join(self.tmp, "empty3")
        os.makedirs(empty)
        out = os.path.join(self.tmp, "nothing.csv")
        self.assertEqual(FileStatsCollector(empty, all_files=True).generate_file_stats_csv(out), -1)
        self.assertFalse(os.path.exists(out))

    def test_allow_empty_with_data_writes_normal_csv(self):
        c = FileStatsCollector(self.root, media_extensions=[".mp3"])
        out = os.path.join(self.tmp, "data.csv")
        c.generate_file_stats_csv(out, allow_empty=True)
        with open(out, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 1)

    def test_parallel_and_serial_give_same_result(self):
        many = os.path.join(self.tmp, "many")
        for i in range(FileStatsCollector._PARALLEL_MIN_FOLDERS + 8):
            make_file(os.path.join(many, f"d{i}", f"f{i}.txt"), b"x" * i)
        parallel = FileStatsCollector(many, all_files=True)

        class Serial(FileStatsCollector):
            _PARALLEL_MIN_FOLDERS = 10 ** 9

        serial = Serial(many, all_files=True)
        key = lambda s: (s["Source Folder"], s["File Name"])
        self.assertEqual(parallel.total_files, FileStatsCollector._PARALLEL_MIN_FOLDERS + 8)
        self.assertEqual(sorted(parallel.file_stats, key=key), sorted(serial.file_stats, key=key))


class TestSkipFolders(TempTreeTestCase):

    def test_skip_by_exact_name(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=[".git"])
        paths = self.rel_paths(c)
        self.assertNotIn(".git/config.txt", paths)
        # '.github' only contains '.git' as text; it must NOT be skipped
        self.assertIn(".github/workflow.txt", paths)

    def test_skip_name_applies_at_any_depth(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=["node_modules"])
        paths = self.rel_paths(c)
        self.assertNotIn("node_modules/pkg/index.txt", paths)
        self.assertNotIn("deep/node_modules/lib.txt", paths)
        self.assertEqual(len(paths), 5)

    def test_skip_does_not_match_partial_names(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=["su", "node", "modules", "gi", "git."])
        self.assertEqual(c.total_files, 7)

    def test_skip_by_full_path(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=[os.path.join(self.root, "sub")])
        self.assertNotIn("sub/c.txt", self.rel_paths(c))
        self.assertEqual(c.total_files, 6)

    def test_skip_path_covers_subfolders_but_not_same_named_elsewhere(self):
        skip = os.path.join(self.root, "node_modules")
        c = FileStatsCollector(self.root, all_files=True, skip_folders=[skip])
        paths = self.rel_paths(c)
        self.assertNotIn("node_modules/pkg/index.txt", paths)
        self.assertIn("deep/node_modules/lib.txt", paths)

    def test_skip_path_with_trailing_separator(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=[os.path.join(self.root, "sub") + os.sep])
        self.assertNotIn("sub/c.txt", self.rel_paths(c))

    @unittest.skipUnless(os.name == "nt", "case-insensitive matching is Windows only")
    def test_skip_is_case_insensitive_on_windows(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=["SUB", ".GIT"])
        paths = self.rel_paths(c)
        self.assertNotIn("sub/c.txt", paths)
        self.assertNotIn(".git/config.txt", paths)
        self.assertIn(".github/workflow.txt", paths)

    def test_empty_skip_entries_are_ignored(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=["", "  "])
        self.assertEqual(c.total_files, 7)

    def test_root_is_not_skipped_by_its_own_name(self):
        c = FileStatsCollector(self.root, all_files=True, skip_folders=["root"])
        self.assertEqual(c.total_files, 7)

    def test_walk_prunes_skipped_folders(self):
        folders = list(FileStatsCollector.walk_with_scandir(self.root, ["node_modules", ".git"]))
        names = {os.path.basename(f) for f in folders}
        self.assertNotIn("node_modules", names)
        self.assertNotIn(".git", names)
        self.assertNotIn("pkg", names)  # child of a skipped folder is never visited
        self.assertIn("root", names)
        self.assertIn(".github", names)

    def test_walk_without_skips_lists_every_folder(self):
        folders = list(FileStatsCollector.walk_with_scandir(self.root))
        self.assertEqual(len(folders), 8)  # root, sub, .git, .github, node_modules, pkg, deep, deep/node_modules

    def test_split_skip_folders(self):
        names, paths = FileStatsCollector._split_skip_folders(["x", os.path.join("a", "b"), "", None or "y"])
        self.assertEqual(names, {os.path.normcase("x"), os.path.normcase("y")})
        self.assertEqual(paths, {os.path.normcase(os.path.abspath(os.path.join("a", "b")))})
        self.assertEqual(FileStatsCollector._split_skip_folders(None), (set(), set()))


class TestHelpers(unittest.TestCase):

    def test_format_size(self):
        f = FileStatsCollector.format_size
        self.assertEqual(f(0), "0.00 B")
        self.assertEqual(f(1023), "1023.00 B")
        self.assertEqual(f(1024), "1.00 KB")
        self.assertEqual(f(1024 ** 2 * 1.5), "1.50 MB")
        self.assertEqual(f(1024 ** 3), "1.00 GB")
        self.assertEqual(f(1024 ** 4), "1.00 TB")
        self.assertEqual(f(1024 ** 5), "1.00 PB")

    def test_format_timestamp(self):
        out = FileStatsCollector.format_timestamp(0)
        self.assertRegex(out, DATE_RE)
        self.assertEqual(out, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(0)))
        # fractional seconds are truncated, not rounded
        self.assertEqual(FileStatsCollector.format_timestamp(0.999), out)

    def test_creation_timestamp_prefers_birthtime(self):
        st = SimpleNamespace(st_birthtime=100.0, st_ctime=200.0)
        self.assertEqual(FileStatsCollector.get_creation_timestamp(st), 100.0)

    def test_creation_timestamp_falls_back_to_ctime(self):
        st = SimpleNamespace(st_ctime=200.0)  # no st_birthtime (older Python on Windows, Linux)
        self.assertEqual(FileStatsCollector.get_creation_timestamp(st), 200.0)

    def test_sort_by_key(self):
        rows = [{"n": "b", "s": 2}, {"n": "A", "s": 3}, {"n": "c", "s": 1}]
        sort = FileStatsCollector.sort_by_key
        self.assertEqual([r["s"] for r in sort(rows, "s")], [1, 2, 3])
        self.assertEqual([r["s"] for r in sort(rows, "s", reverse=True)], [3, 2, 1])
        self.assertEqual([r["n"] for r in sort(rows, "n")], ["A", "b", "c"])
        self.assertEqual([r["n"] for r in sort(rows, "n", case_sense=False)], ["A", "b", "c"])
        self.assertEqual(sort(rows, "missing"), rows)  # unknown key leaves data untouched


class TestFileStatsAndReports(TempTreeTestCase):

    def setUp(self):
        super().setUp()
        self.c = FileStatsCollector(self.root, all_files=True, skip_folders=[".git", ".github", "node_modules"])

    def test_get_file_stats_matches_gathered_record(self):
        path = os.path.join(self.root, "a.txt")
        single = FileStatsCollector.get_file_stats(path)[0]
        gathered = next(s for s in self.c.file_stats if s["File Name"] == "a.txt")
        self.assertEqual(single, gathered)  # same keys, same creation/modification formatting

    def test_get_file_stats_missing_file_returns_none(self):
        self.assertIsNone(FileStatsCollector.get_file_stats(os.path.join(self.root, "nope.txt")))

    def test_file_type_distribution(self):
        self.assertEqual(self.c.get_file_typedist(), Counter({".txt": 2, ".mp3": 1}))

    def test_folders_info(self):
        info = self.c.get_folders_info()
        self.assertEqual(info[self.root], 2)
        self.assertEqual(info[os.path.join(self.root, "sub")], 1)

    def test_folder_size_distribution(self):
        sizes = self.c.get_folder_size_distribution()
        self.assertEqual(sizes[self.root], 6)  # a.txt (4) + b.MP3 (2)
        self.assertEqual(sizes[os.path.join(self.root, "sub")], 1)

    def test_summary_stats(self):
        total, folders, size, avg, common = self.c.get_summary_stats()
        self.assertEqual((total, folders, size), (3, 2, 7))
        self.assertAlmostEqual(avg, 7 / 3)
        self.assertEqual(common, ".txt")

    def test_csv_is_written_with_expected_rows(self):
        out = os.path.join(self.tmp, "out.csv")
        self.c.generate_file_stats_csv(out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), self.c.total_files)
        self.assertEqual(list(rows[0].keys()), [
            "File Name", "File Type", "File Size (Bytes)", "File Size (Human Readable)",
            "Creation Date", "Modification Date", "Source Folder"])
        self.assertEqual([r["File Name"] for r in rows], sorted(r["File Name"] for r in rows))

    def test_csv_sort_by_size(self):
        out = os.path.join(self.tmp, "by_size.csv")
        self.c.generate_file_stats_csv(out, sort_by="File Size (Bytes)")
        with open(out, newline="", encoding="utf-8") as f:
            sizes = [int(r["File Size (Bytes)"]) for r in csv.DictReader(f)]
        self.assertEqual(sizes, sorted(sizes))

    def test_html_reports_are_written(self):
        stats_html = os.path.join(self.tmp, "stats.html")
        summary_html = os.path.join(self.tmp, "summary.html")
        self.c.generate_file_stats_html(stats_html)
        self.c.generate_summary_html(summary_html)
        with open(stats_html, encoding="utf-8") as f:
            self.assertIn("a.txt", f.read())
        with open(summary_html, encoding="utf-8") as f:
            self.assertIn("Summary Statistics", f.read())

    def test_difference_csv_tags(self):
        baseline = os.path.join(self.tmp, "baseline.csv")
        self.c.generate_file_stats_csv(baseline)

        os.remove(os.path.join(self.root, "sub", "c.txt"))                 # Deleted
        make_file(os.path.join(self.root, "new.txt"), b"new")              # New
        make_file(os.path.join(self.root, "a.txt"), b"much longer now")    # Modified (size changed)
        # b.MP3 untouched                                                  # Same

        fresh = FileStatsCollector(self.root, all_files=True, skip_folders=[".git", ".github", "node_modules"])
        fresh.generate_difference_file_csv(baseline)
        with open(baseline, newline="", encoding="utf-8") as f:
            tags = {r["File Name"]: r["Tag"] for r in csv.DictReader(f)}
        self.assertEqual(tags, {"a.txt": "Modified", "b.MP3": "Same", "c.txt": "Deleted", "new.txt": "New"})

    def test_difference_csv_only_diff(self):
        baseline = os.path.join(self.tmp, "baseline.csv")
        self.c.generate_file_stats_csv(baseline)
        make_file(os.path.join(self.root, "new.txt"), b"new")
        fresh = FileStatsCollector(self.root, all_files=True, skip_folders=[".git", ".github", "node_modules"])
        fresh.generate_difference_file_csv(baseline, only_diff=True)
        with open(baseline, newline="", encoding="utf-8") as f:
            tags = {r["File Name"]: r["Tag"] for r in csv.DictReader(f)}
        self.assertEqual(tags, {"new.txt": "New"})

    def test_difference_csv_without_previous_file_creates_it(self):
        target = os.path.join(self.tmp, "first_run.csv")
        self.c.generate_difference_file_csv(target)
        self.assertTrue(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
