import contextlib
import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from Filing import __version__
from Filing.cli import main
from Filing.filestatser import FileStatsCollector
from Filing.journal import JOURNAL_DIR_NAME

OLD = 1_500_000_000
PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the folder that contains Filing/


def make_file(path, content=b"x", mtime=OLD):
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


def run(*argv):
    """Run the CLI in-process. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(list(argv))
        except SystemExit as e:  # argparse errors / --version
            code = e.code if isinstance(e.code, int) else 0
    return code, out.getvalue(), err.getvalue()


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        make_file(os.path.join(self.root, "song.mp3"), b"1")
        make_file(os.path.join(self.root, "a", "photo.jpg"), b"22" * 1000, 1_610_000_000)
        make_file(os.path.join(self.root, "a", "b", "clip.mp4"), b"333")
        make_file(os.path.join(self.root, "x", "song.mp3"), b"4444")
        make_file(os.path.join(self.root, "a", "notes.txt"), b"text")
        make_file(os.path.join(self.root, "skipme", "hidden.jpg"), b"h")
        self.before = tree(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def interactive(self, answer):
        return mock.patch.multiple("Filing.cli", _is_interactive=lambda: True), mock.patch("builtins.input", return_value=answer)


class TestGeneral(Base):

    def test_version(self):
        code, out, _ = run("--version")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), f"filer {__version__}")

    def test_no_command_is_a_usage_error(self):
        code, _, err = run()
        self.assertEqual(code, 2)
        self.assertIn("required", err)

    def test_unknown_command_and_option(self):
        self.assertEqual(run("explode", self.root)[0], 2)
        self.assertEqual(run("stats", self.root, "--nonsense")[0], 2)

    def test_help_lists_commands(self):
        code, out, _ = run("--help")
        self.assertEqual(code, 0)
        for word in ("stats", "move", "undo", "dupes"):
            self.assertIn(word, out)

    def test_missing_folder_is_a_clean_error(self):
        code, out, err = run("stats", os.path.join(self.tmp, "nope"))
        self.assertEqual(code, 1)
        self.assertIn("filer: error:", err)
        self.assertNotIn("Traceback", err)

    def test_package_version_has_one_source(self):
        with open(os.path.join(PACKAGE_PARENT, "pyproject.toml"), encoding="utf-8") as f:
            self.assertIn('version = { attr = "Filing.__version__" }', f.read())
        code, out, _ = run("--version")
        self.assertEqual(out.strip(), f"filer {__version__}")


class TestStats(Base):

    def test_prints_summary(self):
        code, out, _ = run("stats", self.root, "--all-files", "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("Total Files: 6", out)

    def test_media_only_by_default(self):
        code, out, _ = run("stats", self.root, "--no-progress")
        self.assertIn("Total Files: 5", out)  # notes.txt excluded

    def test_ext_option_forms(self):
        for args in (["--ext", ".txt"], ["--ext", "txt"], ["--ext", ".txt,.mp4"]):
            code, out, _ = run("stats", self.root, "--no-progress", *args)
            self.assertEqual(code, 0)
        self.assertIn("Total Files: 1", run("stats", self.root, "--no-progress", "--ext", "txt")[1])
        self.assertIn("Total Files: 2", run("stats", self.root, "--no-progress", "--ext", ".txt,.mp4")[1])

    def test_skip_is_exact(self):
        out = run("stats", self.root, "--all-files", "--no-progress", "--skip", "skipme")[1]
        self.assertIn("Total Files: 5", out)
        out = run("stats", self.root, "--all-files", "--no-progress", "--skip", "skip")[1]
        self.assertIn("Total Files: 6", out)  # 'skip' is not an exact folder name

    def test_filters(self):
        self.assertIn("Total Files: 1", run("stats", self.root, "--all-files", "--no-progress", "--min-size", "1KB")[1])
        self.assertIn("Total Files: 5", run("stats", self.root, "--all-files", "--no-progress", "--max-size", "1KB")[1])
        self.assertIn("Total Files: 1", run("stats", self.root, "--all-files", "--no-progress", "--modified-after", "2020-01-01")[1])
        self.assertIn("Total Files: 5", run("stats", self.root, "--all-files", "--no-progress", "--modified-before", "2020-01-01")[1])
        self.assertIn("Total Files: 2", run("stats", self.root, "--all-files", "--no-progress", "--glob", "*.mp3")[1])
        self.assertIn("Total Files: 1", run("stats", self.root, "--all-files", "--no-progress", "--regex", "^clip")[1])

    def test_bad_filter_value_is_a_clean_error(self):
        code, _, err = run("stats", self.root, "--no-progress", "--min-size", "lots")
        self.assertEqual(code, 1)
        self.assertIn("filer: error:", err)

    def test_extra_distributions(self):
        out = run("stats", self.root, "--all-files", "--no-progress", "--types", "--sizes", "--folders")[1]
        self.assertIn("File Types Distribution", out)
        self.assertIn("File Size Distribution", out)
        self.assertIn("Folders Size Distribution", out)

    def test_no_files(self):
        out = run("stats", self.root, "--no-progress", "--ext", ".zzz")[1]
        self.assertIn("No files found.", out)

    def test_csv_to_path(self):
        path = os.path.join(self.tmp, "report.csv")
        code, out, _ = run("stats", self.root, "--all-files", "--no-progress", "--csv", path)
        self.assertEqual(code, 0)
        with open(path, newline="", encoding="utf-8") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 6)

    def test_csv_default_location_is_the_folder(self):
        run("stats", self.root, "--all-files", "--no-progress", "--csv")
        self.assertEqual(len([n for n in os.listdir(self.root) if n.endswith(".csv")]), 1)

    def test_html_reports_do_not_ask_questions(self):
        html = os.path.join(self.tmp, "all.html")
        summary = os.path.join(self.tmp, "sum.html")
        with mock.patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                mock.patch.object(FileStatsCollector, "generate_file_stats_html", autospec=True,
                                  side_effect=FileStatsCollector.generate_file_stats_html) as spy:
            code, _, _ = run("stats", self.root, "--all-files", "--no-progress", "--html", html, "--summary-html", summary)
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(html) and os.path.exists(summary))
        self.assertIs(spy.call_args.kwargs.get("confirm"), False)

    def test_diff(self):
        baseline = os.path.join(self.tmp, "baseline.csv")
        run("stats", self.root, "--all-files", "--no-progress", "--csv", baseline)
        with open(baseline, encoding="utf-8") as f:
            original = f.read()
        os.remove(os.path.join(self.root, "a", "notes.txt"))
        make_file(os.path.join(self.root, "new.txt"), b"new")
        code, _, _ = run("stats", self.root, "--all-files", "--no-progress", "--diff", baseline, "--only-diff")
        self.assertEqual(code, 0)
        with open(baseline, encoding="utf-8") as f:
            self.assertEqual(f.read(), original)  # the old report is never overwritten
        reports = [n for n in os.listdir(self.root) if "difference" in n]
        self.assertEqual(len(reports), 1)
        with open(os.path.join(self.root, reports[0]), newline="", encoding="utf-8") as f:
            tags = {r["File Name"]: r["Tag"] for r in csv.DictReader(f)}
        self.assertEqual(tags, {"notes.txt": "Deleted", "new.txt": "New"})

    def test_diff_with_missing_file(self):
        code, _, err = run("stats", self.root, "--no-progress", "--diff", os.path.join(self.tmp, "none.csv"))
        self.assertEqual(code, 1)
        self.assertIn("not found", err)

    def test_json(self):
        code, out, _ = run("stats", self.root, "--all-files", "--no-progress", "--json")
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(data["total_files"], 6)
        self.assertEqual(data["file_types"][".mp3"], 2)

    def test_json_stdout_stays_valid_when_reports_are_written(self):
        path = os.path.join(self.tmp, "r.csv")
        code, out, err = run("stats", self.root, "--all-files", "--no-progress", "--json", "--csv", path)
        json.loads(out)  # the "report generated" message went to stderr
        self.assertIn("generated", err)

    def test_progress_goes_to_stderr_and_can_be_silenced(self):
        _, out, err = run("stats", self.root, "--all-files")
        self.assertIn("Reading folders", err)
        self.assertNotIn("Reading folders", out)
        _, _, err = run("stats", self.root, "--all-files", "--quiet")
        self.assertEqual(err, "")
        _, _, err = run("stats", self.root, "--all-files", "--no-progress")
        self.assertEqual(err, "")


class TestMove(Base):

    def media_in(self, folder):
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def test_dry_run_changes_nothing(self):
        code, out, _ = run("move", self.root, "--dry-run", "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN: 5 file(s) would be moved", out)
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "media")))
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))

    def test_dry_run_json(self):
        code, out, _ = run("move", self.root, "--dry-run", "--json", "--no-progress", "--organize-by", "type")
        data = json.loads(out)
        self.assertTrue(data["dry_run"])
        self.assertEqual(data["count"], 5)
        self.assertEqual(len(data["moves"]), 5)
        self.assertTrue(any(os.sep + "Audio" + os.sep in dest for dest in data["moves"].values()))
        self.assertEqual(tree(self.root), self.before)

    def test_move_with_yes(self):
        code, out, _ = run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        self.assertEqual(code, 0)
        self.assertIn("Moved 5 file(s)", out)
        self.assertIn("Undo with: filer undo", out)
        self.assertEqual(len(self.media_in(os.path.join(self.root, "media"))), 5)
        self.assertIn("a/notes.txt", tree(self.root))  # not media

    def test_move_json_result(self):
        code, out, _ = run("move", self.root, "--yes", "--json", "--no-progress", "--delay", "0")
        data = json.loads(out)
        self.assertEqual(len(data["moved"]), 5)
        self.assertEqual(data["failed"], [])
        self.assertTrue(os.path.exists(data["journal"]))

    def test_custom_destination_and_organize_by(self):
        dest = os.path.join(self.tmp, "sorted")
        code, _, _ = run("move", self.root, "-d", dest, "--organize-by", "{type}/{year}", "--yes", "--no-progress", "--delay", "0")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(dest)), ["Audio", "Images", "Videos"])
        self.assertTrue(os.path.isdir(os.path.join(dest, "Images", "2021")))  # photo.jpg modified 2021

    def test_date_field_option(self):
        dest = os.path.join(self.tmp, "by_date")
        run("move", self.root, "-d", dest, "--organize-by", "date", "--date-field", "modified", "--yes", "--no-progress", "--delay", "0")
        self.assertIn("2017", os.listdir(dest))  # OLD = 2017

    def test_all_files_ext_skip_and_filters(self):
        run("move", self.root, "--all-files", "--skip", "skipme", "--yes", "--no-progress", "--delay", "0")
        self.assertEqual(len(self.media_in(os.path.join(self.root, "media"))), 5)
        self.assertTrue(os.path.exists(os.path.join(self.root, "skipme", "hidden.jpg")))

    def test_ext_option(self):
        run("move", self.root, "--ext", ".txt", "--yes", "--no-progress", "--delay", "0")
        self.assertEqual(self.media_in(os.path.join(self.root, "media")), ["notes.txt"])

    def test_nothing_to_move(self):
        code, out, _ = run("move", self.root, "--ext", ".zzz", "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("No files to move.", out)

    def test_refuses_to_change_files_without_yes_when_not_interactive(self):
        code, _, err = run("move", self.root, "--no-progress")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(tree(self.root), self.before)

    def test_interactive_no_cancels(self):
        patches = self.interactive("n")
        with patches[0], patches[1]:
            code, out, _ = run("move", self.root, "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("Cancelled", out)
        self.assertEqual(tree(self.root), self.before)

    def test_interactive_yes_moves(self):
        patches = self.interactive("y")
        with patches[0], patches[1]:
            code, out, _ = run("move", self.root, "--no-progress", "--delay", "0")
        self.assertEqual(code, 0)
        self.assertIn("Moved 5 file(s)", out)

    def test_undo_after_move(self):
        run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        code, out, _ = run("undo", self.root, "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("Restored 5 file(s)", out)
        self.assertEqual(tree(self.root), self.before)

    def test_no_journal_means_nothing_to_undo(self):
        run("move", self.root, "--yes", "--no-journal", "--no-progress", "--delay", "0")
        code, _, err = run("undo", self.root)
        self.assertEqual(code, 1)
        self.assertIn("nothing to undo", err)

    def test_custom_journal_dir(self):
        journals = os.path.join(self.tmp, "journals")
        run("move", self.root, "--yes", "--journal-dir", journals, "--no-progress", "--delay", "0")
        self.assertEqual(len(os.listdir(journals)), 1)
        code, out, _ = run("undo", self.root, "--journal-dir", journals)
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.root), self.before)

    def test_bad_arguments(self):
        self.assertEqual(run("move", self.root, "--batch-size", "0", "--yes")[0], 1)
        code, _, err = run("move", self.root, "--organize-by", "{nonsense}", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Unknown field", err)
        self.assertEqual(run("move", self.root, "--date-field", "accessed")[0], 2)

    def test_a_failing_file_gives_exit_code_1_and_the_rest_still_move(self):
        real_move = shutil.move

        def flaky(src, dst, *a, **k):
            if os.path.basename(src) == "clip.mp4":
                raise OSError("disk says no")
            return real_move(src, dst, *a, **k)

        with mock.patch("Filing.mover.shutil.move", side_effect=flaky):
            code, out, err = run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        self.assertEqual(code, 1)
        self.assertIn("Moved 4 file(s)", out)
        self.assertIn("clip.mp4", err)
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "b", "clip.mp4")))

    def test_quiet_prints_only_the_result(self):
        code, out, err = run("move", self.root, "--yes", "--quiet", "--delay", "0")
        self.assertEqual(err, "")
        self.assertEqual(out.strip().splitlines()[0], f"Moved 5 file(s) to {os.path.join(self.root, 'media')}")

    def test_batches_for_many_files(self):
        for i in range(130):
            make_file(os.path.join(self.root, "bulk", f"f{i}.png"))
        code, out, _ = run("move", self.root, "--yes", "--no-progress", "--batch-size", "40", "--delay", "0")
        self.assertEqual(code, 0)
        self.assertIn("Moved 135 file(s)", out)

    def test_interrupt_is_reported_and_exit_130(self):
        with mock.patch("Filing.cli.MediaGatherer", side_effect=KeyboardInterrupt):
            code, _, err = run("move", self.root, "--yes")
        self.assertEqual(code, 130)
        self.assertIn("interrupted", err)


class TestUndo(Base):

    def test_nothing_to_undo(self):
        code, _, err = run("undo", self.root)
        self.assertEqual(code, 1)
        self.assertIn("nothing to undo", err)

    def test_list_journals(self):
        self.assertIn("No journals found.", run("undo", self.root, "--list")[1])
        run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        code, out, _ = run("undo", self.root, "--list")
        self.assertIn("can be undone", out)
        self.assertIn("5 file(s)", out)
        data = json.loads(run("undo", self.root, "--list", "--json")[1])
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["files"], 5)
        self.assertFalse(data[0]["undone"])
        run("undo", self.root)
        self.assertIn("undone", run("undo", self.root, "--list")[1])

    def test_dry_run(self):
        run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        after = tree(self.root)
        code, out, _ = run("undo", self.root, "--dry-run")
        self.assertIn("Would restore 5 file(s)", out)
        self.assertEqual(tree(self.root), after)

    def test_partial_undo_exits_1_and_can_be_repeated(self):
        run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        blocker = make_file(os.path.join(self.root, "song.mp3"), b"new file")
        code, out, err = run("undo", self.root)
        self.assertEqual(code, 1)
        self.assertIn("Restored 4 file(s)", out)
        self.assertIn("original location is occupied", err)
        os.remove(blocker)
        code, out, _ = run("undo", self.root)
        self.assertEqual(code, 0)
        self.assertIn("Restored 1 file(s)", out)

    def test_undo_json(self):
        run("move", self.root, "--yes", "--no-progress", "--delay", "0")
        data = json.loads(run("undo", self.root, "--json")[1])
        self.assertEqual(len(data["restored"]), 5)
        self.assertEqual(data["skipped"], [])

    def test_specific_journal(self):
        code, out, _ = run("move", self.root, "--yes", "--json", "--no-progress", "--delay", "0")
        journal = json.loads(out)["journal"]
        code, out, _ = run("undo", self.root, "--journal", journal)
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.root), self.before)


class TestDupes(Base):

    def setUp(self):
        super().setUp()
        self.dups = os.path.join(self.tmp, "dups")
        make_file(os.path.join(self.dups, "a", "one.txt"), b"same content", 1_500_000_000)
        make_file(os.path.join(self.dups, "b", "two.txt"), b"same content", 1_600_000_000)
        make_file(os.path.join(self.dups, "c", "three.txt"), b"same content", 1_700_000_000)
        make_file(os.path.join(self.dups, "a", "unique.txt"), b"different!!")
        self.dups_before = tree(self.dups)

    def test_summary(self):
        code, out, _ = run("dupes", self.dups, "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("Duplicate groups: 1", out)
        self.assertIn("Extra copies: 2", out)

    def test_no_duplicates(self):
        code, out, _ = run("dupes", self.root, "--no-progress")
        self.assertEqual(code, 0)
        self.assertIn("No duplicates found.", out)

    def test_csv(self):
        path = os.path.join(self.tmp, "d.csv")
        run("dupes", self.dups, "--no-progress", "--csv", path)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["Action"] for r in rows].count("Keep"), 1)
        self.assertEqual(len(rows), 3)

    def test_json(self):
        code, out, _ = run("dupes", self.dups, "--no-progress", "--json")
        data = json.loads(out)
        self.assertEqual(data["duplicate_count"], 2)
        self.assertEqual(len(data["groups"]), 1)
        self.assertEqual(len(data["groups"][0]["files"]), 3)

    def test_move_dry_run(self):
        code, out, _ = run("dupes", self.dups, "--no-progress", "--move", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN: 2 duplicate(s) would be moved", out)
        self.assertEqual(tree(self.dups), self.dups_before)

    def test_move_and_undo(self):
        code, out, _ = run("dupes", self.dups, "--no-progress", "--move", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Moved 2 duplicate(s)", out)
        self.assertEqual(len(os.listdir(os.path.join(self.dups, "Duplicates"))), 2)
        self.assertIn("a/one.txt", tree(self.dups))  # oldest kept
        code, out, _ = run("undo", self.dups)
        self.assertEqual(code, 0)
        self.assertEqual(tree(self.dups), self.dups_before)

    def test_move_to_custom_folder_and_keep_newest(self):
        dest = os.path.join(self.tmp, "elsewhere")
        run("dupes", self.dups, "--no-progress", "--move", dest, "--keep", "newest", "--yes")
        self.assertEqual(len(os.listdir(dest)), 2)
        self.assertIn("c/three.txt", tree(self.dups))

    def test_move_json(self):
        data = json.loads(run("dupes", self.dups, "--no-progress", "--move", "--yes", "--json")[1])
        self.assertEqual(len(data["moved"]), 2)

    def test_refuses_without_yes_when_not_interactive(self):
        code, _, err = run("dupes", self.dups, "--no-progress", "--move")
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        self.assertEqual(tree(self.dups), self.dups_before)

    def test_interactive_confirmation(self):
        patches = self.interactive("n")
        with patches[0], patches[1]:
            code, out, _ = run("dupes", self.dups, "--no-progress", "--move")
        self.assertIn("Cancelled", out)
        self.assertEqual(tree(self.dups), self.dups_before)

    def test_link_dry_run_and_real(self):
        code, out, _ = run("dupes", self.dups, "--no-progress", "--link", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Would link 2 duplicate(s)", out)
        self.assertFalse(os.path.samefile(os.path.join(self.dups, "a", "one.txt"), os.path.join(self.dups, "b", "two.txt")))
        code, out, err = run("dupes", self.dups, "--no-progress", "--link", "--yes")
        if "Linked 0" in out:
            self.skipTest("file system does not support hard links")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.samefile(os.path.join(self.dups, "a", "one.txt"), os.path.join(self.dups, "c", "three.txt")))
        self.assertEqual(tree(self.dups), self.dups_before)

    def test_link_confirmation_warns_it_cannot_be_undone(self):
        asked = []
        patches = self.interactive("n")
        with patches[0], mock.patch("builtins.input", side_effect=lambda q: asked.append(q) or "n"):
            run("dupes", self.dups, "--no-progress", "--link")
        self.assertIn("CANNOT be undone", asked[0])

    def test_move_and_link_together_is_a_usage_error(self):
        self.assertEqual(run("dupes", self.dups, "--move", "--link")[0], 2)

    def test_cache_options(self):
        cache = os.path.join(self.dups, JOURNAL_DIR_NAME, "hash_cache.json")
        run("dupes", self.dups, "--no-progress")
        self.assertTrue(os.path.exists(cache))
        run("dupes", self.dups, "--no-progress", "--clear-cache", "--no-cache")
        self.assertFalse(os.path.exists(cache))
        run("dupes", self.dups, "--no-progress", "--no-cache")
        self.assertFalse(os.path.exists(cache))

    def test_min_size_and_filters(self):
        out = run("dupes", self.dups, "--no-progress", "--min-size", "1MB")[1]
        self.assertIn("No duplicates found.", out)
        out = run("dupes", self.dups, "--no-progress", "--glob", "*.txt", "--skip", "c")[1]
        self.assertIn("Extra copies: 1", out)

    def test_nothing_to_do_with_an_action(self):
        code, out, _ = run("dupes", self.root, "--no-progress", "--move", "--yes")
        self.assertEqual(code, 0)


class TestRealProcess(unittest.TestCase):
    """End to end through `python -m Filing`, including the multiprocessing scan of many folders."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for i in range(FileStatsCollector._PARALLEL_MIN_FOLDERS + 8):
            make_file(os.path.join(self.tmp, "root", f"d{i}", f"f{i}.txt"), b"x" * i)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def filer(self, *args):
        env = dict(os.environ, PYTHONPATH=PACKAGE_PARENT)
        return subprocess.run([sys.executable, "-m", "Filing", *args], capture_output=True, text=True, env=env, cwd=self.tmp, timeout=120)

    def test_stats_json_in_a_real_process(self):
        result = self.filer("stats", os.path.join(self.tmp, "root"), "--all-files", "--json", "--no-progress")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["total_files"], FileStatsCollector._PARALLEL_MIN_FOLDERS + 8)

    def test_version_and_errors_in_a_real_process(self):
        self.assertEqual(self.filer("--version").stdout.strip(), f"filer {__version__}")
        result = self.filer("stats", os.path.join(self.tmp, "missing"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("filer: error:", result.stderr)
        self.assertEqual(self.filer("stats").returncode, 2)

    def test_move_needs_yes_without_a_terminal(self):
        root = os.path.join(self.tmp, "root")
        result = self.filer("move", root, "--all-files", "--no-progress")
        self.assertEqual(result.returncode, 1)
        self.assertIn("--yes", result.stderr)
        result = self.filer("move", root, "--all-files", "--yes", "--quiet", "--delay", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.filer("undo", root, "--quiet")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(os.path.join(root, "d0", "f0.txt")))


if __name__ == "__main__":
    unittest.main()
