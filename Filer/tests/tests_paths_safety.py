"""
Safety tests: path containment (Filing.paths), crafted/edited journals, and refused moves.

Every file used here is created by the test inside its own temporary folder. "Victim" files that
must never be touched are created next to the scanned folder and checked at the end of each test.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from Filing import MediaGatherer, DuplicateFinder
from Filing import cli
from Filing.journal import MoveJournal, undo_moves, JOURNAL_DIR_NAME, list_journals, load_journal
from Filing.mover import execute_moves
from Filing.paths import UnsafePathError, PathGuard, ensure_inside, is_inside, check_path_syntax

OLD = 1_500_000_000


def write(path, content=b"data"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    os.utime(path, (OLD, OLD))
    return path


def read(path):
    with open(path, "rb") as f:
        return f.read()


def make_dir_link(link, target):
    """Make a folder link: a symbolic link, or on Windows (without the privilege) a junction. False if neither works."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        import subprocess
        result = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True)
        return result.returncode == 0 and os.path.isdir(link)
    return False


def can_make_symlinks(tmp):
    target = os.path.join(tmp, "_t")
    link = os.path.join(tmp, "_l")
    os.makedirs(target, exist_ok=True)
    ok = make_dir_link(link, target)
    if ok:
        os.rmdir(link)
    return ok


class TempCase(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="filer_safety_test_"))
        self.root = os.path.join(self.tmp, "root")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.root)
        os.makedirs(self.outside)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEnsureInside(TempCase):

    def test_paths_inside_are_accepted_and_resolved(self):
        inside = os.path.join(self.root, "a", "b.txt")
        self.assertEqual(os.path.normcase(ensure_inside(self.root, inside)), os.path.normcase(inside))
        self.assertTrue(is_inside(self.root, inside))
        self.assertTrue(is_inside([self.outside, self.root], inside))

    def test_path_need_not_exist(self):
        self.assertTrue(is_inside(self.root, os.path.join(self.root, "no", "such", "deep", "file.txt")))

    def test_dotdot_cannot_escape(self):
        for sneaky in (os.path.join(self.root, "..", "outside", "x.txt"),
                       os.path.join(self.root, "a", "..", "..", "outside", "x.txt"),
                       self.root + os.sep + ".." + os.sep + "root2" + os.sep + "x"):
            with self.assertRaises(UnsafePathError, msg=sneaky):
                ensure_inside(self.root, sneaky)

    def test_dotdot_that_stays_inside_is_fine(self):
        self.assertTrue(is_inside(self.root, os.path.join(self.root, "a", "..", "b.txt")))

    def test_sibling_with_the_same_prefix_is_outside(self):
        sibling = self.root + "_other"
        os.makedirs(sibling)
        self.assertFalse(is_inside(self.root, os.path.join(sibling, "x.txt")))

    def test_root_itself_only_with_allow_root(self):
        self.assertFalse(is_inside(self.root, self.root))
        self.assertTrue(is_inside(self.root, self.root, allow_root=True))
        self.assertFalse(is_inside(self.root, os.path.dirname(self.root), allow_root=True))

    def test_relative_paths_are_taken_relative_to_the_first_root(self):
        self.assertTrue(is_inside(self.root, os.path.join("a", "b.txt")))
        self.assertFalse(is_inside(self.root, os.path.join("..", "outside", "b.txt")))

    def test_several_roots(self):
        self.assertTrue(is_inside([self.root, self.outside], os.path.join(self.outside, "f")))
        self.assertFalse(is_inside([self.root], os.path.join(self.outside, "f")))

    def test_no_roots_allows_nothing(self):
        with self.assertRaises(UnsafePathError):
            ensure_inside([], os.path.join(self.root, "x"))

    def test_bad_path_values(self):
        for bad in ("", "   ", "a\x00b", None, 5, b"bytes", ["x"]):
            with self.assertRaises(UnsafePathError, msg=repr(bad)):
                ensure_inside(self.root, bad)
        with self.assertRaises(UnsafePathError):
            PathGuard(self.root).check(None)

    def test_pathlike_is_accepted(self):
        import pathlib
        self.assertTrue(is_inside(pathlib.Path(self.root), pathlib.Path(self.root) / "a.txt"))

    def test_error_is_a_valueerror_naming_the_path(self):
        with self.assertRaisesRegex(ValueError, "not inside"):
            ensure_inside(self.root, os.path.join(self.outside, "x"))

    def test_symlink_pointing_out_is_refused(self):
        if not can_make_symlinks(self.tmp):
            self.skipTest("cannot create symbolic links here")
        link = os.path.join(self.root, "escape")
        self.assertTrue(make_dir_link(link, self.outside))
        self.assertFalse(is_inside(self.root, os.path.join(link, "x.txt")))
        guard = PathGuard(self.root)
        with self.assertRaises(UnsafePathError):
            guard.check(os.path.join(link, "x.txt"), resolve_name=False)

    def test_symlink_pointing_inside_is_fine(self):
        if not can_make_symlinks(self.tmp):
            self.skipTest("cannot create symbolic links here")
        os.makedirs(os.path.join(self.root, "real"))
        self.assertTrue(make_dir_link(os.path.join(self.root, "alias"), os.path.join(self.root, "real")))
        self.assertTrue(is_inside(self.root, os.path.join(self.root, "alias", "x.txt")))

    def test_final_link_is_followed_only_when_asked(self):
        """A file link inside the root pointing out: a move acts on the link, a read would not."""
        if not can_make_symlinks(self.tmp):
            self.skipTest("cannot create symbolic links here")
        target = write(os.path.join(self.outside, "secret.txt"))
        link = os.path.join(self.root, "link.txt")
        try:
            os.symlink(target, link)
        except OSError:
            self.skipTest("cannot create file symbolic links here")
        guard = PathGuard(self.root)
        self.assertFalse(is_inside(self.root, link))                 # followed: leads outside
        guard.check(link, resolve_name=False)                        # not followed: the link is inside

    def test_guard_checks_many_paths_consistently(self):
        guard = PathGuard(self.root)
        inside = [os.path.join(self.root, f"d{n % 3}", f"f{n}.txt") for n in range(50)]
        for path in inside:
            guard.check(path, resolve_name=False)
        with self.assertRaises(UnsafePathError):
            guard.check(os.path.join(self.outside, "f.txt"), resolve_name=False)


class TestPathSyntax(unittest.TestCase):

    def test_device_names_refused_on_windows_rules(self):
        for bad in ("C:\\data\\NUL", "C:\\data\\nul.txt", "C:\\data\\CON", "C:\\data\\com1.log", "C:\\data\\LPT9",
                    "C:\\data\\aux .txt", "C:\\data\\Prn...", "C:\\data\\x\\con\\y.txt", "\\\\.\\COM1"):
            with self.assertRaises(UnsafePathError, msg=bad):
                check_path_syntax(bad, windows=True)

    def test_alternate_data_streams_refused_on_windows_rules(self):
        for bad in ("C:\\data\\file.txt:hidden", "C:\\data\\file.txt:hidden:$DATA", "relative\\file:stream"):
            with self.assertRaises(UnsafePathError, msg=bad):
                check_path_syntax(bad, windows=True)

    def test_ordinary_windows_paths_pass(self):
        for good in ("C:\\data\\file.txt", "D:/photos/a.jpg", "\\\\server\\share\\f.txt", "\\\\?\\C:\\long\\path\\f.txt",
                     "C:\\data\\console.txt", "C:\\data\\nullable", "C:\\data\\com10.txt", "C:\\data\\.hidden",
                     "relative\\path\\f.txt", "C:\\data\\lpt.txt"):
            self.assertEqual(check_path_syntax(good, windows=True), good)

    def test_windows_rules_can_be_off(self):
        self.assertEqual(check_path_syntax("/data/nul", windows=False), "/data/nul")
        self.assertEqual(check_path_syntax("/data/file:stream", windows=False), "/data/file:stream")

    def test_nul_character_is_always_refused(self):
        for windows in (True, False):
            with self.assertRaises(UnsafePathError):
                check_path_syntax("a\x00b", windows=windows)


class TestCraftedJournals(TempCase):
    """A journal is a file anyone can edit or ship inside a folder. Undo must not follow it out of the root."""

    def setUp(self):
        super().setUp()
        self.victim = write(os.path.join(self.outside, "victim.txt"), b"VICTIM")
        self.victim_dir = os.path.join(self.outside, "victim_dir")
        os.makedirs(self.victim_dir)
        self.journal_dir = os.path.join(self.root, JOURNAL_DIR_NAME)

    def craft(self, moves, created_dirs=(), root=None):
        os.makedirs(self.journal_dir, exist_ok=True)
        path = os.path.join(self.journal_dir, "move_20260101_000000_000000.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "operation": "move", "created": "2026-01-01T00:00:00",
                       "root": self.root if root is None else root, "undone": False,
                       "moves": moves, "created_dirs": list(created_dirs)}, f)
        return path

    def assert_untouched(self):
        self.assertEqual(read(self.victim), b"VICTIM")
        self.assertTrue(os.path.isdir(self.victim_dir))

    def reasons(self, result):
        return [item["reason"] for item in result["skipped"]]

    def test_moving_a_file_from_outside_into_the_root_is_refused(self):
        journal = self.craft([{"source": os.path.join(self.root, "stolen.txt"), "destination": self.victim}])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assertTrue(self.reasons(result)[0].startswith("unsafe path"))
        self.assert_untouched()
        self.assertFalse(os.path.exists(os.path.join(self.root, "stolen.txt")))

    def test_moving_a_root_file_out_of_the_root_is_refused(self):
        inside = write(os.path.join(self.root, "mine.txt"), b"MINE")
        target = os.path.join(self.outside, "planted.txt")
        journal = self.craft([{"source": target, "destination": inside}])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assertEqual(read(inside), b"MINE")
        self.assertFalse(os.path.exists(target))

    def test_dotdot_in_journal_paths_is_refused(self):
        sneaky = os.path.join(self.root, "..", "outside", "victim.txt")
        journal = self.craft([{"source": os.path.join(self.root, "x.txt"), "destination": sneaky}])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assert_untouched()

    def test_relative_paths_in_a_journal_are_judged_against_the_root(self):
        write(os.path.join(self.root, "inside.txt"), b"IN")
        journal = self.craft([{"source": os.path.join("..", "outside", "dropped.txt"),
                               "destination": os.path.join(self.root, "inside.txt")}])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assertFalse(os.path.exists(os.path.join(self.outside, "dropped.txt")))

    def test_created_dirs_outside_the_root_are_never_removed(self):
        journal = self.craft([], created_dirs=[self.victim_dir, os.path.join(self.root, "made_by_run")])
        os.makedirs(os.path.join(self.root, "made_by_run"))
        undo_moves(journal, root_folder=self.root)
        self.assert_untouched()
        self.assertFalse(os.path.exists(os.path.join(self.root, "made_by_run")))  # the legitimate one is removed

    def test_the_root_folder_itself_is_never_removed_via_created_dirs(self):
        journal = self.craft([], created_dirs=[self.root])
        undo_moves(journal, root_folder=self.root)
        self.assertTrue(os.path.isdir(self.root))

    def test_a_journal_that_names_another_root_cannot_widen_the_scope(self):
        """The journal's own 'root' field is ignored when the caller states the root."""
        journal = self.craft([{"source": os.path.join(self.root, "stolen.txt"), "destination": self.victim}],
                             root=self.tmp)
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assert_untouched()

    def test_without_a_caller_root_the_journals_own_root_is_used(self):
        inside = write(os.path.join(self.root, "moved", "a.txt"), b"A")
        journal = self.craft([{"source": os.path.join(self.root, "a.txt"), "destination": inside}])
        result = undo_moves(journal)
        self.assertEqual(len(result["restored"]), 1)
        self.assertEqual(read(os.path.join(self.root, "a.txt")), b"A")

    def test_a_journal_without_any_root_is_refused(self):
        journal = self.craft([])
        data = load_journal(journal)
        del data["root"]
        with open(journal, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(UnsafePathError):
            undo_moves(journal)

    def test_allowed_roots_can_include_a_real_destination(self):
        dest = os.path.join(self.outside, "sorted")
        moved = write(os.path.join(dest, "a.txt"), b"A")
        journal = self.craft([{"source": os.path.join(self.root, "a.txt"), "destination": moved}])
        refused = undo_moves(journal, root_folder=self.root, dry_run=True)
        self.assertEqual(refused["restored"], {})
        allowed = undo_moves(journal, root_folder=self.root, allowed_roots=[self.root, dest])
        self.assertEqual(len(allowed["restored"]), 1)
        self.assertEqual(read(os.path.join(self.root, "a.txt")), b"A")

    def test_malformed_entries_are_skipped_not_crashes(self):
        good_dest = write(os.path.join(self.root, "moved", "ok.txt"), b"OK")
        journal = self.craft([
            "just text", None, 5, {}, {"source": 5, "destination": 6}, {"source": None, "destination": None},
            {"source": os.path.join(self.root, "a\x00b"), "destination": good_dest},
            {"source": os.path.join(self.root, "ok.txt"), "destination": good_dest},
        ])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(list(result["restored"].values()), [os.path.join(self.root, "ok.txt")])
        self.assertEqual(len(result["skipped"]), 7)
        self.assertEqual(read(os.path.join(self.root, "ok.txt")), b"OK")

    def test_a_journal_that_is_not_a_journal(self):
        for content in ("[]", "5", '"text"', '{"moves": 5}'):
            path = os.path.join(self.root, "j.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            with self.assertRaises(ValueError, msg=content):
                undo_moves(path, root_folder=self.root)

    def test_device_and_stream_names_in_a_journal_are_refused_on_windows_rules(self):
        if os.name != "nt":
            self.skipTest("Windows-only form checks")
        moved = write(os.path.join(self.root, "moved", "a.txt"))
        for bad in (os.path.join(self.root, "NUL"), os.path.join(self.root, "x.txt:stream")):
            journal = self.craft([{"source": bad, "destination": moved}])
            result = undo_moves(journal, root_folder=self.root, dry_run=True)
            self.assertEqual(result["restored"], {}, msg=bad)

    def test_skipped_unsafe_entries_stay_in_the_journal(self):
        journal = self.craft([{"source": os.path.join(self.root, "stolen.txt"), "destination": self.victim}])
        undo_moves(journal, root_folder=self.root)
        data = load_journal(journal)
        self.assertFalse(data.get("undone"))
        self.assertFalse(data["moves"][0].get("restored"))

    def test_symlinked_folder_in_a_journal_cannot_lead_out(self):
        if not can_make_symlinks(self.tmp):
            self.skipTest("cannot create symbolic links here")
        link = os.path.join(self.root, "alias")
        self.assertTrue(make_dir_link(link, self.outside))
        journal = self.craft([{"source": os.path.join(self.root, "stolen.txt"),
                               "destination": os.path.join(link, "victim.txt")}])
        result = undo_moves(journal, root_folder=self.root)
        self.assertEqual(result["restored"], {})
        self.assert_untouched()


class TestLegitimateUseStillWorks(TempCase):
    """The new checks must not get in the way of normal moves and undos."""

    def setUp(self):
        super().setUp()
        for name in ("a.mp3", "b.mp3", "c.jpg"):
            write(os.path.join(self.root, "sub", name), name.encode())

    def test_move_and_undo_with_default_destination(self):
        g = MediaGatherer(self.root, verbose=False)
        moved = g.gather_media()
        self.assertEqual(len(moved), 3)
        result = g.undo_last_move()
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(result["skipped"], [])

    def test_move_and_undo_with_destination_outside_the_root(self):
        dest = os.path.join(self.outside, "sorted")
        g = MediaGatherer(self.root, destination_folder=dest, organize_by="type", verbose=False)
        moved = g.gather_media()
        self.assertEqual(len(moved), 3)
        self.assertTrue(all(os.path.commonpath([dest, p]) == dest for p in moved.values()))
        result = g.undo_last_move()  # the gatherer knows its destination
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(result["skipped"], [])

    def test_undo_from_a_fresh_object_needs_the_destination_to_be_allowed(self):
        dest = os.path.join(self.outside, "sorted")
        MediaGatherer(self.root, destination_folder=dest, verbose=False).gather_media()
        journal_dir = os.path.join(self.root, JOURNAL_DIR_NAME)
        strict = undo_moves(None, root_folder=self.root, journal_dir=journal_dir, dry_run=True)
        self.assertEqual(strict["restored"], {})
        self.assertEqual(len(strict["skipped"]), 3)
        allowed = undo_moves(None, root_folder=self.root, journal_dir=journal_dir, allowed_roots=[self.root, dest])
        self.assertEqual(len(allowed["restored"]), 3)

    def test_duplicates_move_and_undo(self):
        write(os.path.join(self.root, "x", "one.dat"), b"same")
        write(os.path.join(self.root, "y", "two.dat"), b"same")
        f = DuplicateFinder(self.root, verbose=False)
        f.find()
        moved = f.move_duplicates()
        self.assertEqual(len(moved), 1)
        result = f.undo_last_move()
        self.assertEqual(len(result["restored"]), 1)

    def test_duplicates_with_a_custom_destination_outside_the_root(self):
        write(os.path.join(self.root, "x", "one.dat"), b"same")
        write(os.path.join(self.root, "y", "two.dat"), b"same")
        dest = os.path.join(self.outside, "dups")
        f = DuplicateFinder(self.root, verbose=False)
        f.find()
        f.move_duplicates(destination_folder=dest)
        self.assertEqual(len(os.listdir(dest)), 1)
        result = f.undo_last_move()
        self.assertEqual(len(result["restored"]), 1)
        self.assertEqual(os.listdir(dest), [])


class TestExecuteMovesGuard(TempCase):

    def test_moves_outside_allowed_roots_are_refused_and_reported(self):
        source = write(os.path.join(self.root, "a.txt"), b"A")
        stray = write(os.path.join(self.outside, "stray.txt"), b"S")
        plan = [(source, os.path.join(self.root, "ok", "a.txt")),
                (source.replace("a.txt", "b.txt"), os.path.join(self.outside, "escaped.txt")),
                (stray, os.path.join(self.root, "ok", "stray.txt"))]
        write(plan[1][0], b"B")
        with contextlib.redirect_stdout(io.StringIO()):
            moved, failed = execute_moves(plan, allowed_roots=[self.root])
        self.assertEqual(list(moved), [source])
        self.assertEqual(len(failed), 2)
        self.assertTrue(all("not inside" in message for _, message in failed))
        self.assertEqual(read(plan[1][0]), b"B")                     # still where it was
        self.assertFalse(os.path.exists(plan[1][1]))                 # nothing appeared outside
        self.assertEqual(read(stray), b"S")

    def test_no_allowed_roots_means_no_restriction(self):
        source = write(os.path.join(self.root, "a.txt"))
        moved, failed = execute_moves([(source, os.path.join(self.outside, "a.txt"))], verbose=False)
        self.assertEqual((len(moved), failed), (1, []))

    def test_refused_moves_do_not_create_folders_or_journal_entries(self):
        source = write(os.path.join(self.root, "a.txt"))
        journal = MoveJournal(self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            execute_moves([(source, os.path.join(self.outside, "new_folder", "a.txt"))], journal=journal,
                          allowed_roots=[self.root])
        self.assertFalse(os.path.exists(os.path.join(self.outside, "new_folder")))
        self.assertEqual(journal.moves, [])
        self.assertEqual(journal.created_dirs, [])
        self.assertEqual(list_journals(journal.journal_dir), [])


class TestJournalNames(TempCase):
    """Two runs in the same clock tick must never share (and overwrite) one journal file."""

    def test_same_timestamp_gets_different_files(self):
        paths = []
        for n in range(5):
            source = write(os.path.join(self.root, f"s{n}.txt"))
            journal = MoveJournal(self.root)
            journal.created = datetime(2026, 1, 1, 12, 0, 0, 123456)
            journal.record_move(source, os.path.join(self.root, f"d{n}.txt"))
            paths.append(journal.save())
        self.assertEqual(len(set(paths)), 5)
        self.assertEqual(len(list_journals(os.path.join(self.root, JOURNAL_DIR_NAME))), 5)
        self.assertEqual(list_journals(os.path.join(self.root, JOURNAL_DIR_NAME)), sorted(paths))  # still in time order
        for n, path in enumerate(paths):  # each one still holds its own move
            self.assertTrue(load_journal(path)["moves"][0]["source"].endswith(f"s{n}.txt"))

    def test_saving_the_same_journal_again_keeps_its_file(self):
        journal = MoveJournal(self.root)
        journal.record_move(os.path.join(self.root, "a"), os.path.join(self.root, "b"))
        first = journal.save()
        journal.record_move(os.path.join(self.root, "c"), os.path.join(self.root, "d"))
        self.assertEqual(journal.save(), first)
        self.assertEqual(len(load_journal(first)["moves"]), 2)

    def test_back_to_back_gathers_keep_both_undo_records(self):
        for name in ("a.jpg", "b.mp4"):
            write(os.path.join(self.root, name), name.encode())
        first = MediaGatherer(self.root, media_extensions=[".jpg"], verbose=False)
        first.gather_media()
        second = MediaGatherer(self.root, media_extensions=[".mp4"], verbose=False)
        second.gather_media()
        self.assertEqual(len(list_journals(os.path.join(self.root, JOURNAL_DIR_NAME))), 2)
        self.assertEqual(len(second.undo_last_move()["restored"]), 1)
        self.assertEqual(len(second.undo_last_move()["restored"]), 1)


class TestCliUndo(TempCase):

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        super().setUp()
        write(os.path.join(self.root, "a.mp3"), b"A")
        write(os.path.join(self.root, "b.mp3"), b"B")
        self.dest = os.path.join(self.outside, "sorted")

    def test_undo_with_outside_destination_explains_and_allow_folder_fixes_it(self):
        code, out, err = self.run_cli("move", self.root, "--dest", self.dest, "--yes", "--quiet")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(len(os.listdir(self.dest)), 2)

        code, out, err = self.run_cli("undo", self.root)
        self.assertEqual(code, 1)
        self.assertIn("--allow-folder", err)
        self.assertEqual(len(os.listdir(self.dest)), 2)        # nothing was moved

        code, out, err = self.run_cli("undo", self.root, "--allow-folder", self.dest)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(os.listdir(self.dest), [])
        self.assertEqual(read(os.path.join(self.root, "a.mp3")), b"A")

    def test_move_prints_the_right_undo_command(self):
        code, out, err = self.run_cli("move", self.root, "--dest", self.dest, "--yes")
        self.assertIn("--allow-folder", out)
        shutil.rmtree(os.path.join(self.root, JOURNAL_DIR_NAME), ignore_errors=True)
        inside = os.path.join(self.root, "inside_dest")
        write(os.path.join(self.root, "c.mp3"))
        code, out, err = self.run_cli("move", self.root, "--dest", inside, "--yes")
        self.assertNotIn("--allow-folder", out)

    def test_normal_undo_without_extra_options(self):
        self.run_cli("move", self.root, "--yes", "--quiet")
        code, out, err = self.run_cli("undo", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(read(os.path.join(self.root, "a.mp3")), b"A")


if __name__ == "__main__":
    unittest.main()
