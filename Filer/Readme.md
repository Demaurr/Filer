# Filer

Filer is a Python package designed to streamline the process of gathering statistics on media files within a specified root folder. It also offers utilities for moving these files to a destination folder, generating reports, and more.

## Features

- **File Statistics Collection:** Scan a root folder for files and collect detailed statistics such as file size, type, creation date, and modification date.
- **Media File Moving:** Move media files under a root dir to a given destination folder, with options to skip specified folders and handle duplicate filenames.
- **File Size Distribution:** Visualize the distribution of file sizes across various ranges.
- **Summary Statistics:** Generate summary statistics including total files, total size, average size, most common file type, etc.
- **Report Generation:** Create CSV and HTML reports containing detailed statistics of media files for easy analysis.
- **Sorting and Filtering:** Sort and filter file statistics based on various criteria.
- **Customizable:** Easily adaptable and extendable for specific use cases.

## Installation

You can install Filer using pip:

```bash
pip install Filer
```

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

<!-- For comprehensive usage instructions and examples, please refer to the [documentation](https://link-to-your-documentation). -->

<!-- ## Documentation

The complete documentation for Filer can be found [here](https://link-to-your-documentation). -->

## Contributing

Contributions are welcome! If you encounter any issues or have suggestions for improvement, please don't hesitate to open an issue or submit a pull request.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.