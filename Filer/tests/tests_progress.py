import contextlib
import io
import os
import pickle
import shutil
import tempfile
import threading
import unittest

from Filing.duplicates import DuplicateFinder, PARTIAL_CHUNK
from Filing.filestatser import FileStatsCollector
from Filing.gatherer import MediaGatherer, Gatherer
from Filing.progress import (ProgressReporter, ConsoleProgress, make_reporter, STAGE_SCAN, STAGE_READ,
                             STAGE_PLAN, STAGE_MOVE, STAGE_HASH_PARTIAL, STAGE_HASH_FULL, STAGE_LINK)

OLD = 1_500_000_000


def make_file(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class Recorder:
    """A progress callback that remembers every call."""

    def __init__(self):
        self.events = []
        self._lock = threading.Lock()

    def __call__(self, stage, done, total=None):
        with self._lock:
            self.events.append((stage, done, total))

    def of(self, stage):
        return [(d, t) for s, d, t in self.events if s == stage]

    def stages(self):
        seen = []
        for s, _, _ in self.events:
            if s not in seen:
                seen.append(s)
        return seen

    def assert_complete(self, test, stage, expected_total=None):
        calls = self.of(stage)
        test.assertTrue(calls, f"no progress reported for {stage!r}")
        dones = [d for d, _ in calls]
        test.assertEqual(dones, sorted(dones), f"{stage}: progress went backwards")
        last_done, last_total = calls[-1]
        test.assertEqual(last_done, last_total, f"{stage}: never reached 100%")
        if expected_total is not None:
            test.assertEqual(last_total, expected_total)
        for d, t in calls:
            if t is not None:
                test.assertLessEqual(d, t)


def captured(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = fn(*a, **k)
    return result, buf.getvalue()


class TestProgressReporter(unittest.TestCase):

    def test_disabled_reporter_does_nothing(self):
        reporter = ProgressReporter(None)
        self.assertFalse(reporter.enabled)
        reporter("x", 1, 2)  # must not raise

    def test_calls_the_callback(self):
        rec = Recorder()
        reporter = ProgressReporter(rec)
        self.assertTrue(reporter.enabled)
        reporter("stage", 1, 4)
        self.assertEqual(rec.events, [("stage", 1, 4)])

    def test_throttling_keeps_first_and_last(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=1000)
        for i in range(1, 11):
            reporter("s", i, 10)
        self.assertEqual(rec.events, [("s", 1, 10), ("s", 10, 10)])

    def test_stages_are_throttled_separately(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=1000)
        reporter("a", 1, 10)
        reporter("b", 1, 10)
        self.assertEqual(len(rec.events), 2)

    def test_finished_stage_can_start_again(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=1000)
        reporter("s", 5, 5)
        reporter("s", 1, 5)
        self.assertEqual(rec.events, [("s", 5, 5), ("s", 1, 5)])

    def test_unknown_total_is_throttled_until_finished(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=1000)
        for i in range(1, 6):
            reporter("s", i, None)
        reporter("s", 5, 5)
        self.assertEqual(rec.events, [("s", 1, None), ("s", 5, 5)])

    def test_offset_continues_counts(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=0)
        second_batch = reporter.offset(100, 150)
        second_batch("m", 10, 50)
        second_batch("m", 50, 50)
        self.assertEqual(rec.events, [("m", 110, 150), ("m", 150, 150)])

    def test_make_reporter(self):
        self.assertFalse(make_reporter(None).enabled)
        rec = Recorder()
        self.assertTrue(make_reporter(rec).enabled)
        reporter = ProgressReporter(rec)
        self.assertIs(make_reporter(reporter), reporter)
        with self.assertRaises(TypeError):
            make_reporter("not callable")

    def test_exceptions_in_the_callback_propagate(self):
        def cancel(stage, done, total):
            raise KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):
            ProgressReporter(cancel)("s", 1, 2)

    def test_pickles_as_a_silent_reporter(self):
        copy = pickle.loads(pickle.dumps(ProgressReporter(lambda *a: None)))
        self.assertFalse(copy.enabled)

    def test_thread_safe_counting(self):
        rec = Recorder()
        reporter = ProgressReporter(rec, min_interval=0)
        threads = [threading.Thread(target=lambda: [reporter("s", i, 1000) for i in range(1, 251)]) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(rec.events), 1000)


class MarkingCollector(FileStatsCollector):
    """Module level (so a process pool can pickle it): overrides process_files."""

    def process_files(self, foldername, all_files, media_extensions, format_size, file_filter=None):
        stats = super().process_files(foldername, all_files, media_extensions, format_size, file_filter)
        for s in stats:
            s["File Type"] = ".marked"
        return stats


class FakeStream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


class TestConsoleProgress(unittest.TestCase):

    def test_redirected_output_prints_one_line_per_finished_stage(self):
        stream = FakeStream(tty=False)
        bar = ConsoleProgress(stream)
        for i in (1, 5, 10):
            bar("Moving files", i, 10)
        bar("Hashing", 3, 3)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("Moving files", lines[0])
        self.assertIn("10/10", lines[0])
        self.assertIn("100%", lines[0])
        self.assertNotIn("\r", stream.getvalue())

    def test_redirected_unknown_total_prints_when_stage_changes(self):
        stream = FakeStream(tty=False)
        bar = ConsoleProgress(stream)
        bar("Finding folders", 7, None)
        self.assertEqual(stream.getvalue(), "")
        bar("Reading folders", 1, 2)
        self.assertEqual(stream.getvalue(), "Finding folders: 7\n")
        bar.close()

    def test_terminal_output_redraws_one_line(self):
        stream = FakeStream(tty=True)
        bar = ConsoleProgress(stream)
        bar("Moving files", 1, 4)
        bar("Moving files", 4, 4)
        text = stream.getvalue()
        self.assertEqual(text.count("\r"), 2)
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)

    def test_close_ends_an_open_line(self):
        stream = FakeStream(tty=True)
        bar = ConsoleProgress(stream)
        bar("Finding folders", 3, None)
        self.assertFalse(stream.getvalue().endswith("\n"))
        bar.close()
        self.assertTrue(stream.getvalue().endswith("\n"))

    def test_bar_text(self):
        bar = ConsoleProgress(FakeStream(False), width=10)
        self.assertEqual(bar._text("S", 5, 10), "S: [#####-----] 5/10 (50%)")
        self.assertEqual(bar._text("S", 5, None), "S: 5")


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        make_file(os.path.join(self.root, "song.mp3"), b"1")
        make_file(os.path.join(self.root, "a", "photo.jpg"), b"22")
        make_file(os.path.join(self.root, "a", "b", "clip.mp4"), b"333")
        make_file(os.path.join(self.root, "x", "song.mp3"), b"4444")
        make_file(os.path.join(self.root, "a", "notes.txt"), b"text")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestScanProgress(Base):

    def test_scan_reports_both_stages(self):
        rec = Recorder()
        c = FileStatsCollector(self.root, all_files=True, progress=rec)
        self.assertEqual(rec.stages(), [STAGE_SCAN, STAGE_READ])
        rec.assert_complete(self, STAGE_SCAN, expected_total=4)  # root, a, a/b, x
        rec.assert_complete(self, STAGE_READ, expected_total=4)
        self.assertEqual(c.total_files, 5)

    def test_results_do_not_depend_on_progress(self):
        plain = FileStatsCollector(self.root, all_files=True)
        with_progress = FileStatsCollector(self.root, all_files=True, progress=Recorder())
        key = lambda s: (s["Source Folder"], s["File Name"])
        self.assertEqual(sorted(plain.file_stats, key=key), sorted(with_progress.file_stats, key=key))

    def test_verbose_false_silences_scan_messages(self):
        _, quiet_out = captured(FileStatsCollector, self.root, all_files=True, verbose=False)
        self.assertEqual(quiet_out, "")
        _, loud_out = captured(FileStatsCollector, self.root, all_files=True)
        self.assertIn("Gathered 5 file stats", loud_out)

    def test_default_output_is_unchanged(self):
        _, out = captured(FileStatsCollector, self.root, all_files=True)
        self.assertIn("Gathering folders....", out)
        self.assertIn("Total folders found: 4", out)

    def test_process_pool_path_reports_progress_and_same_results(self):
        many = os.path.join(self.tmp, "many")
        count = FileStatsCollector._PARALLEL_MIN_FOLDERS + 10
        for i in range(count):
            make_file(os.path.join(many, f"d{i}", f"f{i}.txt"), b"x" * i)
        rec = Recorder()
        c = FileStatsCollector(many, all_files=True, progress=rec, verbose=False)
        self.assertEqual(c.total_files, count)
        rec.assert_complete(self, STAGE_READ, expected_total=count + 1)

    def test_process_pool_path_works_for_gatherers_with_unpicklable_options(self):
        """A gatherer holding a lambda (organize_by) or a callback must still scan in parallel."""
        many = os.path.join(self.tmp, "many2")
        count = FileStatsCollector._PARALLEL_MIN_FOLDERS + 5
        for i in range(count):
            make_file(os.path.join(many, f"d{i}", f"f{i}.png"))
        g = MediaGatherer(many, organize_by=lambda stat: "pics", progress=Recorder(), verbose=False)
        self.assertEqual(g.total_files, count)

    def test_subclass_overriding_process_files_is_still_used_in_parallel(self):
        many = os.path.join(self.tmp, "many3")
        for i in range(FileStatsCollector._PARALLEL_MIN_FOLDERS + 2):
            make_file(os.path.join(many, f"d{i}", f"f{i}.txt"))

        c = MarkingCollector(many, all_files=True, verbose=False)
        self.assertEqual({s["File Type"] for s in c.file_stats}, {".marked"})

    def test_invalid_progress_argument(self):
        with self.assertRaises(TypeError):
            FileStatsCollector(self.root, progress="nope")


class TestMoveProgress(Base):

    def test_move_progress_reaches_total(self):
        rec = Recorder()
        g = MediaGatherer(self.root, progress=rec, verbose=False)
        g.gather_media()
        rec.assert_complete(self, STAGE_MOVE, expected_total=4)

    def test_batched_moves_continue_counting(self):
        for i in range(120):
            make_file(os.path.join(self.root, "bulk", f"f{i}.png"))
        rec = Recorder()
        g = MediaGatherer(self.root, progress=rec, verbose=False)
        g.gather_media(batch_size=25, time_delay=0)
        rec.assert_complete(self, STAGE_MOVE, expected_total=124)
        self.assertEqual({t for _, t in rec.of(STAGE_MOVE)}, {124})
        self.assertEqual(g._move_offset, 0)  # state reset afterwards

    def test_dry_run_reports_planning_only(self):
        rec = Recorder()
        g = MediaGatherer(self.root, progress=rec, verbose=False)
        plan = g.gather_media(dry_run=True)
        self.assertEqual(len(plan), 4)
        self.assertIn(STAGE_PLAN, rec.stages())
        self.assertNotIn(STAGE_MOVE, rec.stages())
        rec.assert_complete(self, STAGE_PLAN, expected_total=4)

    def test_verbose_false_prints_nothing_while_moving(self):
        g = MediaGatherer(self.root, verbose=False)
        _, out = captured(g.gather_media)
        self.assertEqual(out, "")
        _, undo_out = captured(g.undo_last_move)
        self.assertEqual(undo_out, "")

    def test_default_move_output_is_unchanged(self):
        g = MediaGatherer(self.root)
        _, out = captured(g.gather_media)
        self.assertIn("Moved: photo.jpg", out)
        self.assertIn("Total Files Moved: 4", out)
        self.assertIn("Journal saved:", out)
        self.assertIn("Total Moved Files: 4", out)

    def test_errors_are_still_printed_when_not_verbose(self):
        g = MediaGatherer(self.root, verbose=False)
        os.remove(os.path.join(self.root, "a", "photo.jpg"))  # vanishes between scan and move
        _, out = captured(g.gather_media)
        self.assertIn("Error moving", out)

    def test_callback_can_cancel_a_move(self):
        def cancel_after_first(stage, done, total):
            if stage == STAGE_MOVE and done >= 1:
                raise KeyboardInterrupt
        g = MediaGatherer(self.root, progress=cancel_after_first, verbose=False)
        before = len(os.listdir(self.root))
        try:
            g.gather_media()
        except BaseException:
            pass
        # the journal is saved even though the run was cancelled, so what moved can be undone
        self.assertTrue(os.path.isdir(os.path.join(self.root, ".filer_journal")))
        MediaGatherer(self.root, verbose=False).undo_last_move()
        self.assertEqual(len(MediaGatherer(self.root, verbose=False).get_files_to_move()), 4)

    def test_gatherer_class_accepts_progress_and_verbose(self):
        rec = Recorder()
        g = Gatherer(self.root, extensions=[".txt"], progress=rec, verbose=False)
        _, out = captured(g.gather_files)
        self.assertEqual(out, "")
        rec.assert_complete(self, STAGE_MOVE, expected_total=1)


class TestDuplicateProgress(Base):

    def setUp(self):
        super().setUp()
        self.dups = os.path.join(self.tmp, "dups")
        self.big = bytes(range(256)) * (PARTIAL_CHUNK * 3 // 256)
        make_file(os.path.join(self.dups, "a", "one.txt"), b"same")
        make_file(os.path.join(self.dups, "b", "two.txt"), b"same")
        make_file(os.path.join(self.dups, "a", "big1.bin"), self.big)
        make_file(os.path.join(self.dups, "b", "big2.bin"), self.big)

    def test_hash_stages_are_reported(self):
        rec = Recorder()
        finder = DuplicateFinder(self.dups, progress=rec, verbose=False, use_cache=False)
        groups = finder.find()
        self.assertEqual(len(groups), 2)
        self.assertEqual(rec.stages(), [STAGE_SCAN, STAGE_READ, STAGE_HASH_PARTIAL, STAGE_HASH_FULL])
        rec.assert_complete(self, STAGE_HASH_PARTIAL, expected_total=4)
        rec.assert_complete(self, STAGE_HASH_FULL, expected_total=2)  # only the big files need a full hash

    def test_cached_files_still_report_progress(self):
        DuplicateFinder(self.dups, verbose=False).find()
        rec = Recorder()
        DuplicateFinder(self.dups, progress=rec, verbose=False).find()
        rec.assert_complete(self, STAGE_HASH_PARTIAL, expected_total=4)

    def test_verbose_false_silences_find_and_move(self):
        finder = DuplicateFinder(self.dups, verbose=False, use_cache=False)
        _, out = captured(finder.find)
        self.assertEqual(out, "")
        _, out = captured(finder.move_duplicates)
        self.assertEqual(out, "")
        _, out = captured(finder.undo_last_move)
        self.assertEqual(out, "")

    def test_default_find_output_is_unchanged(self):
        finder = DuplicateFinder(self.dups, use_cache=False)
        _, out = captured(finder.find)
        self.assertIn("Checked 4 files, found 2 duplicate file(s)", out)

    def test_print_summary_still_prints_when_not_verbose(self):
        finder = DuplicateFinder(self.dups, verbose=False, use_cache=False)
        _, out = captured(finder.print_summary)
        self.assertIn("Duplicate groups: 2", out)

    def test_move_progress(self):
        rec = Recorder()
        finder = DuplicateFinder(self.dups, progress=rec, verbose=False, use_cache=False)
        finder.move_duplicates()
        rec.assert_complete(self, STAGE_MOVE, expected_total=2)

    def test_link_progress(self):
        rec = Recorder()
        finder = DuplicateFinder(self.dups, progress=rec, verbose=False, use_cache=False)
        finder.link_duplicates(dry_run=True)
        rec.assert_complete(self, STAGE_LINK, expected_total=2)

    def test_no_duplicates_still_finishes_cleanly(self):
        solo = os.path.join(self.tmp, "solo")
        make_file(os.path.join(solo, "a.txt"), b"1")
        rec = Recorder()
        self.assertEqual(DuplicateFinder(solo, progress=rec, verbose=False).find(), [])
        self.assertNotIn(STAGE_HASH_PARTIAL, rec.stages())


if __name__ == "__main__":
    unittest.main()
