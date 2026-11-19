"""
Tests for the pure helpers exported at the top of the package: FileFilter.matches,
Organizer.relative_path, parse_size, parse_time and format_size.

Only made-up records and temporary folders created by the tests are used.
"""
import os
import random
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import Filing
from Filing import FileFilter, Organizer, FileStatsCollector, parse_size, parse_time, format_size


def rec(name="photo.jpg", size=2048, date="2026-11-09 14:30:00", folder="D:\\Photos", **extra):
    return {"File Name": name, "File Type": os.path.splitext(name.lower())[1], "File Size (Bytes)": size,
            "Modification Date": date, "Creation Date": date, "Source Folder": folder, **extra}


class TestExports(unittest.TestCase):

    def test_names_are_exported_and_work(self):
        for name in ("parse_size", "parse_time", "format_size"):
            self.assertIn(name, Filing.__all__)
            self.assertTrue(callable(getattr(Filing, name)))
        self.assertEqual(parse_size("10KB"), 10240)
        self.assertEqual(parse_size("1.5 MB"), int(1.5 * 1024 ** 2))
        self.assertEqual(parse_size(500), 500)
        self.assertIsNone(parse_size(None))
        self.assertEqual(format_size(2048), "2.00 KB")
        self.assertEqual(format_size(0), "0.00 B")
        self.assertEqual(format_size(1024 ** 3), "1.00 GB")
        self.assertEqual(parse_time("2026-11-09"), datetime(2026, 11, 9).timestamp())
        self.assertIsNone(parse_time(None))

    def test_format_size_is_the_same_function_the_collector_uses(self):
        for size in (0, 1, 1023, 1024, 10 ** 6, 10 ** 12, 5 * 1024 ** 5):
            self.assertEqual(format_size(size), FileStatsCollector.format_size(size))

    def test_parse_helpers_reject_bad_input(self):
        for bad in ("abc", "10 XB", "-5KB", "", "1..5MB"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_size(bad)
        for bad in ("yesterday", "2026-13-45", ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_time(bad)
        with self.assertRaises(TypeError):
            parse_time(True)


class TestMatches(unittest.TestCase):

    def test_no_filters_matches_everything_with_a_name(self):
        self.assertTrue(FileFilter().matches(rec()))
        self.assertTrue(FileFilter().matches({"File Name": "only a name"}))

    def test_name_filters(self):
        self.assertTrue(FileFilter(name_glob="*.JPG").matches(rec("photo.jpg")))
        self.assertFalse(FileFilter(name_glob="*.png").matches(rec("photo.jpg")))
        self.assertTrue(FileFilter(name_glob=["*.png", "ph*"]).matches(rec("photo.jpg")))
        self.assertTrue(FileFilter(name_regex=r"^photo\d*").matches(rec("photo12.jpg")))
        self.assertFalse(FileFilter(name_regex=r"^PHOTO").matches(rec("photo.jpg")))
        self.assertTrue(FileFilter(name_regex=r"(?i)^PHOTO").matches(rec("photo.jpg")))

    def test_name_filters_need_no_other_keys(self):
        self.assertTrue(FileFilter(name_glob="*.jpg").matches({"File Name": "a.jpg"}))

    def test_size_filters_are_inclusive(self):
        f = FileFilter(min_size=100, max_size=200)
        self.assertFalse(f.matches(rec(size=99)))
        self.assertTrue(f.matches(rec(size=100)))
        self.assertTrue(f.matches(rec(size=200)))
        self.assertFalse(f.matches(rec(size=201)))

    def test_size_as_text_like_in_a_csv(self):
        self.assertTrue(FileFilter(min_size="1KB").matches(rec(size="2048")))
        self.assertFalse(FileFilter(min_size="1MB").matches(rec(size="2048")))

    def test_time_filters(self):
        after = FileFilter(modified_after="2026-11-09 14:30:00")
        self.assertTrue(after.matches(rec(date="2026-11-09 14:30:00")))   # inclusive
        self.assertFalse(after.matches(rec(date="2026-11-09 14:29:59")))
        before = FileFilter(modified_before="2026-11-09 14:30:00")
        self.assertFalse(before.matches(rec(date="2026-11-09 14:30:00")))  # exclusive
        self.assertTrue(before.matches(rec(date="2026-11-09 14:29:59")))

    def test_mtime_ns_is_preferred(self):
        stamp = datetime(2026, 11, 9, 14, 30, 0).timestamp()
        f = FileFilter(modified_after=stamp)
        self.assertTrue(f.matches(rec(date="1999-01-01 00:00:00", mtime_ns=int(stamp * 1e9) + 1)))
        self.assertFalse(f.matches(rec(date="2030-01-01 00:00:00", mtime_ns=int((stamp - 10) * 1e9))))

    def test_all_filters_must_pass(self):
        f = FileFilter(min_size=10, name_glob="*.jpg", modified_after="2026-01-01")
        self.assertTrue(f.matches(rec("a.jpg", 20, "2026-06-01 00:00:00")))
        self.assertFalse(f.matches(rec("a.png", 20, "2026-06-01 00:00:00")))
        self.assertFalse(f.matches(rec("a.jpg", 5, "2026-06-01 00:00:00")))
        self.assertFalse(f.matches(rec("a.jpg", 20, "2025-06-01 00:00:00")))

    def test_missing_data_is_an_error_not_a_silent_answer(self):
        with self.assertRaisesRegex(ValueError, "File Size"):
            FileFilter(min_size=1).matches({"File Name": "a"})
        with self.assertRaisesRegex(ValueError, "Modification Date"):
            FileFilter(modified_after="2026-01-01").matches({"File Name": "a"})
        for bad in ({}, {"File Name": None}, {"File Name": 5}, None, "text"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                FileFilter().matches(bad)
        for bad_size in ("abc", True, "", "1.5"):
            with self.assertRaises(ValueError, msg=repr(bad_size)):
                FileFilter(min_size=1).matches(rec(size=bad_size))

    def test_unused_bad_values_are_not_looked_at(self):
        # the size is garbage, but no size filter is active
        self.assertTrue(FileFilter(name_glob="*.jpg").matches(rec(size="garbage")))
        # no time filter active
        self.assertTrue(FileFilter(min_size=1).matches(rec(date="not a date")))

    def test_matches_does_not_touch_the_disk(self):
        f = FileFilter(min_size=1, name_glob="*.jpg", modified_after="2020-01-01")
        with mock.patch("os.stat", side_effect=AssertionError("stat")), \
                mock.patch("os.scandir", side_effect=AssertionError("scandir")), \
                mock.patch("builtins.open", side_effect=AssertionError("open")):
            self.assertTrue(f.matches(rec("x.jpg", 5, "2026-01-01 00:00:00")))

    def test_record_is_not_modified(self):
        record = rec()
        before = dict(record)
        FileFilter(min_size=1, modified_after="2020-01-01", name_glob="*").matches(record)
        self.assertEqual(record, before)


class TestMatchesAgreesWithScanning(unittest.TestCase):
    """Filtering records with matches() must give the same files as filtering while scanning."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filer_matches_test_")
        rng = random.Random(2024)
        names = [f"{prefix}{n}.{ext}" for prefix in ("img", "doc", "IMG_") for n in range(6)
                 for ext in ("jpg", "txt", "PNG")]
        for index, name in enumerate(names):
            path = os.path.join(self.tmp, f"d{index % 3}", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"x" * rng.choice([0, 10, 100, 1000, 5000]))
            mtime = datetime(2026, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23), 0, 0).timestamp()
            os.utime(path, (mtime, mtime))  # whole seconds: no rounding difference

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, collector):
        return sorted(os.path.join(s["Source Folder"], s["File Name"]) for s in collector.file_stats)

    def test_same_result_for_many_filter_combinations(self):
        everything = FileStatsCollector(self.tmp, all_files=True, verbose=False)
        combos = [
            dict(min_size=100), dict(max_size=100), dict(min_size=10, max_size=1000),
            dict(name_glob="*.jpg"), dict(name_glob=["*.png", "doc*"]), dict(name_regex=r"^IMG_\d"),
            dict(modified_after="2026-06-01"), dict(modified_before="2026-06-01"),
            dict(modified_after="2026-03-01", modified_before="2026-09-01", min_size=10),
            dict(name_glob="img*", max_size=1000, modified_after="2026-02-01"),
        ]
        for options in combos:
            scanned = FileStatsCollector(self.tmp, all_files=True, verbose=False, **options)
            f = FileFilter(**options)
            by_matches = sorted(os.path.join(s["Source Folder"], s["File Name"])
                                for s in everything.file_stats if f.matches(s))
            self.assertEqual(by_matches, self.names(scanned), msg=str(options))
            self.assertGreater(len(everything.file_stats), len(by_matches) - 1)


class TestRelativePath(unittest.TestCase):

    def test_presets(self):
        record = rec("photo.jpg", 2048, "2026-11-09 14:30:00")
        self.assertEqual(Organizer("type").relative_path(record), os.path.join("Images", "photo.jpg"))
        self.assertEqual(Organizer("date").relative_path(record), os.path.join("2026", "11", "photo.jpg"))
        self.assertEqual(Organizer("extension").relative_path(record), os.path.join("jpg", "photo.jpg"))
        self.assertEqual(Organizer("size").relative_path(record), os.path.join("Tiny", "photo.jpg"))

    def test_pattern(self):
        record = rec("song.mp3", 5 * 1024 ** 2, "2025-03-04 01:02:03")
        self.assertEqual(Organizer("{type}/{year}/{month}").relative_path(record),
                         os.path.join("Audio", "2025", "03", "song.mp3"))
        self.assertEqual(Organizer("{source}/{name}_{year}").relative_path(record),
                         os.path.join("Photos", "song_2025.mp3"))

    def test_function_rule(self):
        self.assertEqual(Organizer(lambda s: "Mine/" + s["File Type"].lstrip(".")).relative_path(rec()),
                         os.path.join("Mine", "jpg", "photo.jpg"))

    def test_result_never_leaves_the_destination(self):
        """Hostile names and folders must not produce '..', absolute paths or drive letters."""
        hostile = ["..\\..\\evil.txt", "../../evil.txt", "C:\\Windows\\evil.txt", "/etc/passwd",
                   "a:b.txt", "..", "con.txt", "x" * 50 + "\\..\\..\\y.txt"]
        rules = ["{type}", "{source}/{name}", "{year}/{filename}", "{extname}",
                 lambda s: "../../outside", lambda s: "C:\\outside", lambda s: "/abs/path"]
        destination = os.path.join(tempfile.gettempdir(), "filer_dest_never_created")
        for rule in rules:
            organizer = Organizer(rule)
            for name in hostile:
                for folder in ("..", "D:\\", "\\\\server\\share", "x/../../.."):
                    record = rec(name, 1, folder=folder)
                    relative = organizer.relative_path(record)
                    self.assertFalse(os.path.isabs(relative), msg=(rule, name, relative))
                    self.assertEqual(os.path.splitdrive(relative)[0], "")
                    self.assertNotIn("..", relative.split(os.sep), msg=(rule, name, relative))
                    self.assertNotIn(".", relative.split(os.sep), msg=(rule, name, relative))
                    full = os.path.abspath(organizer.destination(record, destination))
                    self.assertEqual(os.path.commonpath([os.path.abspath(destination), full]),
                                     os.path.abspath(destination), msg=(rule, name, full))

    def test_ordinary_file_names_are_kept_exactly(self):
        organizer = Organizer("type")
        for name in ("photo.jpg", "a b.jpg", "UPPER.JPG", "many.dots.in.name.jpg", "café.jpg",
                     "日本語.png", "noextension", ".hidden", "(1) copy.jpg", "x" * 200 + ".jpg"):
            self.assertEqual(os.path.basename(organizer.relative_path(rec(name))), name, msg=name)

    def test_separators_and_drives_in_a_file_name_are_neutralised(self):
        organizer = Organizer("type")
        for name in (r"..\..\evil.txt", "../../evil.txt", "C:evil.txt", "a/b.txt", r"\\server\share\f.txt", "..", "."):
            relative = organizer.relative_path(rec(name))
            self.assertEqual(len(relative.split(os.sep)), 2, msg=(name, relative))  # folder + one name

    def test_relative_path_does_not_touch_the_disk(self):
        organizer = Organizer("{type}/{year}")
        with mock.patch("os.stat", side_effect=AssertionError("stat")), \
                mock.patch("os.scandir", side_effect=AssertionError("scandir")), \
                mock.patch("os.makedirs", side_effect=AssertionError("makedirs")), \
                mock.patch("builtins.open", side_effect=AssertionError("open")):
            self.assertEqual(organizer.relative_path(rec()), os.path.join("Images", "2026", "photo.jpg"))

    def test_bad_rules(self):
        for bad in ("", "   ", "{nonsense}", "{type", 5, None):
            if bad is None:
                continue
            with self.assertRaises(ValueError, msg=repr(bad)):
                Organizer(bad)


if __name__ == "__main__":
    unittest.main()
