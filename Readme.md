# Filer

Filer is a Python package designed to simplify the process of gathering statistics on media files within a specified root folder. It provides a range of features for collecting file statistics, moving files to a destination folder, generating reports, and more.

## Features

### 1. FileStatsCollector Class

The `FileStatsCollector` class is the core component of Filer, responsible for gathering statistics on media files within a specified root folder. It offers the following functionality:

- **File Statistics Collection:** Scan a root folder for files and collect detailed statistics such as file size, type, creation date, and modification date.
- **File Filtering:** Filter files based on specified criteria, such as media file extensions.
- **File Size Distribution:** Visualize the distribution of file sizes across various ranges.
- **Summary Statistics:** Generate summary statistics including total files, total size, average size, most common file type, etc.
- **Report Generation:** Create CSV and HTML reports containing detailed statistics of media files for easy analysis.
- **Sorting and Filtering:** Sort and filter file statistics based on various criteria.
- **Customizable:** Easily adaptable and extendable for specific use cases.

### 2. MediaGatherer Class

The `MediaGatherer` class extends the functionality of `FileStatsCollector` by providing utilities for moving media files to a destination folder. It includes the following features:

- **Media File Moving:** Move media files under a root directory to a given destination folder, with options to skip specified folders and handle duplicate filenames.
- **Report Generation:** Generate CSV and HTML reports containing detailed statistics of moved media files for analysis.
- **Customizable:** Customize folder skipping and file moving behavior as per requirements.

## Installation

You can install Filer using the method stated in [documentation](Filer\documentation.md) of the project.

## Usage

Here's a simple example demonstrating how to use Filer:

```python
from Filer.Filing import gatherer as gf

# Initialize MediaGatherer with the root directory
root_directory = input("Enter the root directory: ")
media_gatherer = gf.MediaGatherer(root_directory)

# Gather media files and generate statistics
media_gatherer.gather_media()
media_gatherer.generate_stats_csv()
media_gatherer.show_stats()
```

## Contributing

Contributions are welcome! If you encounter any issues or have suggestions for improvement, please don't hesitate to open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.