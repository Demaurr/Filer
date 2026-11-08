**Project Title: Filing**

**Description:**

Filing is a Python package designed to facilitate the gathering and analysis of file statistics within a specified root folder. It provides classes for collecting detailed statistics on media files, generating HTML and CSV reports, moving media files to a designated folder, and much more. The package aims to simplify the process of managing and analyzing file data, particularly for media files, in a user-friendly manner.

**Installation:**

As the package is not yet uploaded to PyPI, you can install it by following these steps:

1. Clone the Git repository from the project's GitHub page:

   ```
   git clone https://github.com/your_username/Filing.git
   ```

2. Navigate to the project directory:

   ```
   cd Filing
   ```

3. Install the package locally using pip:

   ```
   pip install .
   ```

**Usage:**

Once the package is installed, you can import its modules and classes into your Python scripts as needed. Here's an example of how to use the package to gather file statistics and generate reports:

```python
from Filing import filestatser as fstat
import os

if __name__ == "__main__":
    path = input("Enter a Root Path To Gather Info (or type 'quit'): ")
    
    if path.lower() == "quit":
        exit()

    while not os.path.exists(path):
        path = input("Enter a Correct Root Path: ")

    skip_folders = ['.git', '__pycache__', '.idea', 'The Witcher Series by Andrzej Sapkowski [1-8]', 'Google Python Testing',
                    'venv', 'Lib']  # Example folders to skip

    all_files_input = input("Gather All Files Info (yes/no): ").lower()

    while all_files_input not in ['yes', 'no']:
        print("Please enter 'yes' or 'no'.")
        all_files_input = input("Gather All Files Info (yes/no): ").lower()

    all_files = all_files_input == "yes"

    filename = input("Enter the filename to save your statistics files (or leave it blank): ").strip()
    folderpath = input("Enter the folder path to save your statistics files (or leave it blank): ").strip()

    if not filename or not folderpath:
        html_path = csv_path = None
    else:
        folderpath = os.path.abspath(folderpath)
        filename = os.path.splitext(filename)[0]
        html_path = os.path.join(folderpath, filename + ".html")
        csv_path = os.path.join(folderpath, filename + ".csv")

    stater = fstat.FileStatsCollector(root_folder=path, all_files=all_files, skip_folders=skip_folders)
    
    stater.print_summary_stats()
    
    if html_path:
        stater.generate_summary_html(html_path=html_path)
        stater.generate_file_stats_html(html_path=html_path)

    if csv_path:
        stater.generate_file_stats_csv(csv_path=csv_path)
```

This script prompts the user to enter the root path to gather file information, whether to gather statistics for all files, and the filename and folder path to save the statistics files. It then utilizes the Filing package to gather and analyze file statistics, generate HTML and CSV reports, and save them to the specified location.

**Requirements:** Python 3.9 or newer, no third-party packages.

**Previewing and undoing moves:**

```python
from Filing import MediaGatherer

gatherer = MediaGatherer(r"D:\Downloads")
plan = gatherer.gather_media(dry_run=True)   # {source: destination}, nothing is touched
gatherer.gather_media()                      # does the move and writes a journal
gatherer.undo_last_move()                    # moves everything back
```

Every real move is logged in `<root>/.filer_journal/*.json` (change the location with `journal_dir=`, turn it off with `journal=False`). Undo never overwrites a file: if the original location is occupied again, or the moved file is gone, that entry is skipped and reported, and stays in the journal so you can run `undo_last_move()` again after fixing it. Folders created by the run are removed if they are empty. `undo_last_move(dry_run=True)` shows what would be restored.

**Organising into subfolders:**

```python
MediaGatherer(root, all_files=True, organize_by="type")                        # Images/, Videos/, Documents/ ...
MediaGatherer(root, all_files=True, organize_by="date")                        # 2026/11/
MediaGatherer(root, all_files=True, organize_by="{type}/{year}/{month}")       # Images/2026/11/
MediaGatherer(root, all_files=True, organize_by=lambda stat: "Big" if stat["File Size (Bytes)"] > 10**8 else "Small")
```

Presets: `type`, `date`, `size`, `extension`. Pattern fields: `{type}` (Images, Videos, Audio, Documents, Archives, Code, Other), `{extname}` (`jpg`), `{ext}` (`.jpg`), `{year}` `{month}` `{day}` (modification date), `{size}` (Tiny, Small, Medium, Large, Huge), `{source}` (name of the original folder), `{name}`, `{filename}`. A pattern describes the folder and the file keeps its name; if the last part contains `{name}`, `{filename}` or `{ext}` it describes the whole path (`{type}/{year}/{name}` also works and keeps the extension). Combine it with `dry_run=True` to see the result first.

**Filtering while scanning:**

```python
FileStatsCollector(root, all_files=True, min_size="10MB", modified_after="2026-01-01", name_glob=["*.mp4", "*.mkv"])
```

`min_size` / `max_size` (inclusive; bytes or `'10KB'`, `'1.5 MB'`), `modified_after` (inclusive) / `modified_before` (exclusive) (datetime, date, timestamp or ISO string), `name_glob` (case-insensitive, one or a list), `name_regex` (one or a list). All given filters must match. Filtered-out files are never stored, so they do not appear in reports or get moved. The same options work on `MediaGatherer`, `Gatherer` and `DuplicateFinder`.

**Finding duplicates:**

```python
from Filing import DuplicateFinder

finder = DuplicateFinder(r"D:\Photos", skip_folders=[".git"])
finder.find()
finder.print_summary()
finder.generate_duplicates_csv()
finder.move_duplicates(dry_run=True)   # preview
finder.move_duplicates()               # extra copies go to <root>/Duplicates (undo with finder.undo_last_move())
finder.link_duplicates()               # or: replace extra copies with hard links (cannot be undone)
```

Files are compared in stages (same size, then a hash of the first and last 64 KB, then a hash of the whole file only if still needed), so most files are ruled out without being read in full. `keep='oldest'` (default), `'newest'`, `'first'` or `'shortest_path'` chooses the copy that stays. Empty files are ignored. Nothing is ever deleted. `link_duplicates()` needs files on the same drive and skips copies that changed after the search.

**Skipping folders:**

`skip_folders` entries are matched exactly (case-insensitively on Windows):

- A plain name such as `'.git'` or `'node_modules'` skips every folder with exactly that name, at any depth (`'.github'` is not skipped by `'.git'`).
- A path such as `r'C:\Photos\Backup'` skips that folder and everything below it.
- Empty entries are ignored, and the root folder is never skipped by its own name.

Skipped folders are never scanned, so skipping large folders such as `node_modules` or `.git` makes gathering faster. Folders are scanned in parallel for large trees, so scripts should be run under an `if __name__ == "__main__":` guard (as in the example above), which Windows requires for multiprocessing.

**Empty results:**

`generate_file_stats_csv()` returns `-1` and creates nothing when no files were found. Pass `allow_empty=True` to get a CSV that contains only the header row instead.

**Tests:**

From the repository root:

```
PYTHONPATH=Filer python -m unittest discover -s Filer/tests -p "tests_*.py"
```

(On PowerShell, set `$env:PYTHONPATH = "Filer"` first.) `tests_filestatser.py` uses relative paths, so it must be run from the repository root.

**Documentation:**

Detailed documentation for the Filing package can be found in the README.md file of the project's GitHub repository. It includes information about the package's classes, methods, usage examples, and installation instructions.

**Contributing:**

Contributions to the Filing package are welcome! If you encounter any issues, have suggestions for improvements, or would like to contribute code, please feel free to open an issue or submit a pull request on the project's GitHub page.

**License:**

This project is licensed under the MIT License. See the LICENSE file for more details.