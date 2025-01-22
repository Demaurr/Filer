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
        dest_folder (str): The destination folder path.

    Methods:
        is_media_file(filename): Check if a given filename has a valid media file extension.
        create_media_folder(destination_folder): Create the specified destination folder if it doesn't exist.
        move_files_to_destination(destination_folder, move_all_files=False): Move media files to the specified destination folder.
        show_stats(): Display statistics on the moved media files.
        format_size(size): Format file size in a human-readable format.
        show_file_size_distribution(): Display the distribution of file sizes.
        generate_stats_csv(): Generate a CSV file containing detailed statistics of moved media files.
        gather_media(destination_folder=None, move_all=False): Perform the media gathering process with options for destination folder and moving all files.
        _batch_move_files(files_to_move, destination_folder, batch_size=100, delay=1): Move files in batches with a delay between each batch to avoid system overload.

    Example Usage:
        root_directory = input("Enter the root directory: ")
        destination_folder = input("Enter the destination folder (optional): ")
        move_all_files = input("Move all files? (yes/no): ").lower() == 'yes'
        
        media_gatherer = MediaGatherer(root_directory)
        media_gatherer.gather_media(destination_folder, move_all_files)
        media_gatherer.generate_stats_csv()
        media_gatherer.show_stats()
        media_gatherer.show_file_size_distribution()
        media_gatherer.generate_summary_html("Media_Summary.html")
    """

    def __init__(
            self, 
            root_folder, 
            destination_folder=None, 
            skip_folders=None, 
            media_extensions=['.mp3', '.mp4', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.gif'], 
            all_files=False
        ):
        if skip_folders is None:
            skip_folders = []
        if destination_folder is None:
            destination_folder = os.path.join(root_folder, 'media')
        skip_folders.append(destination_folder)
        # print(skip_folders)
        self.root_folder = root_folder
        self.media_extensions = media_extensions
        super().__init__(
            root_folder, 
            media_extensions=media_extensions, 
            skip_folders=skip_folders, 
            all_files=all_files)
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

    def get_files_to_move(self, move_all_files=False) -> list:
        files_to_move = [stat for stat in self.file_stats if move_all_files or self.is_media_file(stat['File Name'])]
        return files_to_move

    def move_files_to_destination(self, files_to_move, destination_folder, move_all_files=False):
        """
        Move media files to the specified destination folder.

        Parameters:
            destination_folder (str): The destination folder path.
            move_all_files (bool): Flag indicating whether to move all files or only media files.
        """
        try:
            self.create_media_folder(destination_folder)
            # files_to_move = self.get_files_to_move(move_all_files=move_all_files)
            if not files_to_move:
                print("\nNO FILEs TO MOVE. ^<>^")
                return -1

            for file_stat in files_to_move:
                source_path = os.path.join(file_stat['Source Folder'], file_stat['File Name'])
                destination_path = os.path.join(destination_folder, file_stat['File Name'])

                if source_path == destination_path:
                    continue

                # print("The Suffix remover: ",self.remove_number_suffix(os.path.splitext(destination_path)[0]))
                # if os.path.exists(destination_path):
                #     destination_path = self.rename_if_exists(destination_path)
                destination_path = self.handle_file_conflict(destination_path)

                shutil.move(source_path, destination_path)

                # storing source path and destination path of the moved files in a dict {source_path: destination}
                self.moved_files[source_path] = destination_path

                print(f"\tMoved: {file_stat['File Name']}")

            print(f"Total Files Moved: {len(files_to_move)}")
        except Exception as e:
            print(f"Error moving files to destination folder: {e}")

    def handle_file_conflict(self, destination_path):
        """
        Checks if a file exists at the destination and renames it if necessary.
        
        Parameters:
            source_path (str): The source file path.
            destination_path (str): The destination file path.

        Returns:
            str: The new destination file path (with a unique name if needed).
        """
        if os.path.exists(destination_path):
            return self.rename_if_exists(destination_path)
        return destination_path
    
    def rename_if_exists(self, filename):
        """
        Rename a filename if it already exists in the destination folder.

        Parameters:
            filename (str): The filename to check and rename if necessary.

        Returns:
            str: The new filename after renaming.
        """
        
        base_name, extension = os.path.splitext(filename)
        base_name = self.remove_number_suffix(base_name)
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

    def gather_media(
            self, 
            destination_folder=None, 
            move_all=False,
            batch_size=15,
            time_delay=0.1
        ):
        """
        Perform the media gathering process with options for destination folder and moving all files.

        Parameters:
            destination_folder (str): The destination folder path (default is 'media' subfolder).
            move_all (bool): Flag indicating whether to move all files or only media files.
        """
        try:
            if destination_folder is None:
                # destination_folder = os.path.join(self.root_folder, folder_name)
                destination_folder = self.dest_folder

            if self.all_files:
                move_all = True

            files_to_move = self.get_files_to_move(move_all)

            if len(self.file_stats) > 100:
                self._batch_move_files(files_to_move, destination_folder, batch_size=batch_size, delay=time_delay)
            else:
                self.move_files_to_destination(files_to_move, destination_folder, move_all)
            self.show_moved_stats()
        except Exception as e:
            print(f"Error gathering media: {e}")

    def _batch_move_files(self, files_to_move, destination_folder, batch_size=100, delay=0.1):
        from time import sleep
        """
        Move files in batches with a delay between each batch to avoid system overload.
        Parameters:
            files_to_move (list): list of dicts containing file info
            destination_folder (str): path to destination folder
            batch_size (int): number of files to move per batch before delay
            delay (int): number of seconds to delay the moving of files to remove load on pc
        """
        try:
            for i in range(0, len(files_to_move), batch_size):
                batch = files_to_move[i:i + batch_size]
                # for file_stat in batch:
                self.move_files_to_destination(destination_folder=destination_folder, files_to_move=batch)
                print(f"Moved batch {i // batch_size + 1} of {len(files_to_move) // batch_size + 1}")
                sleep(delay)
        except Exception as e:
            print(f"An Exception Occurred while Batch Moving {e}")

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
                return -1

            current_date = datetime.now().strftime('%Y%m%d_%H%M%S')
            csv_file_path = os.path.join(self.root_folder, f'{self.root_folder}_moved_files_stats_{current_date}.csv') if csv_path is None else csv_path

            with open(csv_file_path, 'w', newline='', encoding="utf-8") as csvfile:
                fieldnames = ['File Name', 'File Type', 'File Size (Bytes)', 'File Size (Human Readable)',
                              'Creation Date', 'Modification Date', 'Moved Date', 'Source Folder', "Destination Folder"]
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
                        'Moved Date': str(current_date),
                        'Source Folder': file["Source Folder"],
                        'Destination Folder': self.moved_files[file["Source Folder"] + "\\" + file["File Name"]]
                    })

            print(f"\nStats CSV For Moved file generated: {csv_file_path}")
        except Exception as e:
            print(f"Error generating stats CSV: {e}")

class Gatherer(MediaGatherer):
    """
    A class for gathering files of any specified extensions from a specified root folder.

    Attributes:
        extensions (list): List of custom file extensions to gather.
        destination_folder (str): Folder where the files should be stored after being gathered
             Default: 'Extracted Files' folder under root

    Methods:
        set_extensions(extensions): To Set custom file extensions to gather.
        gather_files(destination_folder=None, move_all=False): Perform the media gathering process with custom extensions.

    Example Usage:
        root_directory = input("Enter the root directory: ")
        destination_folder = input("Enter the destination folder (optional): ")
        extensions = input("Enter custom file extensions (comma-separated): ").split(',')

        custom_gatherer = CustomMediaGatherer(root_directory, destination_folder=destination_folder, extensions=extensions)
        custom_gatherer.gather_files()
        custom_gatherer.generate_moved_stats_csv()
    """

    def __init__(
            self, 
            root_folder, 
            destination_folder=None, 
            extensions=None, 
            skip_folders=None, 
            all_files=False
        ):
        if extensions is None:
            extensions = []
        if skip_folders is None:
            skip_folders = []
        if destination_folder is None:
            destination_folder = os.path.join(root_folder,"Extracted Files")
        self.destination_folder =  destination_folder

        self.extensions = self.set_extensions(extensions)
        # self.skip_folders = skip_folders.append(destination_folder)
        # self.destination_folder = destination_folder
        super().__init__(
            root_folder, 
            destination_folder=self.destination_folder, 
            media_extensions=self.extensions, 
            skip_folders=skip_folders, 
            all_files=all_files
            )
        self.extensions = []

    def set_extensions(self, extensions):
        """
        Set custom file extensions to gather.

        Parameters:
            extensions (list): List of custom file extensions.
        """
        while any(element == "" for element in extensions):
            extensions = input("Enter custom file extensions (comma-separated): ").split(',')
        return extensions


    def gather_files(
            self, 
            destination_folder=None, 
            all_files=False,
            batch_size=100,
            time_delay=0.5
            ):
        """
        Perform the media gathering process with custom extensions.

        Parameters:
            destination_folder (str): The destination folder path (default is 'Extracted Files' subfolder).
            move_all (bool): Flag indicating whether to move all files or only particular extension files.
        """
        try:
            super().gather_media(
                destination_folder=destination_folder, 
                move_all=all_files,
                batch_size=batch_size,
                time_delay=time_delay
                )
        except Exception as e:
            print(f"Error gathering media with custom extensions: {e}")

# if __name__ == "__main__":
#     root_folder = r"Filer\Filing\tests\have_no_files"
#     destination_folder = r'Filer\Filing\tests\have_files'
#     gatherer = Gatherer(root_folder=root_folder, destination_folder=destination_folder, all_files=True)
#     gatherer.gather_files()
#     gatherer.generate_file_stats_csv(r"Filer\Filing\tests\have_files")
    # gatherer.generate_moved_stats_csv(r"Filer\Filing\tests\have_files")
    # gatherer.generate_file_stats_html(r"Filer\Filing\tests\have_files")
    # gatherer.generate_summary_html(r"Filer\Filing\tests\have_files")
