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

**Documentation:**

Detailed documentation for the Filing package can be found in the README.md file of the project's GitHub repository. It includes information about the package's classes, methods, usage examples, and installation instructions.

**Contributing:**

Contributions to the Filing package are welcome! If you encounter any issues, have suggestions for improvements, or would like to contribute code, please feel free to open an issue or submit a pull request on the project's GitHub page.

**License:**

This project is licensed under the MIT License. See the LICENSE file for more details.