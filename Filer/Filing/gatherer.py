import os
import shutil
import csv
from datetime import datetime
from Filing.filestatser import FileStatsCollector
from Filing.journal import MoveJournal, JOURNAL_DIR_NAME, undo_moves
from Filing.mover import plan_moves, execute_moves, unique_destination, remove_number_suffix
from Filing.organizer import make_organizer

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
            all_files=False,
            organize_by=None,
            journal=True,
            journal_dir=None,
            progress=None,
            verbose=True,
            **filter_options
        ):
        # progress: function(stage, done, total) called while scanning and moving (see Filing.progress).
        # verbose: set False to silence the informational messages (errors are always printed).
        # organize_by: preset ('type', 'date', 'size', 'extension'), a pattern like '{type}/{year}'
        #     (see Filing.organizer), an Organizer, or a function(stats dict) -> relative folder.
        # journal / journal_dir: keep a JSON log of every move so it can be undone with
        #     undo_last_move(). Default folder: '<root_folder>/.filer_journal'.
        # filter_options: min_size, max_size, modified_after, modified_before, name_glob, name_regex
        #     (see FileStatsCollector).
        skip_folders = list(skip_folders) if skip_folders else []
        if destination_folder is None:
            destination_folder = os.path.join(root_folder, 'media')
        skip_folders.append(destination_folder)
        skip_folders.append(JOURNAL_DIR_NAME)
        if journal_dir:
            skip_folders.append(journal_dir)
        self.root_folder = root_folder
        self.media_extensions = media_extensions
        self.organizer = make_organizer(organize_by)
        self.journal_enabled = journal
        self.journal_dir = journal_dir
        self._journal = None
        self.last_journal_path = None
        self.failed_moves = []
        self._move_offset = 0
        self._move_total = None
        super().__init__(
            root_folder,
            media_extensions=media_extensions,
            skip_folders=skip_folders,
            all_files=all_files,
            progress=progress,
            verbose=verbose,
            **filter_options)
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

    def _destination_path(self, file_stat, destination_folder):
        """Where a file goes: below destination_folder, in the subfolder chosen by organize_by (if any)."""
        if self.organizer is not None:
            return self.organizer.destination(file_stat, destination_folder)
        return os.path.join(destination_folder, file_stat['File Name'])

    def preview_moves(self, files_to_move=None, destination_folder=None, move_all_files=False):
        """
        Dry run: work out where every file would go, without touching anything.
        Name conflicts (also between files in this plan) are already resolved in the result.

        Returns:
            dict: {source_path: destination_path}
        """
        if destination_folder is None:
            destination_folder = self.dest_folder
        if files_to_move is None:
            files_to_move = self.get_files_to_move(move_all_files or self.all_files)
        pairs = [
            (os.path.join(stat['Source Folder'], stat['File Name']), self._destination_path(stat, destination_folder))
            for stat in files_to_move
        ]
        return dict(plan_moves(pairs, progress=self._progress if self._progress.enabled else None))

    def move_files_to_destination(self, files_to_move, destination_folder, move_all_files=False, dry_run=False):
        """
        Move media files to the specified destination folder.

        Parameters:
            files_to_move (list): File stats dicts to move (see get_files_to_move).
            destination_folder (str): The destination folder path.
            move_all_files (bool): Flag indicating whether to move all files or only media files.
            dry_run (bool): Only return the planned moves {source: destination}; move nothing.
        """
        try:
            if not files_to_move:
                self._say("\nNO FILEs TO MOVE. ^<>^")
                return -1

            if dry_run:
                return self.preview_moves(files_to_move, destination_folder)

            pairs = [
                (os.path.join(stat['Source Folder'], stat['File Name']), self._destination_path(stat, destination_folder))
                for stat in files_to_move
            ]
            plan = plan_moves(pairs)
            self.dest_folder = destination_folder
            move_progress = None
            if self._progress.enabled:
                move_progress = self._progress.offset(self._move_offset, self._move_total or len(plan))
            moved, failed = execute_moves(plan, journal=self._get_journal(), verbose=self.verbose, progress=move_progress,
                                          allowed_roots=[self.root_folder, destination_folder])
            # storing source path and destination path of the moved files in a dict {source_path: destination}
            self.moved_files.update(moved)
            self.failed_moves.extend(failed)

            self._say(f"Total Files Moved: {len(moved)}")
        except Exception as e:
            print(f"Error moving files to destination folder: {e}")

    def _get_journal(self):
        """The journal of the current gather run (created on first use), or None if journaling is off."""
        if not self.journal_enabled:
            return None
        if self._journal is None:
            self._journal = MoveJournal(self.root_folder, self.journal_dir, operation="move")
        return self._journal

    def undo_last_move(self, journal=None, dry_run=False):
        """
        Undo the most recent gather (or the given journal file): move the files back to where they came from.
        Files are never overwritten; entries that cannot be restored are reported and left in the journal.

        Parameters:
            journal (str): Path of a journal JSON file. Default: newest journal not yet undone.
            dry_run (bool): Only report what would be restored.

        Returns:
            dict: {"journal": path, "restored": {moved_path: original_path}, "skipped": [...], "dry_run": bool}
        """
        result = undo_moves(journal, root_folder=self.root_folder, journal_dir=self.journal_dir, dry_run=dry_run,
                            allowed_roots=[self.root_folder, self.dest_folder])
        verb = "Would restore" if dry_run else "Restored"
        self._say(f"{verb} {len(result['restored'])} file(s) from {result['journal']}")
        for item in result["skipped"]:
            self._say(f"\tSkipped {item['source']}: {item['reason']}")
        if not dry_run:
            for original in result["restored"].values():
                self.moved_files.pop(original, None)
        return result

    def handle_file_conflict(self, destination_path, reserved=None):
        """
        Checks if a file exists at the destination and renames it if necessary.

        Parameters:
            destination_path (str): The destination file path.
            reserved (set): Optional planned-but-not-yet-existing paths to avoid as well.

        Returns:
            str: The new destination file path (with a unique name if needed).
        """
        return unique_destination(destination_path, reserved)

    def rename_if_exists(self, filename, reserved=None):
        """
        Rename a filename if it already exists in the destination folder ('name(1).ext', 'name(2).ext'...).

        Parameters:
            filename (str): The filename to check and rename if necessary.

        Returns:
            str: The new filename after renaming.
        """
        return unique_destination(filename, reserved)

    @staticmethod
    def remove_number_suffix(basename):
        """
        Remove the suffix "(number)" from the basename if it exists.

        Parameters:
            basename (str): The basename of the file.

        Returns:
            str: The basename without the "(number)" suffix.
        """
        return remove_number_suffix(basename)

    def gather_media(
            self,
            destination_folder=None,
            move_all=False,
            batch_size=15,
            time_delay=0.1,
            dry_run=False
        ):
        """
        Perform the media gathering process with options for destination folder and moving all files.

        Parameters:
            destination_folder (str): The destination folder path (default is 'media' subfolder).
            move_all (bool): Flag indicating whether to move all files or only media files.
            batch_size (int): Files per batch when moving many files.
            time_delay (float): Seconds to wait between batches.
            dry_run (bool): Move nothing; return the planned moves {source: destination}.

        Returns:
            dict: With dry_run, the planned moves; otherwise the moves done so far {source: destination}.
        """
        try:
            if destination_folder is None:
                # destination_folder = os.path.join(self.root_folder, folder_name)
                destination_folder = self.dest_folder

            if self.all_files:
                move_all = True

            files_to_move = self.get_files_to_move(move_all)

            if dry_run:
                plan = self.preview_moves(files_to_move, destination_folder)
                self._say(f"\nDRY RUN: {len(plan)} file(s) would be moved to {destination_folder} (nothing was changed)")
                for source, destination in list(plan.items())[:20]:
                    self._say(f"\t{source}  ->  {destination}")
                if len(plan) > 20:
                    self._say(f"\t... and {len(plan) - 20} more")
                return plan

            self._journal = None  # every run gets its own journal
            if len(self.file_stats) > 100:
                self._batch_move_files(files_to_move, destination_folder, batch_size=batch_size, delay=time_delay)
            else:
                self.move_files_to_destination(files_to_move, destination_folder, move_all)
            if self._journal is not None and self._journal.path:
                self.last_journal_path = self._journal.path
                self._say(f"Journal saved: {self.last_journal_path} (undo with undo_last_move())")
            if self.verbose:
                self.show_moved_stats()
            return dict(self.moved_files)
        except Exception as e:
            print(f"Error gathering media: {e}")

    def _batch_move_files(self, files_to_move, destination_folder, batch_size=100, delay=0.1):
        """
        Move files in batches with a delay between each batch to avoid system overload.
        Parameters:
            files_to_move (list): list of dicts containing file info
            destination_folder (str): path to destination folder
            batch_size (int): number of files to move per batch before delay
            delay (int): number of seconds to delay the moving of files to remove load on pc
        """
        from time import sleep
        try:
            self._move_total = len(files_to_move)  # progress counts continue across batches
            for i in range(0, len(files_to_move), batch_size):
                batch = files_to_move[i:i + batch_size]
                self._move_offset = i
                self.move_files_to_destination(destination_folder=destination_folder, files_to_move=batch)
                self._say(f"Moved batch {i // batch_size + 1} of {len(files_to_move) // batch_size + 1}")
                if delay:
                    sleep(delay)
        except Exception as e:
            print(f"An Exception Occurred while Batch Moving {e}")
        finally:
            self._move_offset, self._move_total = 0, None

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
            all_files=False,
            organize_by=None,
            journal=True,
            journal_dir=None,
            progress=None,
            verbose=True,
            **filter_options
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
            all_files=all_files,
            organize_by=organize_by,
            journal=journal,
            journal_dir=journal_dir,
            progress=progress,
            verbose=verbose,
            **filter_options
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
            time_delay=0.5,
            dry_run=False
            ):
        """
        Perform the media gathering process with custom extensions.

        Parameters:
            destination_folder (str): The destination folder path (default is 'Extracted Files' subfolder).
            all_files (bool): Flag indicating whether to move all files or only particular extension files.
            dry_run (bool): Move nothing; return the planned moves {source: destination}.
        """
        try:
            return super().gather_media(
                destination_folder=destination_folder,
                move_all=all_files,
                batch_size=batch_size,
                time_delay=time_delay,
                dry_run=dry_run
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
