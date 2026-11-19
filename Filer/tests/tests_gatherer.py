"""
Tests for MediaGatherer / Gatherer. They work on temporary folders only.

Run from the repository root:
    PYTHONPATH=Filer python -m unittest discover -s Filer/tests -p "tests_*.py"
"""
import os
import shutil
import tempfile
import unittest

from Filing.gatherer import MediaGatherer, Gatherer


def make_file(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def listing(folder):
    return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class GathererTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "root")
        make_file(os.path.join(self.root, "song.mp3"), b"1")
        make_file(os.path.join(self.root, "a", "photo.jpg"), b"22")
        make_file(os.path.join(self.root, "a", "b", "clip.MP4"), b"333")
        make_file(os.path.join(self.root, "a", "notes.txt"), b"text")
        make_file(os.path.join(self.root, "doc.pdf"), b"pdf")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestMediaGatherer(GathererTestCase):

    def test_moves_only_media_files_to_default_media_folder(self):
        g = MediaGatherer(self.root)
        g.gather_media()
        media = os.path.join(self.root, "media")
        self.assertEqual(listing(media), ["clip.MP4", "photo.jpg", "song.mp3"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "notes.txt")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "doc.pdf")))
        self.assertEqual(len(g.moved_files), 3)

    def test_moved_files_maps_source_to_destination(self):
        g = MediaGatherer(self.root)
        g.gather_media()
        src = os.path.join(self.root, "song.mp3")
        self.assertEqual(g.moved_files[src], os.path.join(self.root, "media", "song.mp3"))
        self.assertFalse(os.path.exists(src))

    def test_custom_destination_folder(self):
        dest = os.path.join(self.tmp, "elsewhere")
        MediaGatherer(self.root, destination_folder=dest).gather_media()
        self.assertEqual(listing(dest), ["clip.MP4", "photo.jpg", "song.mp3"])

    def test_move_all_files(self):
        g = MediaGatherer(self.root, all_files=True)
        g.gather_media()
        self.assertEqual(listing(os.path.join(self.root, "media")),
                         ["clip.MP4", "doc.pdf", "notes.txt", "photo.jpg", "song.mp3"])

    def test_destination_folder_is_not_rescanned(self):
        MediaGatherer(self.root).gather_media()
        second = MediaGatherer(self.root)  # media/ now holds the files and must be ignored
        self.assertEqual(second.total_files, 0)
        second.gather_media()
        self.assertEqual(listing(os.path.join(self.root, "media")), ["clip.MP4", "photo.jpg", "song.mp3"])

    def test_skip_folders_are_respected(self):
        g = MediaGatherer(self.root, skip_folders=["b"])
        g.gather_media()
        self.assertTrue(os.path.exists(os.path.join(self.root, "a", "b", "clip.MP4")))
        self.assertEqual(listing(os.path.join(self.root, "media")), ["photo.jpg", "song.mp3"])

    def test_name_conflicts_are_renamed_not_overwritten(self):
        make_file(os.path.join(self.root, "x", "song.mp3"), b"other")
        make_file(os.path.join(self.root, "y", "song.mp3"), b"another")
        MediaGatherer(self.root).gather_media()
        media = os.path.join(self.root, "media")
        files = listing(media)
        self.assertEqual(len([f for f in files if f.startswith("song")]), 3)
        self.assertIn("song.mp3", files)
        self.assertIn("song(1).mp3", files)
        self.assertIn("song(2).mp3", files)
        contents = {open(os.path.join(media, f), "rb").read() for f in files if f.startswith("song")}
        self.assertEqual(contents, {b"1", b"other", b"another"})

    def test_batch_moving_for_many_files(self):
        for i in range(120):
            make_file(os.path.join(self.root, "bulk", f"f{i}.png"), b"p")
        g = MediaGatherer(self.root)
        g.gather_media(batch_size=25, time_delay=0)
        self.assertEqual(len(g.moved_files), 123)
        self.assertEqual(len(listing(os.path.join(self.root, "media"))), 123)

    def test_nothing_to_move(self):
        g = MediaGatherer(self.root, media_extensions=[".zzz"])
        g.gather_media()
        self.assertEqual(g.moved_files, {})
        self.assertFalse(os.path.exists(os.path.join(self.root, "media", "song.mp3")))

    def test_get_files_to_move(self):
        g = MediaGatherer(self.root, all_files=True)
        self.assertEqual(len(g.get_files_to_move(move_all_files=True)), 5)
        self.assertEqual(len(g.get_files_to_move(move_all_files=False)), 3)

    def test_remove_number_suffix(self):
        r = MediaGatherer.remove_number_suffix
        self.assertEqual(r("song(1)"), "song")
        self.assertEqual(r("song(12)"), "song")
        self.assertEqual(r("song"), "song")
        self.assertEqual(r("song(a)"), "song(a)")
        self.assertEqual(r("a(1)b"), "a(1)b")

    def test_handle_file_conflict(self):
        g = MediaGatherer(self.root)
        free = os.path.join(self.tmp, "free.mp3")
        self.assertEqual(g.handle_file_conflict(free), free)
        taken = make_file(os.path.join(self.tmp, "taken.mp3"))
        self.assertEqual(g.handle_file_conflict(taken), os.path.join(self.tmp, "taken(1).mp3"))

    def test_generate_moved_stats_csv_before_moving_reports_nothing(self):
        g = MediaGatherer(self.root)
        self.assertEqual(g.generate_moved_stats_csv(os.path.join(self.tmp, "moved.csv")), -1)


class TestGatherer(GathererTestCase):

    def test_gathers_only_given_extensions_into_extracted_files(self):
        g = Gatherer(self.root, extensions=[".txt", ".pdf"])
        g.gather_files()
        self.assertEqual(listing(os.path.join(self.root, "Extracted Files")), ["doc.pdf", "notes.txt"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "song.mp3")))

    def test_custom_destination(self):
        dest = os.path.join(self.tmp, "out")
        Gatherer(self.root, destination_folder=dest, extensions=[".jpg"]).gather_files()
        self.assertEqual(listing(dest), ["photo.jpg"])

    def test_all_files(self):
        g = Gatherer(self.root, extensions=[".txt"], all_files=True)
        g.gather_files()
        self.assertEqual(len(listing(os.path.join(self.root, "Extracted Files"))), 5)


if __name__ == "__main__":
    unittest.main()
