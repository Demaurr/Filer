import os
import shutil
import csv
from datetime import datetime
from Filing.filestatser import FileStatsCollector
import re

class MediaGatherer(FileStatsCollector):
    """
    A class for gathering media files from a specified root folder, providing options for file renaming
    and specifying destination folders, and generating statistics on the moved files.

    Attributes:
        root_folder (str): The root folder from which media files will be gathered.
        media_folder (str): The default folder where media files will be moved.
        media_extensions (list): List of allowed media file extensions.
        moved_files (list): List of paths to the moved media files.
        file_sources (list): List of tuples containing the original filename and its source folder.
        file_stats_dict (list): List of dictionaries containing detailed statistics of moved media files.

    Methods:
        is_media_file(filename): Check if a given filename has a valid media file extension.
        create_media_folder(destination_folder): Create the specified destination folder if it doesn't exist.
        move_files_to_destination(destination_folder, move_all_files=False): Move media files to the specified destination folder.
        show_stats(): Display statistics on the moved media files.
        format_size(size): Format file size in a human-readable format.
        show_file_size_distribution(): Display the distribution of file sizes.
        generate_stats_csv(): Generate a CSV file containing detailed statistics of moved media files.
        gather_media(destination_folder=None, move_all=False): Perform the media gathering process with options for destination folder and moving all files.

    Example Usage:
        root_directory = input("Enter the root directory: ")
        destination_folder = input("Enter the destination folder (optional): ")
        move_all_files = input("Move all files? (yes/no): ").lower() == 'yes'
        
        media_gatherer = MediaGatherer(root_directory)
        media_gatherer.gather_media(destination_folder, move_all_files)
        media_gatherer.generate_stats_csv()
    """

    def __init__(self, root_folder, destination_folder="media", skip_folders=[], media_extensions=['.mp3', '.mp4', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.gif'], all_files=False):
        skip_folders.append(destination_folder)
        super().__init__(root_folder, media_extensions=media_extensions, skip_folders=skip_folders, all_files=all_files)
        self.root_folder = root_folder
        self.media_extensions = media_extensions
        self.moved_files = {}
        self.file_sources = []
        self.file_stats_dict = []
        self.dest_folder = destination_folder

    def create_media_folder(self, destination_folder):
        """
        Create the specified destination folder if it doesn't exist.

        Parameters:
            destination_folder (str): The destination folder path.
        """
        if not os.path.exists(destination_folder):
            os.makedirs(destination_folder)
            self.dest_folder = destination_folder

    def move_files_to_destination(self, destination_folder, move_all_files=False):
        """
        Move media files to the specified destination folder.

        Parameters:
            destination_folder (str): The destination folder path.
            move_all_files (bool): Flag indicating whether to move all files or only media files.
        """
        try:
            self.create_media_folder(destination_folder)
            files_to_move = [stat for stat in self.file_stats if move_all_files or self.is_media_file(stat['File Name'])]
            if not files_to_move:
                print("\nNO FILEs TO MOVE. ^<>^")
                return -1

            for file_stat in files_to_move:
                source_path = os.path.join(file_stat['Source Folder'], file_stat['File Name'])
                destination_path = os.path.join(destination_folder, file_stat['File Name'])

                # print("The Suffix remover: ",self.remove_number_suffix(os.path.splitext(destination_path)[0]))

                # Handle duplicate filenames
                if os.path.exists(destination_path):
                    destination_path = self.rename_if_exists(destination_path)

                shutil.move(source_path, destination_path)

                # storing source path and destination path of the moved files in a dict {source_path: destination}
                self.moved_files[source_path] = destination_path

                print(f"\tMoved: {file_stat['File Name']}")

            print(f"Total Files Moved: {len(files_to_move)}")
        except Exception as e:
            print(f"Error moving files to destination folder: {e}")
    
    def rename_if_exists(self, filename):
        """
        Rename a filename if it already exists in the destination folder.

        Parameters:
            filename (str): The filename to check and rename if necessary.

        Returns:
            str: The new filename after renaming.
        """
        
        base_name, extension = os.path.splitext(filename)
        base_name = self.remove_number_suffix(base_name)  # Remove (number) suffix if exists
        counter = 1
        new_filename = filename
        
        while os.path.exists(new_filename):
            new_filename = f"{base_name}({counter}){extension}"
            counter += 1

        return new_filename

    @staticmethod
    def remove_number_suffix(basename):
        """
        Remove the suffix "(number)" from the basename if it exists.

        Parameters:
            basename (str): The basename of the file.

        Returns:
            str: The basename without the "(number)" suffix.
        """
        match = re.match(r'^(.*?)\(\d+\)$', basename)
        if match:
            return match.group(1)
        return basename

    def gather_media(self, destination_folder=None, move_all=False):
        """
        Perform the media gathering process with options for destination folder and moving all files.

        Parameters:
            destination_folder (str): The destination folder path (default is 'media' subfolder).
            move_all (bool): Flag indicating whether to move all files or only media files.
        """
        try:
            if destination_folder is None:
                destination_folder = os.path.join(self.root_folder, 'media')
            # self.dest_folder = destination_folder

            if self.all_files:
                move_all = True
            self.move_files_to_destination(destination_folder, move_all)
            self.show_moved_stats()
        except Exception as e:
            print(f"Error gathering media: {e}")

    def show_moved_stats(self):
        """
        Display statistics on the moved media files.
        """
        try:
            total_files = len(self.moved_files)
            total_size = sum(os.path.getsize(file) for file in self.moved_files.values())

            print("\n", " "*20, "Total Stats")
            print("=" * 60)
            print(f"Total Moved Files: {total_files}")
            print(f"Total Size of Moved Files: {total_size} bytes ({self.format_size(total_size)})")
        except Exception as e:
            print(f"Error displaying stats in Media Gatherer: {e}")
    
    def generate_moved_stats_csv(self, csv_path=None):
        """
        Under Construction
        """
        try:
            if not self.moved_files:
                print("\n\tNOTHING MOVED ^<>^ NO CSV TO GENERATE\n")
                return

            current_date = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_file_path = os.path.join(self.root_folder, f'{self.root_folder}_moved_files_stats_{current_date}.csv') if csv_path is None else csv_path

            with open(csv_file_path, 'w', newline='', encoding="utf-8") as csvfile:
                fieldnames = ['File Name', 'File Type', 'File Size (Bytes)', 'File Size (Human Readable)',
                              'Creation Date', 'Modification Date', 'Source Folder', "Destination Folder"]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()

                for file in self.file_stats:
                    # print(file)
                    writer.writerow({
                        'File Name': file["File Name"],
                        'File Type': file["File Type"],
                        'File Size (Bytes)': file["File Size (Bytes)"],
                        'File Size (Human Readable)': file["File Size (Human Readable)"],
                        'Creation Date': file["Creation Date"],
                        'Modification Date': file["Modification Date"],
                        'Source Folder': file["Source Folder"],
                        'Destination Folder': self.moved_files[file["Source Folder"] + "\\" + file["File Name"]]
                    }
                        
                    )

            print(f"\nStats CSV For Moved file generated: {csv_file_path}")
        except Exception as e:
            print(f"Error generating stats CSV: {e}")
