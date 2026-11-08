import json
import os
import shutil
import tempfile
import unittest

from Filing.gatherer import MediaGatherer, Gatherer
from Filing.journal import MoveJournal, JOURNAL_DIR_NAME, undo_moves, find_latest_journal, list_journals
from Filing.mover import plan_moves, unique_destination, execute_moves


def make_file(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def tree(folder):
    """Sorted relative paths of every file below folder (journal folder excluded)."""
    found = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != JOURNAL_DIR_NAME]
        found += [os.path.relpath(os.path.join(base, f), folder).replace("\\", "/") for f in files]
    return sorted(found)


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        make_file(os.path.join(self.root, "song.mp3"), b"1")
        make_file(os.path.join(self.root, "a", "photo.jpg"), b"22")
        make_file(os.path.join(self.root, "a", "b", "clip.mp4"), b"333")
        make_file(os.path.join(self.root, "x", "song.mp3"), b"4444")
        make_file(os.path.join(self.root, "a", "notes.txt"), b"text")
        self.before = tree(self.root)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gatherer(self, **kw):
        return MediaGatherer(self.root, **kw)


class TestMoverFunctions(Base):

    def test_unique_destination_uses_reserved_names(self):
        target = os.path.join(self.tmp, "free.txt")
        self.assertEqual(unique_destination(target), target)
        reserved = {os.path.normcase(os.path.abspath(target))}
        self.assertEqual(unique_destination(target, reserved), os.path.join(self.tmp, "free(1).txt"))

    def test_plan_moves_resolves_conflicts_inside_the_plan(self):
        dest = os.path.join(self.tmp, "dest")
        pairs = [(os.path.join(self.root, "song.mp3"), os.path.join(dest, "song.mp3")),
                 (os.path.join(self.root, "x", "song.mp3"), os.path.join(dest, "song.mp3"))]
        plan = plan_moves(pairs)
        self.assertEqual([os.path.basename(d) for _, d in plan], ["song.mp3", "song(1).mp3"])
        self.assertFalse(os.path.exists(dest))

    def test_plan_moves_skips_files_already_in_place(self):
        path = os.path.join(self.root, "song.mp3")
        self.assertEqual(plan_moves([(path, path)]), [])

    def test_execute_moves_continues_after_a_failure(self):
        dest = os.path.join(self.tmp, "dest")
        plan = [(os.path.join(self.root, "missing.mp3"), os.path.join(dest, "missing.mp3")),
                (os.path.join(self.root, "song.mp3"), os.path.join(dest, "song.mp3"))]
        moved, failed = execute_moves(plan, verbose=False)
        self.assertEqual(len(moved), 1)
        self.assertEqual(len(failed), 1)
        self.assertTrue(os.path.exists(os.path.join(dest, "song.mp3")))


class TestDryRun(Base):

    def test_dry_run_returns_plan_and_touches_nothing(self):
        g = self.gatherer()
        plan = g.gather_media(dry_run=True)
        self.assertEqual(len(plan), 4)  # song.mp3 x2, photo.jpg, clip.mp4
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "media")))
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))
        self.assertEqual(g.moved_files, {})

    def test_dry_run_matches_real_run(self):
        g = self.gatherer()
        plan = g.gather_media(dry_run=True)
        g2 = self.gatherer()
        moved = g2.gather_media()
        self.assertEqual(plan, moved)

    def test_dry_run_resolves_name_conflicts(self):
        plan = self.gatherer().gather_media(dry_run=True)
        names = sorted(os.path.basename(d) for d in plan.values())
        self.assertIn("song.mp3", names)
        self.assertIn("song(1).mp3", names)

    def test_dry_run_respects_existing_files_in_destination(self):
        make_file(os.path.join(self.root, "media", "photo.jpg"), b"existing")
        plan = self.gatherer().gather_media(dry_run=True)
        self.assertIn("photo(1).jpg", [os.path.basename(d) for d in plan.values()])

    def test_dry_run_with_organize_by(self):
        plan = self.gatherer(organize_by="type").gather_media(dry_run=True)
        folders = {os.path.basename(os.path.dirname(d)) for d in plan.values()}
        self.assertEqual(folders, {"Audio", "Images", "Videos"})
        self.assertEqual(tree(self.root), self.before)

    def test_preview_moves_directly(self):
        plan = self.gatherer().preview_moves()
        self.assertEqual(len(plan), 4)

    def test_move_files_to_destination_dry_run(self):
        g = self.gatherer()
        plan = g.move_files_to_destination(g.get_files_to_move(), os.path.join(self.tmp, "out"), dry_run=True)
        self.assertEqual(len(plan), 4)
        self.assertEqual(tree(self.root), self.before)

    def test_gatherer_class_dry_run(self):
        g = Gatherer(self.root, extensions=[".txt"])
        plan = g.gather_files(dry_run=True)
        self.assertEqual(list(plan.values()), [os.path.join(self.root, "Extracted Files", "notes.txt")])
        self.assertEqual(tree(self.root), self.before)


class TestJournalAndUndo(Base):

    def test_move_writes_a_journal(self):
        g = self.gatherer()
        g.gather_media()
        journal_dir = os.path.join(self.root, JOURNAL_DIR_NAME)
        files = list_journals(journal_dir)
        self.assertEqual(len(files), 1)
        self.assertEqual(g.last_journal_path, files[0])
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["operation"], "move")
        self.assertFalse(data["undone"])
        self.assertEqual(len(data["moves"]), 4)
        self.assertEqual({m["source"] for m in data["moves"]}, {os.path.abspath(s) for s in g.moved_files})

    def test_no_journal_when_nothing_moved(self):
        self.gatherer(media_extensions=[".zzz"]).gather_media()
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))

    def test_journal_can_be_disabled(self):
        self.gatherer(journal=False).gather_media()
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))

    def test_custom_journal_dir(self):
        custom = os.path.join(self.tmp, "my_journals")
        g = self.gatherer(journal_dir=custom)
        g.gather_media()
        self.assertEqual(len(list_journals(custom)), 1)
        self.assertFalse(os.path.exists(os.path.join(self.root, JOURNAL_DIR_NAME)))
        g.undo_last_move()
        self.assertEqual(tree(self.root), self.before)

    def test_undo_restores_everything(self):
        g = self.gatherer()
        g.gather_media()
        self.assertNotEqual(tree(self.root), self.before)
        result = g.undo_last_move()
        self.assertEqual(len(result["restored"]), 4)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "media")))  # created folder removed
        self.assertEqual(g.moved_files, {})

    def test_undo_restores_file_contents(self):
        self.gatherer().gather_media()
        self.gatherer().undo_last_move()
        with open(os.path.join(self.root, "x", "song.mp3"), "rb") as f:
            self.assertEqual(f.read(), b"4444")

    def test_undo_with_organized_folders_removes_created_folders(self):
        g = self.gatherer(organize_by="{type}/{year}")
        g.gather_media()
        self.assertTrue(os.path.isdir(os.path.join(self.root, "media", "Images")))
        g.undo_last_move()
        self.assertEqual(tree(self.root), self.before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "media")))

    def test_undo_keeps_folder_that_existed_before(self):
        os.makedirs(os.path.join(self.root, "media"))
        g = self.gatherer()
        g.gather_media()
        g.undo_last_move()
        self.assertTrue(os.path.isdir(os.path.join(self.root, "media")))

    def test_undo_marks_journal_and_second_undo_finds_nothing(self):
        g = self.gatherer()
        g.gather_media()
        path = g.last_journal_path
        g.undo_last_move()
        with open(path, encoding="utf-8") as f:
            self.assertTrue(json.load(f)["undone"])
        self.assertIsNone(find_latest_journal(os.path.join(self.root, JOURNAL_DIR_NAME)))
        with self.assertRaises(FileNotFoundError):
            g.undo_last_move()

    def test_undo_dry_run_changes_nothing(self):
        g = self.gatherer()
        g.gather_media()
        after_move = tree(self.root)
        result = g.undo_last_move(dry_run=True)
        self.assertEqual(len(result["restored"]), 4)
        self.assertTrue(result["dry_run"])
        self.assertEqual(tree(self.root), after_move)
        self.assertEqual(len(g.moved_files), 4)

    def test_undo_never_overwrites_and_can_be_retried(self):
        g = self.gatherer()
        g.gather_media()
        occupied = os.path.join(self.root, "a", "photo.jpg")
        make_file(occupied, b"new file")
        result = g.undo_last_move()
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "original location is occupied")
        with open(occupied, "rb") as f:
            self.assertEqual(f.read(), b"new file")
        # journal is still pending; after the user frees the spot, undo finishes the job
        os.remove(occupied)
        result = g.undo_last_move()
        self.assertEqual(len(result["restored"]), 1)
        with open(occupied, "rb") as f:
            self.assertEqual(f.read(), b"22")
        self.assertIsNone(find_latest_journal(os.path.join(self.root, JOURNAL_DIR_NAME)))

    def test_undo_skips_files_that_are_gone(self):
        g = self.gatherer()
        moved = g.gather_media()
        os.remove(next(iter(moved.values())))
        result = g.undo_last_move()
        self.assertEqual(len(result["restored"]), 3)
        self.assertEqual(result["skipped"][0]["reason"], "moved file no longer exists")

    def test_undo_targets_latest_journal_only(self):
        g1 = self.gatherer(media_extensions=[".jpg"])
        g1.gather_media()
        g2 = self.gatherer(media_extensions=[".mp4"])
        g2.gather_media()
        g2.undo_last_move()
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "b", "clip.mp4")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a", "photo.jpg")))  # first run still in effect
        g2.undo_last_move()
        self.assertEqual(tree(self.root), self.before)

    def test_undo_specific_journal_by_path(self):
        g = self.gatherer()
        g.gather_media()
        result = undo_moves(g.last_journal_path)
        self.assertEqual(len(result["restored"]), 4)

    def test_undo_moves_needs_a_location(self):
        with self.assertRaises(ValueError):
            undo_moves()

    def test_journal_folder_is_not_scanned(self):
        self.gatherer().gather_media()
        collector_names = [s["File Name"] for s in MediaGatherer(self.root, media_extensions=[".json"], all_files=True).file_stats]
        self.assertFalse(any(name.endswith(".json") for name in collector_names))

    def test_batched_moves_are_all_in_one_journal(self):
        for i in range(120):
            make_file(os.path.join(self.root, "bulk", f"f{i}.png"))
        g = self.gatherer()
        g.gather_media(batch_size=25, time_delay=0)
        self.assertEqual(len(list_journals(os.path.join(self.root, JOURNAL_DIR_NAME))), 1)
        g.undo_last_move()
        self.assertEqual(len([n for n in tree(self.root) if n.startswith("bulk/")]), 120)
        self.assertEqual(tree(self.root), sorted(self.before + [f"bulk/f{i}.png" for i in range(120)]))

    def test_journal_class_make_dirs_records_only_new_folders(self):
        journal = MoveJournal(self.root, operation="move")
        existing = os.path.join(self.root, "a")
        journal.make_dirs(os.path.join(existing, "new1", "new2"))
        self.assertEqual(len(journal.created_dirs), 2)
        self.assertTrue(journal.created_dirs[0].endswith("new1"))  # top-most first
        self.assertTrue(os.path.exists(journal.save()))  # created folders are journalled too


if __name__ == "__main__":
    unittest.main()
