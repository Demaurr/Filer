"""
Tests for ignore rules (.filerignore / --exclude): the rule language, scanning with rules from the
Python API, and the command line options.

All folders and files are created by the tests in their own temporary folder.
"""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from unittest import mock

from Filing import (FileStatsCollector, MediaGatherer, DuplicateFinder, IgnoreRules, cli)
from Filing.ignore import MAX_RULES, MAX_PATTERN_LENGTH, MAX_IGNORE_FILE_BYTES, IGNORE_FILE_NAME, load_ignore_rules

OLD = 1_600_000_000


def write(path, content=b"x", mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    os.utime(path, (mtime, mtime))
    return path


class TestRuleLanguage(unittest.TestCase):

    def check(self, patterns, cases, case_sensitive=True):
        rules = IgnoreRules(patterns, case_sensitive=case_sensitive)
        for path, is_dir, expected in cases:
            self.assertEqual(rules.is_ignored(path, is_dir), expected, msg=f"{patterns} vs {path!r} (dir={is_dir})")

    def test_plain_name_matches_at_any_depth(self):
        self.check(["secret.txt"], [("secret.txt", 0, True), ("a/secret.txt", 0, True), ("a/b/secret.txt", 0, True),
                                    ("secret.txt2", 0, False), ("my_secret.txt", 0, False), ("a/secret", 1, False)])

    def test_name_matches_folders_too_and_everything_inside(self):
        self.check(["cache"], [("cache", 1, True), ("a/cache", 1, True), ("cache/x.txt", 0, True),
                               ("a/cache/deep/x.txt", 0, True), ("cached", 1, False)])

    def test_star_and_question_mark(self):
        self.check(["*.log", "data?.csv"], [
            ("a.log", 0, True), ("x/y/a.log", 0, True), ("a.log.txt", 0, False), (".log", 0, True),
            ("data1.csv", 0, True), ("data12.csv", 0, False), ("data.csv", 0, False)])

    def test_star_does_not_cross_folders(self):
        self.check(["docs/*.md"], [("docs/a.md", 0, True), ("docs/sub/a.md", 0, False), ("x/docs/a.md", 0, False)])

    def test_character_classes(self):
        self.check(["file[0-9].txt", "x[!a]y", "[]]z"], [
            ("file5.txt", 0, True), ("fileA.txt", 0, False), ("xby", 0, True), ("xay", 0, False), ("]z", 0, True)])

    def test_unclosed_bracket_is_literal(self):
        self.check(["a[b"], [("a[b", 0, True), ("ab", 0, False)])

    def test_trailing_slash_means_folders_only(self):
        self.check(["build/"], [("build", 1, True), ("build", 0, False), ("src/build", 1, True),
                                ("build/out.o", 0, True)])

    def test_leading_slash_anchors_to_the_root(self):
        self.check(["/top.txt", "/only"], [("top.txt", 0, True), ("a/top.txt", 0, False),
                                            ("only", 1, True), ("a/only", 1, False)])

    def test_slash_inside_the_pattern_anchors_it(self):
        self.check(["a/b.txt"], [("a/b.txt", 0, True), ("x/a/b.txt", 0, False)])

    def test_double_star(self):
        self.check(["**/tmp", "logs/**", "a/**/z.txt", "**/*.bak"], [
            ("tmp", 1, True), ("x/y/tmp", 1, True), ("logs/a.txt", 0, True), ("logs/a/b/c.txt", 0, True),
            ("logs", 1, False), ("a/z.txt", 0, True), ("a/b/c/z.txt", 0, True), ("b/z.txt", 0, False),
            ("old.bak", 0, True), ("deep/er/old.bak", 0, True)])

    def test_negation_reincludes(self):
        self.check(["*.log", "!keep.log"], [("a.log", 0, True), ("keep.log", 0, False), ("x/keep.log", 0, False)])

    def test_last_matching_rule_wins(self):
        self.check(["!keep.log", "*.log"], [("keep.log", 0, True)])
        self.check(["*.log", "!keep.log", "keep.log"], [("keep.log", 0, True)])

    def test_cannot_reinclude_inside_an_ignored_folder(self):
        self.check(["build/", "!build/keep.txt"], [("build/keep.txt", 0, True)])

    def test_comments_blank_lines_and_escapes(self):
        rules = IgnoreRules(["# a comment", "", "   ", r"\#hash.txt", r"\!bang.txt", "real.txt"])
        self.assertEqual(len(rules), 3)
        self.assertTrue(rules.is_ignored("#hash.txt"))
        self.assertTrue(rules.is_ignored("!bang.txt"))
        self.assertTrue(rules.is_ignored("real.txt"))
        self.assertFalse(rules.is_ignored("a comment"))

    def test_trailing_spaces_dropped_unless_escaped(self):
        rules = IgnoreRules(["name.txt   ", "spaced\\ "])
        self.assertTrue(rules.is_ignored("name.txt"))
        self.assertTrue(rules.is_ignored("spaced "))

    def test_special_characters_are_literal(self):
        self.check(["a+b(1).txt", "x{2}.txt", "p|q", "$dollar", "^caret", "dot.dot"], [
            ("a+b(1).txt", 0, True), ("aab(1).txt", 0, False), ("x{2}.txt", 0, True), ("xx.txt", 0, False),
            ("p|q", 0, True), ("p", 0, False), ("$dollar", 0, True), ("^caret", 0, True), ("dotXdot", 0, False)])

    def test_case_rules(self):
        self.check(["Thumbs.db"], [("thumbs.db", 0, False), ("Thumbs.db", 0, True)], case_sensitive=True)
        self.check(["Thumbs.db"], [("thumbs.db", 0, True), ("THUMBS.DB", 0, True), ("x/ThUmBs.Db", 0, True)],
                   case_sensitive=False)
        self.assertEqual(IgnoreRules(case_sensitive=None).case_sensitive, os.name != "nt")

    def test_separator_styles_and_prefixes(self):
        rules = IgnoreRules(["a/b.txt"], case_sensitive=True)
        self.assertTrue(rules.is_ignored("a\\b.txt"))
        self.assertTrue(rules.is_ignored("./a/b.txt"))
        self.assertTrue(rules.is_ignored("/a/b.txt"))

    def test_paths_outside_the_folder_are_never_ignored(self):
        rules = IgnoreRules(["*", "**"])
        for outside in ("..", "../x", "a/../../x", "C:/x", "c:\\x", "", "/", "."):
            self.assertFalse(rules.is_ignored(outside), msg=repr(outside))

    def test_no_rules_ignore_nothing(self):
        rules = IgnoreRules()
        self.assertFalse(rules)
        self.assertFalse(rules.is_ignored("anything"))
        self.assertEqual(len(rules), 0)

    def test_string_with_several_lines_and_patterns_property(self):
        rules = IgnoreRules("*.log\n!keep.log\nbuild/\n")
        self.assertEqual(rules.patterns, ["*.log", "!keep.log", "build/"])
        rules.add("*.tmp")
        self.assertTrue(rules.is_ignored("a.tmp"))
        self.assertIn("IgnoreRules", repr(rules))

    def test_crlf_lines_and_bom_in_files(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, IGNORE_FILE_NAME)
            with open(path, "wb") as f:
                f.write(b"\xef\xbb\xbf*.log\r\nbuild/\r\n")
            rules = IgnoreRules.from_file(path)
            self.assertEqual(rules.patterns, ["*.log", "build/"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_input(self):
        with self.assertRaises(TypeError):
            IgnoreRules([5])
        with self.assertRaises(ValueError):
            IgnoreRules(["x" * (MAX_PATTERN_LENGTH + 1)])
        with self.assertRaises(ValueError):
            IgnoreRules([f"p{n}" for n in range(MAX_RULES + 1)])

    def test_file_limits_and_bad_bytes(self):
        tmp = tempfile.mkdtemp()
        try:
            big = os.path.join(tmp, "big")
            with open(big, "wb") as f:
                f.write(b"a\n" * (MAX_IGNORE_FILE_BYTES // 2 + 10))
            with self.assertRaises(ValueError):
                IgnoreRules.from_file(big)
            odd = os.path.join(tmp, "odd")
            with open(odd, "wb") as f:
                f.write(b"ok.txt\n\xff\xfe\x00bad\n")
            self.assertTrue(IgnoreRules.from_file(odd).is_ignored("ok.txt"))
            with self.assertRaises(OSError):
                IgnoreRules.from_file(os.path.join(tmp, "missing"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pathological_patterns_stay_fast(self):
        rules = IgnoreRules(["a*a*a*a*a*a*a*b", "**/**/**/x", "[a-z]*[a-z]*[a-z]*!"])
        name = "a" * 5000
        start = time.perf_counter()
        for _ in range(20):
            rules.is_ignored(name)
            rules.is_ignored("/".join(["d"] * 400) + "/y")
        self.assertLess(time.perf_counter() - start, 5)

    def test_many_rules_with_negation_still_work(self):
        patterns = [f"junk{n}.tmp" for n in range(3000)] + ["!junk7.tmp"]
        rules = IgnoreRules(patterns)
        self.assertTrue(rules.is_ignored("junk6.tmp"))
        self.assertFalse(rules.is_ignored("junk7.tmp"))

    def test_matching_never_touches_the_disk(self):
        rules = IgnoreRules(["*.log", "build/"])
        with mock.patch("os.stat", side_effect=AssertionError("stat")), \
                mock.patch("os.scandir", side_effect=AssertionError("scandir")), \
                mock.patch("builtins.open", side_effect=AssertionError("open")):
            self.assertTrue(rules.is_ignored("a/b.log"))


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="filer_ignore_test_")
        self.root = os.path.join(self.tmp, "root")
        os.makedirs(self.root)
        for rel in ("keep.txt", "debug.log", "keep.log", "src/code.py", "src/code.pyc", "build/out.o",
                    "build/sub/more.o", "docs/a.md", "docs/draft.md", "node_modules/pkg/index.js"):
            write(os.path.join(self.root, *rel.split("/")), rel.encode())  # every file has its own content

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self, collector):
        return sorted(os.path.relpath(os.path.join(s["Source Folder"], s["File Name"]), self.root).replace(os.sep, "/")
                      for s in collector.file_stats)

    def scan(self, **kw):
        kw.setdefault("all_files", True)
        kw.setdefault("verbose", False)
        return FileStatsCollector(self.root, **kw)


class TestScanningWithRules(Base):

    def test_no_rules_changes_nothing(self):
        self.assertEqual(len(self.names(self.scan())), 10)
        self.assertIsNone(self.scan().ignore_rules)

    def test_exclude_list(self):
        found = self.names(self.scan(exclude=["*.log", "build/", "node_modules"]))
        self.assertEqual(found, ["docs/a.md", "docs/draft.md", "keep.txt", "src/code.py", "src/code.pyc"])

    def test_exclude_single_string(self):
        self.assertNotIn("debug.log", self.names(self.scan(exclude="*.log")))

    def test_negation(self):
        found = self.names(self.scan(exclude=["*.log", "!keep.log"]))
        self.assertIn("keep.log", found)
        self.assertNotIn("debug.log", found)

    def test_filerignore_file_is_read_automatically(self):
        write(os.path.join(self.root, IGNORE_FILE_NAME), b"# my rules\n*.log\nbuild/\n")
        found = self.names(self.scan())
        self.assertNotIn("debug.log", found)
        self.assertNotIn("build/out.o", found)
        self.assertIn(IGNORE_FILE_NAME, found)  # the rules file is an ordinary file unless you ignore it

    def test_ignore_file_can_be_switched_off_or_replaced(self):
        write(os.path.join(self.root, IGNORE_FILE_NAME), b"*.log\n")
        self.assertIn("debug.log", self.names(self.scan(ignore_file=False)))
        self.assertIn("debug.log", self.names(self.scan(ignore_file=None)))
        other = write(os.path.join(self.tmp, "elsewhere.rules"), b"*.md\n")
        found = self.names(self.scan(ignore_file=other))
        self.assertIn("debug.log", found)
        self.assertNotIn("docs/a.md", found)

    def test_exclude_adds_to_the_file(self):
        write(os.path.join(self.root, IGNORE_FILE_NAME), b"*.log\n")
        found = self.names(self.scan(exclude=["*.md"]))
        self.assertNotIn("debug.log", found)
        self.assertNotIn("docs/a.md", found)

    def test_missing_explicit_ignore_file_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "Cannot use ignore file"):
            self.scan(ignore_file=os.path.join(self.tmp, "nope"))

    def test_damaged_automatic_ignore_file_warns_and_scans_on(self):
        path = os.path.join(self.root, IGNORE_FILE_NAME)
        with open(path, "wb") as f:
            f.write(b"a\n" * (MAX_IGNORE_FILE_BYTES // 2 + 10))
        with mock.patch("builtins.print") as printed:
            found = self.names(self.scan())
        self.assertEqual(len(found), 11)
        self.assertTrue(any("ignoring unreadable" in str(c) for c in printed.call_args_list))

    def test_ignored_folders_are_never_entered(self):
        listed = []
        real = os.scandir

        def spy(path):
            listed.append(os.path.basename(str(path)))
            return real(path)

        with mock.patch("os.scandir", spy):
            self.scan(exclude=["build/", "node_modules"])
        self.assertNotIn("build", listed)
        self.assertNotIn("sub", listed)
        self.assertNotIn("node_modules", listed)
        self.assertNotIn("pkg", listed)
        self.assertIn("src", listed)

    def test_anchored_rule_only_hits_the_top_level(self):
        write(os.path.join(self.root, "src", "build", "x.o"))
        found = self.names(self.scan(exclude=["/build"]))
        self.assertNotIn("build/out.o", found)
        self.assertIn("src/build/x.o", found)

    def test_rules_work_with_other_filters_and_skip_folders(self):
        found = self.names(self.scan(exclude=["*.log"], skip_folders=["node_modules"], name_glob="*.o"))
        self.assertEqual(found, ["build/out.o", "build/sub/more.o"])

    def test_media_only_scan_with_rules(self):
        write(os.path.join(self.root, "a.mp3"))
        write(os.path.join(self.root, "build", "b.mp3"))
        found = self.names(self.scan(all_files=False, exclude=["build/"]))
        self.assertEqual(found, ["a.mp3"])

    def test_two_pass_path_gives_the_same_files(self):
        class TwoPass(FileStatsCollector):
            def process_files(self, *args, **kwargs):
                return super().process_files(*args, **kwargs)

        write(os.path.join(self.root, IGNORE_FILE_NAME), b"*.log\n!keep.log\nbuild/\ndocs/**/draft*\n")
        single = self.scan()
        double = TwoPass(self.root, all_files=True, verbose=False)
        self.assertEqual(single.file_stats, double.file_stats)
        self.assertLess(len(self.names(single)), 11)

    def test_load_ignore_rules_function(self):
        self.assertIsNone(load_ignore_rules(self.root))
        self.assertTrue(load_ignore_rules(self.root, exclude="*.x").is_ignored("a.x"))


class TestOtherClassesAcceptRules(Base):

    def test_move_leaves_ignored_files_alone(self):
        g = MediaGatherer(self.root, all_files=True, exclude=["*.log", "build/"], verbose=False)
        moved = g.gather_media()
        self.assertEqual(len(moved), 6)  # keep.txt, src x2, docs x2 + the media folder is empty of ignored files
        self.assertTrue(os.path.exists(os.path.join(self.root, "debug.log")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "build", "out.o")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "keep.txt")))

    def test_dry_run_move_ignores_too(self):
        plan = MediaGatherer(self.root, all_files=True, exclude=["*.log", "build/", "node_modules"],
                             verbose=False).gather_media(dry_run=True)
        self.assertFalse(any(source.endswith((".log", ".o", "index.js")) for source in plan))

    def test_duplicates_skip_ignored_folders(self):
        write(os.path.join(self.root, "one.dat"), b"same")
        write(os.path.join(self.root, "build", "two.dat"), b"same")
        write(os.path.join(self.root, "docs", "three.dat"), b"same")
        everything = DuplicateFinder(self.root, verbose=False, use_cache=False).find()
        self.assertEqual(len(everything[0].files), 3)
        ignoring = DuplicateFinder(self.root, verbose=False, use_cache=False, exclude=["build/"]).find()
        self.assertEqual(len(ignoring[0].files), 2)
        self.assertFalse(any(os.sep + "build" + os.sep in p for p in ignoring[0].files))

    def test_filerignore_file_is_used_by_duplicates_and_move(self):
        write(os.path.join(self.root, IGNORE_FILE_NAME), b"build/\n")
        write(os.path.join(self.root, "one.dat"), b"same")
        write(os.path.join(self.root, "build", "two.dat"), b"same")
        self.assertEqual(DuplicateFinder(self.root, verbose=False, use_cache=False).find(), [])


class TestCommandLine(Base):

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv) + ["--no-progress"])
        return code, out.getvalue(), err.getvalue()

    def stats(self, *extra):
        code, out, err = self.run_cli("stats", self.root, "--all-files", "--json", *extra)
        self.assertEqual(code, 0, msg=err)
        return json.loads(out)["total_files"]

    def test_exclude_option(self):
        self.assertEqual(self.stats(), 10)
        self.assertEqual(self.stats("--exclude", "*.log"), 8)
        self.assertEqual(self.stats("--exclude", "*.log", "--exclude", "build/"), 6)

    def test_filerignore_is_used_and_can_be_disabled(self):
        write(os.path.join(self.root, IGNORE_FILE_NAME), b"*.log\n")
        self.assertEqual(self.stats(), 9)  # 10 files - 2 logs + the rules file itself
        self.assertEqual(self.stats("--no-ignore-file"), 11)

    def test_ignore_file_option(self):
        other = write(os.path.join(self.tmp, "rules.txt"), b"build/\n")
        self.assertEqual(self.stats("--ignore-file", other), 8)
        code, out, err = self.run_cli("stats", self.root, "--ignore-file", os.path.join(self.tmp, "none"))
        self.assertEqual(code, 1)
        self.assertIn("Cannot use ignore file", err)

    def test_move_dry_run_with_exclude(self):
        code, out, err = self.run_cli("move", self.root, "--all-files", "--dry-run", "--json", "--exclude", "*.log",
                                      "--exclude", "build/", "--exclude", "node_modules")
        self.assertEqual(code, 0, msg=err)
        plan = json.loads(out)["moves"]
        self.assertEqual(len(plan), 5)
        self.assertFalse(any(s.endswith((".log", ".o", ".js")) for s in plan))

    def test_dupes_with_exclude(self):
        write(os.path.join(self.root, "one.dat"), b"same")
        write(os.path.join(self.root, "build", "two.dat"), b"same")
        code, out, err = self.run_cli("dupes", self.root, "--json", "--exclude", "build/")
        self.assertEqual(code, 0, msg=err)
        self.assertEqual(json.loads(out)["groups"], [])
        code, out, err = self.run_cli("dupes", self.root, "--json")
        self.assertEqual(len(json.loads(out)["groups"]), 1)


if __name__ == "__main__":
    unittest.main()
