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
PYTHONPATH=Filer python -m unittest Filer.tests.tests_filestatser_extra Filer.tests.tests_gatherer Filer.tests.tests_filestatser
```

(On PowerShell, set `$env:PYTHONPATH = "Filer"` first.) `tests_filestatser.py` uses relative paths, so it must be run from the repository root.

**Documentation:**

Detailed documentation for the Filing package can be found in the README.md file of the project's GitHub repository. It includes information about the package's classes, methods, usage examples, and installation instructions.

**Contributing:**

Contributions to the Filing package are welcome! If you encounter any issues, have suggestions for improvements, or would like to contribute code, please feel free to open an issue or submit a pull request on the project's GitHub page.

**License:**

This project is licensed under the MIT License. See the LICENSE file for more details.