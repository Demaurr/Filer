import os
import shutil
import tempfile
import unittest
from datetime import date, datetime

from Filing.filestatser import FileStatsCollector
from Filing.filters import FileFilter, parse_size, parse_time


def make_file(path, size=1, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestParsing(unittest.TestCase):

    def test_parse_size(self):
        self.assertIsNone(parse_size(None))
        self.assertEqual(parse_size(500), 500)
        self.assertEqual(parse_size("500"), 500)
        self.assertEqual(parse_size("10KB"), 10 * 1024)
        self.assertEqual(parse_size("1.5 MB"), int(1.5 * 1024 ** 2))
        self.assertEqual(parse_size("2gb"), 2 * 1024 ** 3)
        self.assertEqual(parse_size("3M"), 3 * 1024 ** 2)

    def test_parse_size_errors(self):
        for bad in ("abc", "10XB", "-5", -1, "", "1..2MB"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_size(bad)
        with self.assertRaises(TypeError):
            parse_size(True)

    def test_parse_time(self):
        self.assertIsNone(parse_time(None))
        self.assertEqual(parse_time(1000), 1000.0)
        moment = datetime(2026, 11, 9, 14, 30)
        self.assertEqual(parse_time(moment), moment.timestamp())
        self.assertEqual(parse_time("2026-11-09 14:30:00"), moment.timestamp())
        self.assertEqual(parse_time(date(2026, 11, 9)), datetime(2026, 11, 9).timestamp())
        self.assertEqual(parse_time("2026-11-09"), datetime(2026, 11, 9).timestamp())

    def test_parse_time_errors(self):
        with self.assertRaises(ValueError):
            parse_time("not a date")


class TestFileFilter(unittest.TestCase):

    def test_inactive_when_empty(self):
        self.assertFalse(FileFilter().active)
        self.assertTrue(FileFilter(min_size=1).active)

    def test_size_limits_are_inclusive(self):
        f = FileFilter(min_size=10, max_size=20)
        self.assertEqual([f.stat_ok(s, 0) for s in (9, 10, 15, 20, 21)], [False, True, True, True, False])

    def test_time_range_after_inclusive_before_exclusive(self):
        f = FileFilter(modified_after=100, modified_before=200)
        self.assertEqual([f.stat_ok(1, t) for t in (99, 100, 150, 199, 200)], [False, True, True, True, False])

    def test_glob_is_case_insensitive_and_accepts_lists(self):
        f = FileFilter(name_glob=["*.JPG", "img_*"])
        self.assertTrue(f.name_ok("photo.jpg"))
        self.assertTrue(f.name_ok("IMG_001.png"))
        self.assertFalse(f.name_ok("notes.txt"))

    def test_regex_is_searched_and_case_sensitive(self):
        f = FileFilter(name_regex=r"^report_\d{4}")
        self.assertTrue(f.name_ok("report_2026.pdf"))
        self.assertFalse(f.name_ok("Report_2026.pdf"))
        self.assertTrue(FileFilter(name_regex=r"(?i)^report").name_ok("REPORT.txt"))

    def test_glob_and_regex_must_both_match(self):
        f = FileFilter(name_glob="*.txt", name_regex="^a")
        self.assertTrue(f.name_ok("a.txt"))
        self.assertFalse(f.name_ok("b.txt"))
        self.assertFalse(f.name_ok("a.csv"))

    def test_invalid_ranges(self):
        with self.assertRaises(ValueError):
            FileFilter(min_size=10, max_size=5)
        with self.assertRaises(ValueError):
            FileFilter(modified_after=200, modified_before=100)


class TestFiltersInCollector(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        self.old = datetime(2020, 1, 1).timestamp()
        self.new = datetime(2026, 6, 1).timestamp()
        make_file(os.path.join(self.root, "small_old.txt"), 10, self.old)
        make_file(os.path.join(self.root, "big_new.txt"), 5000, self.new)
        make_file(os.path.join(self.root, "sub", "photo.JPG"), 2000, self.new)
        make_file(os.path.join(self.root, "sub", "photo_old.jpg"), 3000, self.old)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, **kw):
        c = FileStatsCollector(self.root, all_files=True, **kw)
        return sorted(s["File Name"] for s in c.file_stats)

    def test_no_filters_keeps_everything(self):
        self.assertEqual(len(self.names()), 4)

    def test_min_and_max_size(self):
        self.assertEqual(self.names(min_size=2000), ["big_new.txt", "photo.JPG", "photo_old.jpg"])
        self.assertEqual(self.names(max_size="2KB"), ["photo.JPG", "small_old.txt"])
        self.assertEqual(self.names(min_size="1KB", max_size="4KB"), ["photo.JPG", "photo_old.jpg"])

    def test_modified_range(self):
        self.assertEqual(self.names(modified_after="2025-01-01"), ["big_new.txt", "photo.JPG"])
        self.assertEqual(self.names(modified_before="2025-01-01"), ["photo_old.jpg", "small_old.txt"])
        self.assertEqual(self.names(modified_after="2019-01-01", modified_before="2021-01-01"),
                         ["photo_old.jpg", "small_old.txt"])

    def test_name_filters(self):
        self.assertEqual(self.names(name_glob="*.jpg"), ["photo.JPG", "photo_old.jpg"])
        self.assertEqual(self.names(name_regex=r"_old\."), ["photo_old.jpg", "small_old.txt"])

    def test_filters_combine(self):
        self.assertEqual(self.names(name_glob="*.jpg", modified_after="2025-01-01"), ["photo.JPG"])

    def test_filters_work_with_media_extension_filter(self):
        c = FileStatsCollector(self.root, media_extensions=[".jpg"], min_size=2500)
        self.assertEqual([s["File Name"] for s in c.file_stats], ["photo_old.jpg"])

    def test_filtered_files_are_not_in_reports(self):
        c = FileStatsCollector(self.root, all_files=True, min_size=2000)
        self.assertEqual(c.total_files, 3)
        self.assertEqual(sum(c.get_folder_size_distribution().values()), 10000)

    def test_invalid_filter_raises_on_creation(self):
        with self.assertRaises(ValueError):
            FileStatsCollector(self.root, min_size="lots")

    def test_filters_with_process_pool(self):
        many = os.path.join(self.tmp, "many")
        for i in range(FileStatsCollector._PARALLEL_MIN_FOLDERS + 5):
            make_file(os.path.join(many, f"d{i}", f"f{i}.txt"), i)
        c = FileStatsCollector(many, all_files=True, min_size=10)
        self.assertEqual(c.total_files, FileStatsCollector._PARALLEL_MIN_FOLDERS + 5 - 10)


if __name__ == "__main__":
    unittest.main()
