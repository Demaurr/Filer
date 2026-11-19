"""
Tests for the one-pass scan: it must give exactly the records the older two-pass scan gave
(same fields, same order), and handle skips, filters, odd names and unreadable folders.

All folders and files are created by the tests in their own temporary folder.
"""
import os
import random
import shutil
import tempfile
import unittest
from unittest import mock

from Filing import FileStatsCollector

OLD = 1_600_000_000


def write(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    os.utime(path, (mtime, mtime))


class TwoPass(FileStatsCollector):
    """Overriding process_files selects the older code path (list all folders, then read each one)."""

    def process_files(self, *args, **kwargs):
        return super().process_files(*args, **kwargs)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filer_scan_test_")
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self, cls=FileStatsCollector, **kw):
        kw.setdefault("all_files", True)
        kw.setdefault("verbose", False)
        return cls(self.root, **kw).file_stats


NAMES = ["a.txt", "B.JPG", "noext", ".hidden", ".hidden.txt", "a..b", "..b", "multi.part.name.tar.gz",
         "café.png", "UPPER.Mp3", "name with spaces.doc"]


class TestSameResultsAsTwoPass(Base):

    def build(self):
        rng = random.Random(3)
        folders = ["", "a", "a/b", "a/b/c", "x", "x/y", "empty_dir", ".git", "node_modules/pkg", "deep/1/2/3/4/5"]
        for folder in folders:
            os.makedirs(os.path.join(self.root, *folder.split("/")), exist_ok=True)
        for n in range(120):
            folder = rng.choice(folders)
            name = rng.choice(NAMES) if rng.random() < 0.6 else f"f{n}.{rng.choice(['txt', 'JPG', 'mp3', 'log'])}"
            path = os.path.join(self.root, *([] if not folder else folder.split("/")), f"{n}_{name}")
            write(path, b"d" * rng.choice([0, 1, 50, 2000]), mtime=OLD + rng.randint(0, 10 ** 7))

    def test_identical_records_and_order(self):
        self.build()
        self.assertEqual(self.scan(), self.scan(TwoPass))
        self.assertGreater(len(self.scan()), 100)

    def test_identical_with_skip_folders_by_name_and_path(self):
        self.build()
        skips = [".git", "node_modules", os.path.join(self.root, "a", "b")]
        self.assertEqual(self.scan(skip_folders=skips), self.scan(TwoPass, skip_folders=skips))
        folders = {s["Source Folder"] for s in self.scan(skip_folders=skips)}
        self.assertFalse(any(".git" in f or "node_modules" in f or f.startswith(os.path.join(self.root, "a", "b")) for f in folders))

    def test_identical_with_filters(self):
        self.build()
        for options in (dict(min_size=10), dict(max_size=100), dict(name_glob="*.txt"), dict(name_regex=r"\d_f\d+"),
                        dict(modified_after=OLD + 5 * 10 ** 6), dict(media_extensions=[".jpg", ".mp3"], all_files=False)):
            self.assertEqual(self.scan(**options), self.scan(TwoPass, **options), msg=str(options))

    def test_file_type_matches_splitext_for_awkward_names(self):
        for name in NAMES:
            write(os.path.join(self.root, name))
        by_name = {s["File Name"]: s["File Type"] for s in self.scan()}
        for name in NAMES:
            self.assertEqual(by_name[name], os.path.splitext(name.lower())[1], msg=name)

    def test_file_type_matches_splitext_for_random_names(self):
        rng = random.Random(11)
        alphabet = "ab.. C"
        names = {"".join(rng.choice(alphabet) for _ in range(rng.randint(1, 7))) for _ in range(300)}
        names = {n for n in names if n.strip() and n.strip() == n and not n.endswith(".") and not n.endswith(" ")}
        for name in names:
            write(os.path.join(self.root, name))
        found = {s["File Name"]: s["File Type"] for s in self.scan()}
        for name in names:
            self.assertEqual(found[name], os.path.splitext(name.lower())[1], msg=repr(name))

    def test_creation_date_text_when_equal_to_and_different_from_modification(self):
        write(os.path.join(self.root, "f.txt"), mtime=OLD)
        with mock.patch.object(FileStatsCollector, "get_creation_timestamp", staticmethod(lambda info: info.st_mtime)):
            record = self.scan()[0]
        self.assertEqual(record["Creation Date"], FileStatsCollector.format_timestamp(OLD))
        self.assertEqual(record["Modification Date"], FileStatsCollector.format_timestamp(OLD))
        with mock.patch.object(FileStatsCollector, "get_creation_timestamp", staticmethod(lambda info: OLD + 86400)):
            record = self.scan()[0]
        self.assertEqual(record["Creation Date"], FileStatsCollector.format_timestamp(OLD + 86400))
        self.assertEqual(record["Modification Date"], FileStatsCollector.format_timestamp(OLD))
        self.assertNotEqual(record["Creation Date"], record["Modification Date"])


class TestBehaviour(Base):

    def test_empty_folder(self):
        self.assertEqual(self.scan(), [])

    def test_only_media_files_by_default(self):
        write(os.path.join(self.root, "a.mp3"))
        write(os.path.join(self.root, "b.txt"))
        names = [s["File Name"] for s in self.scan(all_files=False)]
        self.assertEqual(names, ["a.mp3"])

    def test_root_is_not_skipped_by_its_own_name(self):
        write(os.path.join(self.root, "f.txt"))
        self.assertEqual(len(self.scan(skip_folders=["root"])), 1)

    def test_folder_that_cannot_be_listed_is_skipped_quietly(self):
        write(os.path.join(self.root, "ok", "a.txt"))
        write(os.path.join(self.root, "locked", "b.txt"))
        real_scandir = os.scandir

        def scandir(path):
            if os.path.basename(str(path)) == "locked":
                raise PermissionError("no access")
            return real_scandir(path)

        with mock.patch("os.scandir", scandir):
            names = [s["File Name"] for s in self.scan()]
        self.assertEqual(names, ["a.txt"])

    def test_other_errors_are_reported_and_the_scan_continues(self):
        write(os.path.join(self.root, "ok", "a.txt"))
        write(os.path.join(self.root, "bad", "b.txt"))
        real_scandir = os.scandir

        def scandir(path):
            if os.path.basename(str(path)) == "bad":
                raise OSError("disk problem")
            return real_scandir(path)

        with mock.patch("os.scandir", scandir), mock.patch("builtins.print") as printed:
            names = [s["File Name"] for s in self.scan()]
        self.assertEqual(names, ["a.txt"])
        self.assertTrue(any("disk problem" in str(call) for call in printed.call_args_list))

    def test_no_second_listing_of_folders(self):
        """One directory listing per folder (the old scan listed every folder twice)."""
        for folder in ("a", "b", "c"):
            write(os.path.join(self.root, folder, "f.txt"))
        real_scandir = os.scandir
        calls = []

        def scandir(path):
            calls.append(path)
            return real_scandir(path)

        with mock.patch("os.scandir", scandir):
            self.scan()
        self.assertEqual(len(calls), 4)  # root, a, b, c
        self.assertEqual(len(set(map(os.path.normcase, calls))), 4)

    def test_no_worker_processes_are_started(self):
        for n in range(FileStatsCollector._PARALLEL_MIN_FOLDERS + 5):
            write(os.path.join(self.root, f"d{n}", "f.txt"))
        with mock.patch("Filing.filestatser.Pool", side_effect=AssertionError("process pool used")):
            self.assertEqual(len(self.scan()), FileStatsCollector._PARALLEL_MIN_FOLDERS + 5)

    def test_symlinked_folders_are_not_followed(self):
        write(os.path.join(self.tmp, "elsewhere", "secret.txt"))
        write(os.path.join(self.root, "f.txt"))
        link = os.path.join(self.root, "alias")
        try:
            os.symlink(os.path.join(self.tmp, "elsewhere"), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symbolic links here")
        self.assertEqual([s["File Name"] for s in self.scan()], ["f.txt"])


if __name__ == "__main__":
    unittest.main()
