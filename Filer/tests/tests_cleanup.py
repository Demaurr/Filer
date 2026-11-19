"""
Tests for EmptyFolderFinder and JunkFinder (Python API) and the `filer empty` / `filer junk` commands.

Every folder and file is created by the tests in their own temporary folder. Several tests also make
os.remove, os.unlink and shutil.rmtree fail on purpose to prove that no file is ever deleted.
"""
import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import Filing
from Filing import EmptyFolderFinder, JunkFinder, JUNK_PATTERNS, cli
from Filing.cleanup import KEEP_EMPTY_NAMES
from Filing.journal import JOURNAL_DIR_NAME, list_journals, load_journal, undo_moves
from tests_paths_safety import make_dir_link  # helper: folder link (symlink or Windows junction)

OLD = 1_600_000_000
HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE_PARENT = os.path.dirname(HERE)


def write(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    os.utime(path, (mtime, mtime))
    return path


def make_dirs(*paths):
    for path in paths:
        os.makedirs(path, exist_ok=True)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="filer_cleanup_test_"))
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def relative(self, paths):
        return sorted(os.path.relpath(p, self.root).replace(os.sep, "/") for p in paths)

    @contextlib.contextmanager
    def deletion_forbidden(self):
        """Any attempt to delete a file or a whole tree raises, so a test passes only if none happens."""
        def boom(*args, **kwargs):
            raise AssertionError("a file was deleted")
        with mock.patch("os.remove", boom), mock.patch("os.unlink", boom), mock.patch("shutil.rmtree", boom):
            yield


class TestEmptyFolders(Base):

    def finder(self, **kw):
        kw.setdefault("verbose", False)
        return EmptyFolderFinder(self.root, **kw)

    def test_finds_nested_empty_folders(self):
        make_dirs(self.p("a", "b", "c"), self.p("d"))
        self.assertEqual(self.relative(self.finder().find()), ["a", "a/b", "a/b/c", "d"])

    def test_folder_with_a_file_is_not_empty_nor_are_its_parents(self):
        write(self.p("a", "b", "file.txt"))
        make_dirs(self.p("a", "empty"), self.p("a", "b", "c"))
        self.assertEqual(self.relative(self.finder().find()), ["a/b/c", "a/empty"])

    def test_zero_byte_file_still_counts_as_a_file(self):
        write(self.p("a", "zero.txt"), b"")
        self.assertEqual(self.finder().find(), [])

    def test_hidden_files_count_as_files(self):
        write(self.p("a", ".hidden"))
        self.assertEqual(self.finder().find(), [])

    def test_root_is_not_listed_unless_asked(self):
        make_dirs(self.p("a"))
        self.assertNotIn(self.root, self.finder().find())
        self.assertIn(self.root, self.finder(include_root=True).find())

    def test_empty_root_gives_nothing_by_default(self):
        self.assertEqual(self.finder().find(), [])

    def test_missing_folder(self):
        with self.assertRaises(FileNotFoundError):
            EmptyFolderFinder(os.path.join(self.tmp, "nope"))

    def test_skipped_folders_count_as_content(self):
        make_dirs(self.p("keepme", "inner"), self.p("other"))
        found = self.relative(self.finder(skip_folders=["keepme"]).find())
        self.assertEqual(found, ["other"])  # keepme is not listed, nor is inner (never looked at)

    def test_ignored_folders_count_as_content(self):
        make_dirs(self.p("build", "x"), self.p("plain"))
        found = self.relative(self.finder(exclude=["build/"]).find())
        self.assertEqual(found, ["plain"])

    def test_filerignore_file_is_used(self):
        write(self.p(".filerignore"), b"build/\n")
        make_dirs(self.p("build"), self.p("plain"))
        self.assertEqual(self.relative(self.finder().find()), ["plain"])
        self.assertEqual(self.relative(self.finder(ignore_file=False).find()), ["build", "plain"])

    def test_journal_folder_is_never_listed(self):
        make_dirs(self.p(JOURNAL_DIR_NAME, "sub"), self.p("a"))
        self.assertEqual(self.relative(self.finder().find()), ["a"])

    def test_links_are_content_and_are_never_followed(self):
        make_dirs(self.p("real_empty"), self.p("holder"))
        if not make_dir_link(self.p("holder", "link"), self.p("real_empty")):
            self.skipTest("cannot create folder links here")
        found = self.relative(self.finder().find())
        self.assertEqual(found, ["real_empty"])  # holder holds a link, so it is not empty

    def test_unreadable_folder_is_not_called_empty(self):
        make_dirs(self.p("locked"), self.p("fine"))
        real = os.scandir

        def scandir(path):
            if os.path.basename(str(path)) == "locked":
                raise PermissionError("denied")
            return real(path)

        finder = self.finder()
        with mock.patch("os.scandir", scandir):
            found = self.relative(finder.find())
        self.assertEqual(found, ["fine"])
        self.assertEqual(len(finder.errors), 1)

    def test_to_dicts_and_summary(self):
        make_dirs(self.p("a"))
        finder = self.finder()
        self.assertEqual(finder.to_dicts(), [{"path": self.p("a"), "relative": "a"}])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            finder.print_summary()
        self.assertIn("Empty folders: 1", out.getvalue())

    def test_find_does_not_change_anything(self):
        make_dirs(self.p("a", "b"))
        self.finder().find()
        self.assertTrue(os.path.isdir(self.p("a", "b")))
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_large_tree_is_quick(self):
        for n in range(1500):
            make_dirs(self.p(f"d{n // 50}", f"e{n}"))
        start = time.perf_counter()
        found = self.finder().find()
        self.assertEqual(len(found), 1500 + 30)
        self.assertLess(time.perf_counter() - start, 10)


class TestRemovingEmptyFolders(Base):

    def finder(self, **kw):
        kw.setdefault("verbose", False)
        return EmptyFolderFinder(self.root, **kw)

    def test_dry_run_changes_nothing(self):
        make_dirs(self.p("a", "b"))
        finder = self.finder()
        planned = finder.remove(dry_run=True)
        self.assertEqual(self.relative(planned), ["a", "a/b"])
        self.assertEqual(planned[0], self.p("a", "b"))  # deepest first
        self.assertTrue(os.path.isdir(self.p("a", "b")))
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_remove_deepest_first_and_leave_files_alone(self):
        make_dirs(self.p("a", "b", "c"), self.p("d"))
        write(self.p("keep", "f.txt"), b"KEEP")
        write(self.p("top.txt"), b"TOP")
        finder = self.finder()
        with self.deletion_forbidden():
            removed = finder.remove()
        self.assertEqual(self.relative(removed), ["a", "a/b", "a/b/c", "d"])
        self.assertFalse(os.path.exists(self.p("a")))
        self.assertFalse(os.path.exists(self.p("d")))
        with open(self.p("keep", "f.txt"), "rb") as f:
            self.assertEqual(f.read(), b"KEEP")
        self.assertTrue(os.path.exists(self.p("top.txt")))
        self.assertEqual(finder.folders, [])

    def test_removing_twice_is_harmless(self):
        make_dirs(self.p("a"))
        finder = self.finder()
        finder.remove()
        self.assertEqual(finder.remove(), [])
        self.assertEqual(self.finder().find(), [])

    def test_a_folder_that_got_a_file_meanwhile_is_kept(self):
        make_dirs(self.p("a"), self.p("b"))
        finder = self.finder()
        finder.find()
        write(self.p("a", "late.txt"), b"LATE")  # appears after the search
        removed = finder.remove()
        self.assertEqual(self.relative(removed), ["b"])
        self.assertTrue(os.path.exists(self.p("a", "late.txt")))
        self.assertTrue(any(f == self.p("a") for f, _ in finder.errors))

    def test_remove_uses_rmdir_only(self):
        make_dirs(self.p("a"))
        finder = self.finder()
        with mock.patch("os.rmdir", wraps=os.rmdir) as rmdir, self.deletion_forbidden():
            finder.remove()
        self.assertEqual(rmdir.call_count, 1)

    def test_journal_is_written_and_undo_recreates_the_folders(self):
        make_dirs(self.p("a", "b", "c"), self.p("d"))
        finder = self.finder()
        finder.remove()
        self.assertTrue(finder.last_journal_path)
        data = load_journal(finder.last_journal_path)
        self.assertEqual(data["operation"], "empty")
        self.assertEqual(len(data["removed_dirs"]), 4)
        result = finder.undo_last_removal()
        self.assertEqual(len(result["recreated"]), 4)
        for folder in ("a", "d"):
            self.assertTrue(os.path.isdir(self.p(folder)))
        self.assertTrue(os.path.isdir(self.p("a", "b", "c")))
        self.assertTrue(load_journal(finder.last_journal_path)["undone"])

    def test_undo_dry_run_creates_nothing(self):
        make_dirs(self.p("a"))
        finder = self.finder()
        finder.remove()
        result = finder.undo_last_removal(dry_run=True)
        self.assertEqual(len(result["recreated"]), 1)
        self.assertFalse(os.path.exists(self.p("a")))
        self.assertFalse(load_journal(finder.last_journal_path)["undone"])

    def test_undo_through_the_function_and_a_fresh_object(self):
        make_dirs(self.p("a"))
        self.finder().remove()
        result = undo_moves(None, root_folder=self.root)
        self.assertEqual(len(result["recreated"]), 1)
        self.assertTrue(os.path.isdir(self.p("a")))

    def test_without_journal_nothing_is_recorded(self):
        make_dirs(self.p("a"))
        finder = self.finder(journal=False)
        finder.remove()
        self.assertIsNone(finder.last_journal_path)
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_custom_journal_dir(self):
        make_dirs(self.p("a"))
        journal_dir = os.path.join(self.tmp, "my_journals")
        finder = self.finder(journal_dir=journal_dir)
        finder.remove()
        self.assertEqual(len(list_journals(journal_dir)), 1)
        finder.undo_last_removal()
        self.assertTrue(os.path.isdir(self.p("a")))

    def test_nothing_to_remove_writes_no_journal(self):
        finder = self.finder()
        self.assertEqual(finder.remove(), [])
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_include_root_removes_the_root_last(self):
        make_dirs(self.p("a"))
        finder = self.finder(include_root=True, journal=False)
        removed = finder.remove()
        self.assertEqual(removed[-1], self.root)
        self.assertFalse(os.path.exists(self.root))

    def test_a_tampered_journal_cannot_recreate_folders_elsewhere(self):
        make_dirs(self.p("a"))
        finder = self.finder()
        finder.remove()
        data = load_journal(finder.last_journal_path)
        outside = os.path.join(self.tmp, "outside_made_by_journal")
        data["removed_dirs"].append(outside)
        with open(finder.last_journal_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        result = undo_moves(finder.last_journal_path, root_folder=self.root)
        self.assertFalse(os.path.exists(outside))
        self.assertEqual(len(result["recreated"]), 1)
        self.assertTrue(any("not recreated" in item["reason"] for item in result["skipped"]))
        self.assertFalse(load_journal(finder.last_journal_path).get("undone"))

    def test_progress_is_reported(self):
        make_dirs(self.p("a", "b"))
        calls = []
        EmptyFolderFinder(self.root, progress=lambda *a: calls.append(a), verbose=False).find()
        self.assertTrue(calls)
        self.assertEqual(calls[-1][1], calls[-1][2])


class TestJunkFinder(Base):

    def finder(self, **kw):
        kw.setdefault("verbose", False)
        return JunkFinder(self.root, **kw)

    def names(self, records):
        return sorted(os.path.relpath(r["Path"], self.root).replace(os.sep, "/") for r in records)

    def make_junk(self):
        for name in ("Thumbs.db", "desktop.ini", ".DS_Store", "._photo.jpg", "~$report.docx", "scratch.tmp",
                     "notes.txt~", "ehthumbs.db", ".~lock.doc.odt#", "scratch.TEMP"):
            write(self.p("pics", name), b"junk data")
        write(self.p("pics", "photo.jpg"), b"photo")
        write(self.p("report.docx"), b"report")

    def test_default_patterns(self):
        self.make_junk()
        found = self.names(self.finder(kinds="junk").find())
        self.assertEqual(found, sorted("pics/" + n for n in (
            "Thumbs.db", "desktop.ini", ".DS_Store", "._photo.jpg", "~$report.docx", "scratch.tmp", "notes.txt~",
            "ehthumbs.db", ".~lock.doc.odt#", "scratch.TEMP")))

    def test_names_are_matched_without_regard_to_case(self):
        write(self.p("THUMBS.DB"))
        write(self.p("Desktop.INI"))
        self.assertEqual(len(self.finder(kinds="junk").find()), 2)

    def test_normal_files_are_never_junk(self):
        for name in ("photo.jpg", "report.docx", "thumbs.png", "my.tmpl", "tmp.txt", "desk.ini.txt", "notes.txt", "~notes"):
            write(self.p(name))
        self.assertEqual(self.finder().find(), [])

    def test_zero_byte_files(self):
        write(self.p("empty1.dat"), b"")
        write(self.p("sub", "empty2.log"), b"")
        write(self.p("full.dat"), b"data")
        records = self.finder(kinds="zero_byte").find()
        self.assertEqual(self.names(records), ["empty1.dat", "sub/empty2.log"])
        self.assertTrue(all(r["Kind"] == "zero_byte" for r in records))

    def test_expected_empty_files_are_not_reported(self):
        for name in KEEP_EMPTY_NAMES:
            write(self.p("pkg", name), b"")
        write(self.p("pkg", "__INIT__.PY"), b"")
        write(self.p("pkg", "reallyempty.txt"), b"")
        self.assertEqual(self.names(self.finder().find()), ["pkg/reallyempty.txt"])

    def test_keep_empty_names_can_be_changed(self):
        write(self.p("__init__.py"), b"")
        self.assertEqual(self.names(self.finder(keep_empty_names=[]).find()), ["__init__.py"])

    def test_a_file_that_is_both_is_junk(self):
        write(self.p("Thumbs.db"), b"")
        records = self.finder().find()
        self.assertEqual([r["Kind"] for r in records], ["junk"])

    def test_kinds(self):
        write(self.p("Thumbs.db"), b"x")
        write(self.p("zero.dat"), b"")
        self.assertEqual({r["Kind"] for r in self.finder().find()}, {"junk", "zero_byte"})
        self.assertEqual({r["Kind"] for r in self.finder(kinds=["junk"]).find()}, {"junk"})
        self.assertEqual({r["Kind"] for r in self.finder(kinds=("zero_byte",)).find()}, {"zero_byte"})
        for bad in ([], ["other"], ["junk", "x"], ()):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.finder(kinds=bad)

    def test_custom_and_extra_patterns(self):
        write(self.p("a.bak"))
        write(self.p("Thumbs.db"))
        only_custom = self.finder(patterns=["*.bak"], kinds="junk").find()
        self.assertEqual(self.names(only_custom), ["a.bak"])
        both = self.finder(extra_patterns="*.bak", kinds="junk").find()
        self.assertEqual(self.names(both), ["Thumbs.db", "a.bak"])
        none = self.finder(patterns=[], kinds="junk").find()
        self.assertEqual(none, [])
        for bad in ([5], [""], 5):
            with self.assertRaises((ValueError, TypeError), msg=repr(bad)):
                self.finder(patterns=bad)

    def test_default_pattern_list_is_exposed(self):
        self.assertIn("Thumbs.db", JUNK_PATTERNS)
        self.assertIn("desktop.ini", JUNK_PATTERNS)
        self.assertIs(Filing.JUNK_PATTERNS, JUNK_PATTERNS)

    def test_properties_and_dicts(self):
        write(self.p("Thumbs.db"), b"12345")
        write(self.p("zero.dat"), b"")
        finder = self.finder()
        self.assertEqual(len(finder.junk), 1)
        self.assertEqual(len(finder.zero_byte), 1)
        self.assertEqual(finder.total_bytes, 5)
        self.assertEqual(sorted(d["kind"] for d in finder.to_dicts()), ["junk", "zero_byte"])
        self.assertEqual({d["size"] for d in finder.to_dicts()}, {0, 5})

    def test_results_are_sorted_and_repeatable(self):
        self.make_junk()
        first = [r["Path"] for r in self.finder().find()]
        self.assertEqual(first, sorted(first))
        self.assertEqual(first, [r["Path"] for r in self.finder().find()])

    def test_skip_folders_and_ignore_rules(self):
        write(self.p("skipme", "Thumbs.db"))
        write(self.p("build", "Thumbs.db"))
        write(self.p("keep", "Thumbs.db"))
        found = self.names(self.finder(skip_folders=["skipme"], exclude=["build/"]).find())
        self.assertEqual(found, ["keep/Thumbs.db"])

    def test_the_junk_folder_itself_is_not_searched(self):
        write(self.p("Junk", "Thumbs.db"))
        write(self.p("a", "Thumbs.db"))
        self.assertEqual(self.names(self.finder().find()), ["a/Thumbs.db"])

    def test_csv_report(self):
        write(self.p("Thumbs.db"), b"12")
        out = os.path.join(self.tmp, "junk.csv")
        finder = self.finder()
        self.assertEqual(finder.generate_csv(out), out)
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["Path", "Kind", "Size (Bytes)", "Modification Date"])
        self.assertEqual(rows[1][:3], [self.p("Thumbs.db"), "junk", "2"])

    def test_find_changes_nothing(self):
        self.make_junk()
        before = sorted(os.listdir(self.p("pics")))
        self.finder().find()
        self.assertEqual(sorted(os.listdir(self.p("pics"))), before)
        self.assertFalse(os.path.exists(self.p("Junk")))
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))


class TestMovingJunk(Base):

    def setUp(self):
        super().setUp()
        write(self.p("a", "Thumbs.db"), b"A-thumbs")
        write(self.p("b", "Thumbs.db"), b"B-thumbs")        # same name in another folder
        write(self.p("b", "zero.dat"), b"")
        write(self.p("good.txt"), b"GOOD")

    def finder(self, **kw):
        kw.setdefault("verbose", False)
        return JunkFinder(self.root, **kw)

    def test_dry_run_changes_nothing(self):
        plan = self.finder().move(dry_run=True)
        self.assertEqual(len(plan), 3)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))
        self.assertFalse(os.path.exists(self.p("Junk")))
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_move_keeps_the_folder_layout_and_never_deletes(self):
        finder = self.finder()
        with self.deletion_forbidden():
            moved = finder.move()
        self.assertEqual(len(moved), 3)
        for rel, content in (("a/Thumbs.db", b"A-thumbs"), ("b/Thumbs.db", b"B-thumbs")):
            path = self.p("Junk", *rel.split("/"))
            with open(path, "rb") as f:
                self.assertEqual(f.read(), content)
        self.assertTrue(os.path.exists(self.p("Junk", "b", "zero.dat")))
        self.assertFalse(os.path.exists(self.p("a", "Thumbs.db")))
        with open(self.p("good.txt"), "rb") as f:
            self.assertEqual(f.read(), b"GOOD")

    def test_undo_restores_everything(self):
        finder = self.finder()
        finder.move()
        result = finder.undo_last_move()
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(result["skipped"], [])
        with open(self.p("a", "Thumbs.db"), "rb") as f:
            self.assertEqual(f.read(), b"A-thumbs")
        self.assertFalse(os.path.exists(self.p("Junk")))  # the folder made for the move is removed again

    def test_undo_from_another_object(self):
        self.finder().move()
        result = undo_moves(None, root_folder=self.root)
        self.assertEqual(len(result["restored"]), 3)

    def test_moving_again_finds_nothing_new(self):
        finder = self.finder()
        finder.move()
        again = self.finder()
        self.assertEqual(again.find(), [])
        self.assertEqual(again.move(), {})

    def test_custom_destination_outside_the_root_can_be_undone(self):
        dest = os.path.join(self.tmp, "junkyard")
        finder = self.finder(destination_folder=dest)
        moved = finder.move()
        self.assertTrue(all(v.startswith(dest) for v in moved.values()))
        self.assertEqual(len(finder.undo_last_move()["restored"]), 3)

    def test_destination_given_to_move(self):
        dest = os.path.join(self.tmp, "elsewhere")
        finder = self.finder()
        moved = finder.move(destination_folder=dest)
        self.assertTrue(all(v.startswith(dest) for v in moved.values()))
        self.assertEqual(len(finder.undo_last_move()["restored"]), 3)

    def test_name_clash_in_destination_is_renamed_not_overwritten(self):
        write(self.p("Junk", "a", "Thumbs.db"), b"ALREADY THERE")
        finder = self.finder()
        finder.find()
        write(self.p("a", "Thumbs.db"), b"A-thumbs")
        moved = finder.move()
        with open(self.p("Junk", "a", "Thumbs.db"), "rb") as f:
            self.assertEqual(f.read(), b"ALREADY THERE")
        self.assertEqual(len(moved), 3)

    def test_without_journal(self):
        finder = self.finder(journal=False)
        finder.move()
        self.assertIsNone(finder.last_journal_path)
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_nothing_to_move(self):
        shutil.rmtree(self.p("a"))
        shutil.rmtree(self.p("b"))
        self.assertEqual(self.finder().move(), {})


class Cli(Base):

    def run_cli(self, *argv, stdin_tty=False):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv) + ["--no-progress"])
        return code, out.getvalue(), err.getvalue()


class TestEmptyCommand(Cli):

    def setUp(self):
        super().setUp()
        make_dirs(self.p("a", "b"), self.p("d"))
        write(self.p("keep", "f.txt"))

    def test_lists_by_default_and_changes_nothing(self):
        code, out, err = self.run_cli("empty", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertIn("Empty folders: 3", out)
        self.assertTrue(os.path.isdir(self.p("a", "b")))

    def test_json_listing(self):
        code, out, err = self.run_cli("empty", self.root, "--json")
        data = json.loads(out)
        self.assertEqual(data["count"], 3)
        self.assertEqual(sorted(os.path.basename(p) for p in data["empty_folders"]), ["a", "b", "d"])

    def test_nothing_found(self):
        shutil.rmtree(self.p("a"))
        shutil.rmtree(self.p("d"))
        code, out, err = self.run_cli("empty", self.root)
        self.assertEqual(code, 0)
        self.assertIn("No empty folders", out)

    def test_dry_run_with_remove(self):
        code, out, err = self.run_cli("empty", self.root, "--remove", "--dry-run")
        self.assertEqual(code, 0, msg=err)
        self.assertIn("DRY RUN: 3 empty folder(s) would be removed", out)
        self.assertTrue(os.path.isdir(self.p("a", "b")))

    def test_remove_needs_confirmation_when_not_interactive(self):
        code, out, err = self.run_cli("empty", self.root, "--remove")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertTrue(os.path.isdir(self.p("a", "b")))

    def test_remove_with_yes_and_undo(self):
        code, out, err = self.run_cli("empty", self.root, "--remove", "--yes")
        self.assertEqual(code, 0, msg=err)
        self.assertIn("Removed 3 empty folder(s)", out)
        self.assertIn("filer undo", out)
        self.assertFalse(os.path.exists(self.p("a")))
        self.assertTrue(os.path.exists(self.p("keep", "f.txt")))
        code, out, err = self.run_cli("undo", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertIn("Recreated 3 empty folder(s)", out)
        self.assertTrue(os.path.isdir(self.p("a", "b")))

    def test_remove_json(self):
        code, out, err = self.run_cli("empty", self.root, "--remove", "--yes", "--json")
        data = json.loads(out)
        self.assertEqual(len(data["removed"]), 3)
        self.assertEqual(data["failed"], [])
        self.assertTrue(data["journal"])

    def test_no_journal_option(self):
        code, out, err = self.run_cli("empty", self.root, "--remove", "--yes", "--no-journal")
        self.assertEqual(code, 0)
        self.assertNotIn("Undo with", out)
        self.assertFalse(os.path.exists(self.p(JOURNAL_DIR_NAME)))

    def test_skip_and_exclude_options(self):
        code, out, err = self.run_cli("empty", self.root, "--json", "--skip", "a", "--exclude", "d/")
        self.assertEqual(json.loads(out)["count"], 0)

    def test_bad_folder(self):
        code, out, err = self.run_cli("empty", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1)
        self.assertIn("filer: error", err)

    def test_undo_list_shows_the_empty_run(self):
        self.run_cli("empty", self.root, "--remove", "--yes")
        code, out, err = self.run_cli("undo", self.root, "--list")
        self.assertIn("empty", out)


class TestJunkCommand(Cli):

    def setUp(self):
        super().setUp()
        write(self.p("a", "Thumbs.db"), b"12345")
        write(self.p("b", "zero.dat"), b"")
        write(self.p("good.txt"), b"GOOD")

    def test_report_only_by_default(self):
        code, out, err = self.run_cli("junk", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertIn("Junk files (by name): 1", out)
        self.assertIn("Zero-byte files: 1", out)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))
        self.assertFalse(os.path.exists(self.p("Junk")))

    def test_json_report(self):
        code, out, err = self.run_cli("junk", self.root, "--json")
        data = json.loads(out)
        self.assertEqual((data["junk_count"], data["zero_byte_count"], data["total_bytes"]), (1, 1, 5))
        self.assertEqual(sorted(f["kind"] for f in data["files"]), ["junk", "zero_byte"])

    def test_kinds_option(self):
        code, out, err = self.run_cli("junk", self.root, "--json", "--kinds", "junk")
        data = json.loads(out)
        self.assertEqual((data["junk_count"], data["zero_byte_count"]), (1, 0))
        with self.assertRaises(SystemExit) as raised:  # argparse rejects an unknown kind with exit code 2
            self.run_cli("junk", self.root, "--json", "--kinds", "nonsense")
        self.assertEqual(raised.exception.code, 2)

    def test_pattern_options(self):
        write(self.p("old.bak"), b"bak")
        code, out, err = self.run_cli("junk", self.root, "--json", "--kinds", "junk", "--pattern", "*.bak")
        self.assertEqual(json.loads(out)["junk_count"], 2)
        code, out, err = self.run_cli("junk", self.root, "--json", "--kinds", "junk", "--no-default-patterns",
                                      "--pattern", "*.bak")
        self.assertEqual(json.loads(out)["junk_count"], 1)

    def test_no_findings(self):
        os.remove(self.p("a", "Thumbs.db"))
        os.remove(self.p("b", "zero.dat"))
        code, out, err = self.run_cli("junk", self.root)
        self.assertIn("No junk or zero-byte files found.", out)

    def test_csv_option(self):
        out_csv = os.path.join(self.tmp, "report.csv")
        code, out, err = self.run_cli("junk", self.root, "--csv", out_csv)
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(os.path.exists(out_csv))

    def test_move_dry_run(self):
        code, out, err = self.run_cli("junk", self.root, "--move", "--dry-run")
        self.assertEqual(code, 0, msg=err)
        self.assertIn("DRY RUN: 2 file(s) would be moved", out)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))

    def test_move_needs_confirmation_when_not_interactive(self):
        code, out, err = self.run_cli("junk", self.root, "--move")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))

    def test_move_and_undo(self):
        code, out, err = self.run_cli("junk", self.root, "--move", "--yes")
        self.assertEqual(code, 0, msg=err)
        self.assertIn("Moved 2 file(s)", out)
        self.assertTrue(os.path.exists(self.p("Junk", "a", "Thumbs.db")))
        self.assertTrue(os.path.exists(self.p("good.txt")))
        code, out, err = self.run_cli("undo", self.root)
        self.assertEqual(code, 0, msg=err)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))

    def test_move_to_another_folder_prints_the_matching_undo_command(self):
        dest = os.path.join(self.tmp, "junkyard")
        code, out, err = self.run_cli("junk", self.root, "--move", dest, "--yes")
        self.assertEqual(code, 0, msg=err)
        self.assertIn("--allow-folder", out)
        self.assertEqual(self.run_cli("undo", self.root)[0], 1)  # refused: outside the scanned folder
        self.assertEqual(self.run_cli("undo", self.root, "--allow-folder", dest)[0], 0)
        self.assertTrue(os.path.exists(self.p("a", "Thumbs.db")))

    def test_move_json(self):
        code, out, err = self.run_cli("junk", self.root, "--move", "--yes", "--json")
        data = json.loads(out)
        self.assertEqual(len(data["moved"]), 2)

    def test_exclude_option(self):
        code, out, err = self.run_cli("junk", self.root, "--json", "--exclude", "a/")
        self.assertEqual(json.loads(out)["junk_count"], 0)


class TestRunAsCommandAndImport(unittest.TestCase):
    """The same features must work from a real command line (python -m Filing) and from an import."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="filer_cmd_test_"))
        os.makedirs(os.path.join(self.tmp, "empty_one"))
        write(os.path.join(self.tmp, "Thumbs.db"), b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_module(self, *args):
        env = dict(os.environ, PYTHONPATH=PACKAGE_PARENT)
        return subprocess.run([sys.executable, "-m", "Filing", *args, "--no-progress"], capture_output=True, text=True,
                              env=env, timeout=60)

    def test_commands_are_listed(self):
        result = subprocess.run([sys.executable, "-m", "Filing", "--help"], capture_output=True, text=True,
                                env=dict(os.environ, PYTHONPATH=PACKAGE_PARENT), timeout=60)
        for command in ("stats", "move", "undo", "dupes", "empty", "junk"):
            self.assertIn(command, result.stdout)

    def test_empty_and_junk_as_real_commands(self):
        result = self.run_module("empty", self.tmp, "--json")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(json.loads(result.stdout)["count"], 1)
        result = self.run_module("junk", self.tmp, "--json")
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(json.loads(result.stdout)["junk_count"], 1)

    def test_remove_without_yes_in_a_real_process_refuses(self):
        result = self.run_module("empty", self.tmp, "--remove")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "empty_one")))

    def test_same_features_from_an_import(self):
        self.assertTrue(callable(Filing.EmptyFolderFinder))
        finder = Filing.EmptyFolderFinder(self.tmp, verbose=False)
        self.assertEqual(len(finder.find()), 1)
        junk = Filing.JunkFinder(self.tmp, verbose=False)
        self.assertEqual(len(junk.find()), 1)
        for name in ("EmptyFolderFinder", "JunkFinder", "IgnoreRules", "JUNK_PATTERNS"):
            self.assertIn(name, Filing.__all__)

    def test_packaging_declares_the_filer_command_and_one_version_source(self):
        with open(os.path.join(PACKAGE_PARENT, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('filer = "Filing.cli:main"', text)
        self.assertIn('version = { attr = "Filing.__version__" }', text)
        for version in ("3.9", "3.10", "3.11", "3.12", "3.13"):
            self.assertIn(f"Python :: {version}", text)
        with open(os.path.join(PACKAGE_PARENT, "setup.py"), encoding="utf-8") as f:  # only a shim: no second copy of the data
            shim = f.read()
        self.assertIn("setup()", shim)
        self.assertNotIn("version=", shim)
        self.assertNotIn("name=", shim)
        self.assertRegex(Filing.__version__, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
