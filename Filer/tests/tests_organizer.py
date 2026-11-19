import os
import unittest

from Filing.organizer import Organizer, categorize, size_bucket, make_organizer


def stat(name="photo.JPG", size=2000, modified="2026-11-09 14:30:00", created="2020-03-04 01:02:03", source=r"C:\Pics\Holiday"):
    return {"File Name": name, "File Size (Bytes)": size, "Modification Date": modified,
            "Creation Date": created, "Source Folder": source}


def p(*parts):
    return os.path.join(*parts)


class TestHelpers(unittest.TestCase):

    def test_categorize(self):
        self.assertEqual(categorize(".JPG"), "Images")
        self.assertEqual(categorize(".mkv"), "Videos")
        self.assertEqual(categorize(".mp3"), "Audio")
        self.assertEqual(categorize(".pdf"), "Documents")
        self.assertEqual(categorize(".zip"), "Archives")
        self.assertEqual(categorize(".py"), "Code")
        self.assertEqual(categorize(".weird"), "Other")
        self.assertEqual(categorize(""), "Other")

    def test_size_bucket(self):
        self.assertEqual(size_bucket(0), "Tiny")
        self.assertEqual(size_bucket(100 * 1024 - 1), "Tiny")
        self.assertEqual(size_bucket(100 * 1024), "Small")
        self.assertEqual(size_bucket(10 * 1024 ** 2), "Medium")
        self.assertEqual(size_bucket(100 * 1024 ** 2), "Large")
        self.assertEqual(size_bucket(1024 ** 3), "Huge")


class TestOrganizerPresets(unittest.TestCase):

    def test_type(self):
        self.assertEqual(Organizer("type").relative_path(stat()), p("Images", "photo.JPG"))

    def test_date_uses_modification_date_by_default(self):
        self.assertEqual(Organizer("date").relative_path(stat()), p("2026", "11", "photo.JPG"))

    def test_date_can_use_creation_date(self):
        self.assertEqual(Organizer("date", date_field="created").relative_path(stat()), p("2020", "03", "photo.JPG"))

    def test_size(self):
        self.assertEqual(Organizer("size").relative_path(stat(size=2000)), p("Tiny", "photo.JPG"))
        self.assertEqual(Organizer("size").relative_path(stat(size=5 * 1024 ** 2)), p("Small", "photo.JPG"))

    def test_extension(self):
        self.assertEqual(Organizer("extension").relative_path(stat()), p("jpg", "photo.JPG"))
        self.assertEqual(Organizer("extension").relative_path(stat(name="README")), p("noext", "README"))

    def test_preset_names_are_case_insensitive(self):
        self.assertEqual(Organizer("TYPE").pattern, "{type}")


class TestOrganizerPatterns(unittest.TestCase):

    def test_folder_pattern_keeps_file_name(self):
        self.assertEqual(Organizer("{type}/{year}/{month}").relative_path(stat()), p("Images", "2026", "11", "photo.JPG"))

    def test_both_separators_work(self):
        self.assertEqual(Organizer("{type}\\{year}").relative_path(stat()), p("Images", "2026", "photo.JPG"))

    def test_pattern_ending_in_name_describes_full_path_and_adds_extension(self):
        self.assertEqual(Organizer("{type}/{year}/{name}").relative_path(stat()), p("Images", "2026", "photo.JPG"))

    def test_name_with_prefix_and_explicit_extension(self):
        self.assertEqual(Organizer("{year}/{day}_{name}{ext}").relative_path(stat()), p("2026", "09_photo.JPG"))

    def test_filename_field(self):
        self.assertEqual(Organizer("{source}/{filename}").relative_path(stat()), p("Holiday", "photo.JPG"))

    def test_source_and_literal_text(self):
        self.assertEqual(Organizer("Sorted/{source}").relative_path(stat()), p("Sorted", "Holiday", "photo.JPG"))

    def test_destination_joins_destination_folder(self):
        self.assertEqual(Organizer("type").destination(stat(), "out"), p("out", "Images", "photo.JPG"))

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ValueError):
            Organizer("{nonsense}/{type}")

    def test_bad_patterns_are_rejected(self):
        for bad in ("", "   ", "{type", "///"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                Organizer(bad)
        with self.assertRaises(ValueError):
            Organizer("type", date_field="accessed")

    def test_values_cannot_escape_the_destination(self):
        evil = stat(source=r"C:\..\..\Windows", name="a.txt")
        for rule in ("{source}", "{source}/{name}", "{source}/{filename}"):
            relative = Organizer(rule).relative_path(evil)
            self.assertFalse(os.path.isabs(relative))
            self.assertNotIn("..", relative.split(os.sep))

    def test_illegal_characters_are_replaced(self):
        result = Organizer("{name}").relative_path(stat(name='we:ird*na?me.txt'))
        self.assertEqual(result, "we_ird_na_me.txt")

    def test_empty_values_become_underscore(self):
        self.assertEqual(Organizer("{source}").relative_path(stat(source="")), p("_", "photo.JPG"))


class TestOrganizerFunctions(unittest.TestCase):

    def test_function_rule(self):
        organizer = Organizer(lambda s: "Big" if s["File Size (Bytes)"] > 1000 else "Small/Files")
        self.assertEqual(organizer.relative_path(stat(size=5000)), p("Big", "photo.JPG"))
        self.assertEqual(organizer.relative_path(stat(size=5)), p("Small", "Files", "photo.JPG"))

    def test_function_result_is_sanitised(self):
        self.assertEqual(Organizer(lambda s: "..\\x").relative_path(stat()), p("_", "x", "photo.JPG"))

    def test_function_returning_nothing_uses_destination_root(self):
        self.assertEqual(Organizer(lambda s: "").relative_path(stat()), "photo.JPG")

    def test_make_organizer(self):
        self.assertIsNone(make_organizer(None))
        existing = Organizer("type")
        self.assertIs(make_organizer(existing), existing)
        self.assertIsInstance(make_organizer("date"), Organizer)


if __name__ == "__main__":
    unittest.main()
