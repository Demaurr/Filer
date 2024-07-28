"""
Creates a csv File with the File And Folder Statistics
To Record Which file is from Which Folder

"""

import os
import csv
from datetime import datetime
from collections import Counter
from multiprocessing import Pool

class FileStatsCollector:
    """
    A class for gathering statistics on media files within a specified root folder.

    Attributes:
        root_folder (str): The root folder to scan for media files.
        media_extensions (list): List of allowed media file extensions.
        all_files (bool): Flag indicating whether to gather statistics for all files or only media files.
        skip_folders (list): List of folders to skip while gathering statistics.

    Methods:
        is_media_file(filename): Check if a given filename has a valid media file extension.
        check_root_folder(folder): Check if the specified root folder exists; raise an error if it doesn't.
        format_size(size): Format file size in a human-readable format.
        gather_file_stats(): Scan the root folder for media files, collect file statistics, and return the obtained statistics.
        generate_file_stats_csv(): Generate a CSV file containing detailed statistics of media files.
        show_file_size_distribution(): Display the distribution of file sizes.
        print_summary_stats(): Print a summary of file statistics, including total files, total average size, most occurring file type, and total size.
        get_file_typedist(): Get the distribution of file types.
        get_folders_info(): Get information about the distribution of files across folders.
        print_file_types(): Print the distribution of file types.
        get_folder_size_distribution(): Calculate the distribution of folder sizes.
        show_folder_size_distribution(): Print the distribution of folder sizes.
        generate_file_stats_html(): Generates a Html file with all the stats like [Fila Name, Source Folder, File Sizes...].
        generate_summary_html(): Generates a Html file with Summary Statistics like File Type counts, Folder Counts etc.
        sort_by_key(): Returns a Sorted List of Dicts with Respected to a Given Key. 

    Example Usage:
        path = input("Enter a Root Path To Gather Info: ")
        skip_folders = ['.git', '__pycache__', '.idea', 'venv']  # Example folders to skip
        all_files = input("Gather All Files Info: ").lower() == 'yes'
        file_stats_collector = FileStatsCollector(path, all_files=all_files, skip_folders=skip_folders)
        file_stats_collector.generate_file_stats_csv()
        file_stats_collector.show_file_size_distribution()
        file_stats_collector.print_summary_stats()
    """
    def __init__(self, root_folder, media_extensions=['.mp3', '.mp4', '.avi', '.mkv', '.jpg', '.jpeg', '.png', '.gif'], all_files=False, skip_folders=[]):
        self.root_folder = self.check_root_folder(root_folder)
        self.media_extensions = media_extensions
        self.all_files = all_files
        self.skip_folders = skip_folders
        self.file_stats = self._gather_file_stats()
        self.file_types = [stat["File Type"] for stat in self.file_stats]

    def get_styles(self):
        styles = f"""
body {{
    font-family: Arial, sans-serif;
    background: black;
}}

.container {{
    width: 80%;
    margin: auto;
    padding: 20px;
}}

table {{
    border-collapse: collapse;
    width: 100%;
    margin-bottom: 20px;
}}

th, td, p {{
    color: white;
}}

.root_foldername {{
    font-size: 18px;
    font-weight: bold;
    text-decoration:underline;
}}

th, td {{
    padding: 8px;
    text-align: left;
    border-bottom: 1px solid #ddd;
}}

/* th {{
    background-color: Black;
    
}} */

h1 {{
    text-align: center;
    font-size: 40px;
}}

h1, h2 {{
    font-family: sans-serif;
    color: red;
    text-decoration: underline;
}}

h2 {{
    font-size: 28px;
}}

/* Styles for summary statistics report */
.summary-table {{
    margin-bottom: 30px;
}}

/* Styles for file statistics report */
.file-stats-table {{
    margin-bottom: 30px;
}}

.datetime {{
    text-align: center;
    font-weight: bold;
    font-size: 18px;
}}

"""
        return styles

    def is_media_file(self, filename):
        """
        Check if a given filename has a valid media file extension.

        Args:
            filename (str): The name of the file to check.

        Returns:
            bool: True if the file has a valid media extension, False otherwise.
        """
        return any(filename.lower().endswith(ext) for ext in self.media_extensions)
    
    def check_root_folder(self, folder):
        """
        Check if the specified root folder exists; raise an error if it doesn't.

        Args:
            folder (str): The root folder to check.

        Raises:
            FileNotFoundError: If the specified root folder does not exist.
        """
        if os.path.exists(folder):
            return folder
        else:
            raise FileNotFoundError(f"Folder '{folder}' doesn't exist.")
        
    def get_root_folder():
        """
        Prompt the user for the root folder path and validate it.
        """
        while True:
            root_folder = input("Enter the root folder path to gather information: ").strip()
            if os.path.exists(root_folder) and os.path.isdir(root_folder):
                return root_folder
            else:
                print("Error: Invalid folder path. Please enter a valid existing folder path: ")

    def get_skip_folders():
        """
        Prompt the user for skip folders and split them into a list.
        """
        skip_folders_input = input("Enter folders to skip separated by commas (if any): ").strip()
        return [folder.strip() for folder in skip_folders_input.split(',')]

    def get_all_files():
        """
        Prompt the user whether to gather information for all files.
        """
        all_files_input = input("Gather information for all files? (yes/no): ").strip().lower()
        return all_files_input == 'yes'

    @staticmethod
    def format_size(size):
        """
        Format file size in a human-readable format.

        Args:
            size (float): The size of the file in bytes.

        Returns:
            str: The formatted file size with appropriate units (B, KB, MB, GB, TB).
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size < 1024.0:
                break
            size /= 1024.0
        return "{:.2f} {}".format(size, unit)

    def process_files(self, foldername, all_files, is_media_file, format_size):
        """
        Process files within a folder and collect file statistics.

        Args:
            foldername (str): Path of the folder to process.
            all_files (bool): Flag indicating whether to collect statistics for all files or only media files.
            is_media_file (function): Function to check if a file is a media file.
            format_size (function): Function to format file size.

        Returns:
            list: A list of dictionaries containing file statistics.
        """
        folder_stats = []
        counter = 0
        try:
            with os.scandir(foldername) as entries:
                print("Gathering data from:", foldername)
                for entry in entries:
                    if entry.is_file():
                        filename = entry.name
                        source_path = os.path.join(foldername, filename)
                        if all_files or is_media_file(filename):
                            counter += 1
                            folder_stats.append({
                                'File Name': filename,
                                'File Type': os.path.splitext(filename)[1].lower(),
                                'File Size (Bytes)': entry.stat().st_size,
                                'File Size (Human Readable)': format_size(entry.stat().st_size),
                                'Creation Date': datetime.fromtimestamp(entry.stat().st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                                'Modification Date': datetime.fromtimestamp(entry.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                                'Source Folder': foldername
                            })
                            
                if counter:
                    print(f"\t>Total Files From {foldername}: {counter}")
        except Exception as e:
            print(f"Error gathering file stats in folder '{foldername}': {e}")
        return folder_stats

    def _gather_file_stats(self):
        """
        Scan the root folder for media files, collect file statistics, and return the obtained statistics.

        Returns:
            list: A list of dictionaries containing file statistics.
        """
        all_files = self.all_files
        file_stats = []

        try:
            with Pool() as pool:
                folder_stats_lists = pool.starmap(self.process_files, [(foldername, all_files, self.is_media_file, self.format_size) for foldername, _, _ in os.walk(self.root_folder) if all(skip_folder not in foldername for skip_folder in self.skip_folders)])
                file_stats = [file_stat for folder_stats in folder_stats_lists for file_stat in folder_stats]
        except Exception as e:
            print(f"Unexpected error occurred: {e}")

        return file_stats
    
    @staticmethod
    def get_file_stats(file_path):
        """
        Collects and returns statistics for a given file path.
        
        Args:
            file_path (str): The path to the file for which statistics are to be collected.
            
        Returns:
            list: A list containing a dictionary with the file's statistics.
        """
        file_stats = []
        try:
            filename = os.path.basename(file_path)
            foldername = os.path.dirname(file_path)
            file_stat = os.stat(file_path)
            file_stats.append({
                'File Name': filename,
                'File Type': os.path.splitext(filename)[1].lower(),
                'File Size (Bytes)': file_stat.st_size,
                'File Size (Human Readable)': FileStatsCollector.format_size(file_stat.st_size),
                'Creation Date': datetime.fromtimestamp(file_stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S'),
                'Modification Date': datetime.fromtimestamp(file_stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                'Source Folder': foldername
            })
            # print(file_stats)
            return file_stats
        except Exception as e:
            print(f"An Exception Occurred in get_file_stats: {e}")
    
    @staticmethod
    def sort_by_key(file_stats, key, reverse=False):
        """
        Sorts the list of dictionaries by the specified key.

        Parameters:
            file_stats (list): The list of dictionaries to be sorted.
            key (str): The key by which to sort the dictionaries.
            reverse (bool, optional): Whether to sort in reverse order. Defaults to False.

        Returns:
            list: The sorted list of dictionaries.
        """
        try:
            return sorted(file_stats, key=lambda x: x[key], reverse=reverse)
        except KeyError:
            print(f"Key '{key}' not found in file_stats dictionaries.")
            return file_stats
        except Exception as e:
            print(f"An error occurred while sorting by key '{key}': {e}")
            return file_stats

    def generate_file_stats_html(self, html_path=None):
        """
        Generate a pretty HTML report of file statistics.
        """
        if not self.file_stats:
            return

        if len(self.file_stats) > 1500:
            user_input = input("Warning: Generating HTML report for a large number of files can consume a significant amount of memory. Do you want to continue? (yes/no): ").strip().lower()
            if user_input != 'yes':
                print("HTML report generation aborted.")
                return

        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>File Statistics Report</title>
            <!--<link rel="stylesheet" href="styles.css">  Link to external CSS file -->
            <style>
            { self.get_styles() }
            </style>
        </head>
        <body>
            <h1>File Statistics Report</h1>
            <p class="datetime">Generated on: { datetime.now().strftime("%d-%b-%Y %H:%M:%S") }</p>
            <table>
                <tr>
                    <th>File Name</th>
                    <th>File Type</th>
                    <th>File Size (Bytes)</th>
                    <th>File Size (Human Readable)</th>
                    <th>Creation Date</th>
                    <th>Modification Date</th>
                    <th>Source Folder</th>
                </tr>
        """
        file_stats = self.sort_by_key(self.file_stats, "File Size (Bytes)", reverse=True)
        for stat in file_stats:
            html_content += f"""
                <tr>
                    <td>{stat['File Name']}</td>
                    <td>{stat['File Type']}</td>
                    <td>{stat['File Size (Bytes)']}</td>
                    <td>{stat['File Size (Human Readable)']}</td>
                    <td>{stat['Creation Date']}</td>
                    <td>{stat['Modification Date']}</td>
                    <td>{stat['Source Folder']}</td>
                </tr>
            """

        html_content += """
            </table>
        </body>
        </html>
        """

        # Writing HTML content to a file
        html_file_name = f'{os.path.basename(self.root_folder)}_file_stats_report.html'
        html_file_path = os.path.join(self.root_folder, html_file_name) if html_path is None else html_path
        try:
            with open(html_file_path, 'w', encoding='utf-8') as html_file:
                html_file.write(html_content)
            print(f"\nHTML report generated: {html_file_path}")
        except Exception as e:
            print(f"Error generating HTML report: {e}")

    def generate_file_stats_csv(self, csv_path=None):
        """
        Generate a CSV file containing detailed statistics of media files or all files.
        """
        if not self.file_stats:
            print("NO Data FOUND: Nothing To Create Csv of")
            return -1

        root_folder_basename = os.path.basename(self.root_folder)

        if not root_folder_basename:  # Handle drive letters
            drive_letter = self.root_folder.split(":")[0]
            root_folder_basename = f"Drive_{drive_letter}"

        current_time= datetime.now().strftime('%Y%m%d_%H%M%S')
        csv_file_name = f'{root_folder_basename}_files_stats_{current_time}.csv'
        csv_file_path = os.path.join(self.root_folder, csv_file_name) if csv_path is None else csv_path

        try:
            with open(csv_file_path, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['File Name', 'File Type', 'File Size (Bytes)', 'File Size (Human Readable)',
                              'Creation Date', 'Modification Date', 'Source Folder']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

                writer.writeheader()

                for stat in self.file_stats:
                    writer.writerow({
                        'File Name': stat['File Name'],
                        'File Type': stat['File Type'],
                        'File Size (Bytes)': stat['File Size (Bytes)'],
                        'File Size (Human Readable)': stat['File Size (Human Readable)'],
                        'Creation Date': stat['Creation Date'],
                        'Modification Date': stat['Modification Date'],
                        'Source Folder': stat['Source Folder']
                    })

            print(f"\nFile Stats CSV file generated: {csv_file_path}")
        except Exception as e:
            print(f"Error generating file stats CSV: {e}")

    def show_file_size_distribution(self):
        """
        Display the distribution of file sizes.
        """
        # Define file size ranges
        size_ranges = {
            '0-1KB': (0, 1024),
            '1KB-100KB': (1024, 1024 * 100),
            '100KB-1MB': (1024 * 100, 1024 * 1024),
            '1MB-10MB': (1024 * 1024, 1024 * 1024 * 10),
            '10MB-100MB': (1024 * 1024 * 10, 1024 * 1024 * 100),
            '100MB+': (1024 * 1024 * 100, 1024 * 1024 * 500),  # 100MB to 500MB
            '500MB+': (1024 * 1024 * 500, float('inf'))  # 500MB+
        }

        # Initialize dictionary to store file count in each range
        size_distribution = {size_range: 0 for size_range in size_ranges}

        for stat in self.file_stats:
            file_size = stat['File Size (Bytes)']

            # Determine the size range for the current file size
            for size_range, (min_size, max_size) in size_ranges.items():
                if min_size <= file_size < max_size:
                    size_distribution[size_range] += 1
                    break

        print("\nFile Size Distribution (No Files Moved):")
        for size_range, count in size_distribution.items():
            print(f"{size_range}: {count}")

    def get_folder_size_distribution(self):
        """
        Get the distribution of folder sizes based on the total size of files contained within each folder.

        Returns:
            dict: A dictionary containing the folder paths as keys and their corresponding total size as values in Bytes.
        """
        folder_sizes = {}  # Dictionary to store folder sizes

        for stat in self.file_stats:
            folder_path = stat['Source Folder']
            file_size = stat['File Size (Bytes)']

            # Update total size for the folder
            folder_sizes[folder_path] = folder_sizes.get(folder_path, 0) + file_size

        return folder_sizes
    
    def show_folder_size_distribution(self):
        folder_size = self.get_folder_size_distribution()
        print("\nFolders Size Distribution: ")
        for folder, size in folder_size.items():
            print(f"{folder} --> {self.format_size(size)}")



    def get_file_typedist(self):
        """
        Get the distribution of file types in the file statistics.

        Returns:
            Counter: A Counter object containing the count of each file type.
        """
        file_types = Counter(self.file_types)
        return file_types

    def get_folders_info(self):
        """
        Get information about the distribution of files across folders.

        Returns:
            Counter: A Counter object containing the count of files in each folder.
        """
        folder_info = Counter([stat['Source Folder'] for stat in self.file_stats])
        return folder_info

    
    def print_file_types(self):
        file_types = self.get_file_typedist()
        print("\nFile Types Distribution: ")
        for file, count in file_types.items():
            print(file, "-->", count)
    
    def generate_summary_html(self, html_path=None):
        """
        Generate an HTML summary report based on the provided summary statistics, file type statistics,
        and folder file counts.

        Args:
            html_path (str, optional): Path to save the HTML report. If not provided, the report will be saved in the root folder.
        """
        if not len(self.file_stats):
            print("NO Data FOUND: Nothing To Create Html of")
            return -1
        total_files, total_folders, total_size, avg_size, most_occurring_file_type = self.get_summary_stats()
        file_type_stats = sorted(self.get_file_typedist().items(), key=lambda x: x[1], reverse=True)
        folder_file_counts = sorted(self.get_folders_info().items(), key=lambda x: x[1], reverse=True)
        folder_size = self.get_folder_size_distribution()

        # HTML Code For File Statistics Table
        file_type_table = "<table><tr><th>File Type</th><th>Count</th></tr>"
        for file_type, counts in file_type_stats:
            file_type_table += f"<tr><td>{file_type}</td><td>{counts}</td></tr>\n\t"
        file_type_table += "</table>"

        # HTML Code For Folder Table
        folder_file_counts_table = "<table><tr><th>Folder</th><th>File Count</th><th>Size</th></tr>"
        for folder, files in folder_file_counts:
            if len(folder) > 20:
                folder_splitted = folder.split("\\")
                abbreviated_folder = folder_splitted[0] + "\\...." + "\\".join(folder_splitted[-2:])
            else:
                abbreviated_folder = folder
            folder_file_counts_table += f"<tr><td>{abbreviated_folder}</td><td>{files}</td><td>{self.format_size(folder_size[folder])}</td></tr>\n\t"
        folder_file_counts_table += "</table>"

        # Complete Html code
        html_content = f"""
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Summary Statistics Report</title>
            <!--<link rel="stylesheet" href="styles.css">  Link to external CSS file -->
            <style>
            { self.get_styles() }
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Summary Statistics {" (Media Files)" if not self.all_files else ""}</h1>
                <p class="datetime">Generated on: { datetime.now().strftime("%d-%b-%Y %H:%M:%S") }</p>
                <p class="root_foldername">Stats For Folder: {self.root_folder}</p>
                <table>
                    <tr><th>Total Files</th><td>{total_files}</td></tr>
                    <tr><th>Total Folders (With Files)</th><td>{total_folders}</td></tr>
                    <tr><th>Total Size</th><td>{self.format_size(total_size)}</td></tr>
                    <tr><th>Average Size</th><td>{self.format_size(avg_size)}</td></tr>
                    <tr><th>Most Occurring File Type</th><td>{most_occurring_file_type}</td></tr>
                </table>

                <h2>File Type Statistics</h2>
                {file_type_table}

                <h2>Folder File Counts</h2>
                {folder_file_counts_table}
            </div>
        </body>
        </html>
        """

        # Writing HTML content to a file
        html_file_name = f'{os.path.basename(self.root_folder)}_summary_stats_report.html'
        html_file_path = os.path.join(self.root_folder, html_file_name) if html_path is None else html_path
        try:
            with open(html_file_path, 'w', encoding='utf-8') as html_file:
                html_file.write(html_content)
            print(f"\nSummary HTML report generated: {html_file_path}")
        except Exception as e:
            print(f"Error generating Summary HTML report: {e}")




    def get_summary_stats(self):
        try:
            total_files = len(self.file_stats)
            if not total_files:
                print("No Files To Show Summary of. ^<>^")
                return 0, 0, 0, 0.0, None
            total_size = sum(stat['File Size (Bytes)'] for stat in self.file_stats)
            avg_size = total_size / total_files if total_files > 0 else 0

            file_types = [stat['File Type'] for stat in self.file_stats]
            most_occurring_file_type = max(set(file_types), key=file_types.count)

            # Count the unique folders
            self.unique_folders = set(stat['Source Folder'] for stat in self.file_stats)
            total_folders = len(self.unique_folders)
            return total_files, total_folders, total_size, avg_size, most_occurring_file_type
        except Exception as e:
            print(f"An Exception Occurred {e} in get_summary_stats")


    def print_summary_stats(self):
        """
        Print a summary of file statistics, including total files, total average size, most occurring file type, and total size.
        """
        try:
            if not len(self.file_stats):
                print("Nothing To Create Html of")
                return -1
            total_files, total_folders, total_size, avg_size, most_occurred = self.get_summary_stats()
            summary_stats = f"""
Summary Stats:
Total Files: {total_files}
Total Folders (With Files): {total_folders}
Total Size: {self.format_size(total_size)}
Average Size: {self.format_size(avg_size)}
Most Occurring File Type: {most_occurred}
"""
            print(summary_stats)
        except Exception as e:
            print(f"Error printing summary stats: {e}")
