from collections import Counter
import os
import unittest
from Filing.filestatser import FileStatsCollector

class TestFileStatsCollector(unittest.TestCase):

    def setUp(self):
        self.root_folder = r"Filer\tests\have_files"
        self.root_folder_empty = r"Filer\tests\have_no_files"
        self.media_extensions = ['.mp3', '.mp4']
        self.all_files = True
        self.skip_folders = ['.git', '__pycache__']
        self.file_stats_collector = FileStatsCollector(
            self.root_folder, 
            media_extensions=self.media_extensions, 
            all_files=self.all_files, 
            skip_folders=self.skip_folders
        )

    def test_file_types(self):
        file_types = self.file_stats_collector.get_file_typedist()
        self.assertIsInstance(file_types, Counter)
        print("File Types Distribution:", file_types)

    def test_folders_info(self):
        folder_info = self.file_stats_collector.get_folders_info()
        self.assertIsInstance(folder_info, Counter)
        print("Folders Info:", folder_info)

    def test_print_file_types(self):
        self.file_stats_collector.print_file_types()

    def test_generate_file_stats_csv_with_files(self):
        file_stats_collector = FileStatsCollector(
            self.root_folder, 
            media_extensions=self.media_extensions, 
            all_files=self.all_files, 
            skip_folders=self.skip_folders
        )
        csv_path = os.path.join(self.root_folder, "file_stats.csv")
        file_stats_collector.generate_file_stats_csv(csv_path)
        self.assertTrue(os.path.exists(csv_path))
        print(f"CSV generated at: {csv_path}")

    def test_generate_file_stats_csv_no_files(self):
        file_stats_collector = FileStatsCollector(
            self.root_folder_empty, 
            media_extensions=self.media_extensions, 
            all_files=self.all_files, 
            skip_folders=self.skip_folders
        )
        csv_path = os.path.join(self.root_folder_empty, "file_stats.csv")
        file_stats_collector.generate_file_stats_csv(csv_path)
        self.assertFalse(os.path.exists(csv_path))
        print(f"No CSV generated as no files are present in: {self.root_folder_empty}")

if __name__ == '__main__':
    unittest.main()