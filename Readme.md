# Filer

Filer is a Python package for scanning folders, reporting on the files in them, and moving and organising those files safely. Moves can be previewed first and undone afterwards. It has no third-party dependencies and requires **Python 3.9 or newer**.

## Features

- **Fast scanning:** folders are scanned in parallel, with skipped folders never entered.
- **Statistics and reports:** file sizes, types, dates, size and folder distributions, as CSV or HTML. A difference report compares the current files with an earlier CSV and tags files as New, Modified, Same or Deleted.
- **Filters:** keep only files within a size range, modified date range, or matching name (glob or regex), applied while scanning.
- **Moving files:** gather media files (or files of any extension) from a folder tree into one destination, with automatic renaming on name clashes and batched moves to limit load.
- **Dry run:** preview exactly where every file would go before anything is touched.
- **Undo:** every move is recorded in a JSON journal, and the last run can be reversed without overwriting anything.
- **Organise by rule:** sort files into subfolders by type, date, size, extension, a pattern such as `{type}/{year}/{month}`, or your own function.
- **Duplicate finder:** find identical files quickly (by size, then partial hash, then full hash), then report them, move the extra copies aside (undoable) or replace them with hard links. Nothing is ever deleted. Hashes are remembered between runs, so repeat scans only read new or changed files.

## Classes

| Class | Purpose |
|---|---|
| `FileStatsCollector` | Scan a folder, collect statistics, write CSV and HTML reports, compare scans. |
| `MediaGatherer` | `FileStatsCollector` plus moving media files to a destination, with dry run, undo and organising. |
| `Gatherer` | Like `MediaGatherer`, but for any file extensions you choose. |
| `DuplicateFinder` | Find and handle duplicate files. |
| `FileFilter`, `Organizer` | The filter and organise rules behind the options above. |
| `MoveJournal`, `undo_moves` | The undo journal. |

## Installation

Install from the folder that contains `setup.py` (see [documentation](Filer/documentation.md) for details):

```
cd Filer
pip install --upgrade .
```

## Usage

### Gather files, with a preview and an undo

```python
from Filing import MediaGatherer

if __name__ == "__main__":
    gatherer = MediaGatherer(r"D:\Downloads")

    gatherer.gather_media(dry_run=True)   # shows what would move; nothing is touched
    gatherer.gather_media()               # moves the media files into D:\Downloads\media
    gatherer.undo_last_move()             # changes your mind: everything goes back
```

Keep scripts under an `if __name__ == "__main__":` guard. Large folders are scanned with multiple processes, which Windows requires.

### Organise into subfolders

```python
from Filing import Gatherer

Gatherer(root, all_files=True, organize_by="{type}/{year}/{month}").gather_files()
# -> media files end up in Extracted Files/Images/2026/11/, Documents/2026/11/ ...
```

### Statistics and reports

```python
from Filing import FileStatsCollector

stats = FileStatsCollector(root, all_files=True, skip_folders=[".git", "node_modules"],
                           min_size="1MB", modified_after="2026-01-01")
stats.print_summary_stats()
stats.generate_file_stats_csv()
stats.generate_summary_html()
```

### Find duplicates

```python
from Filing import DuplicateFinder

finder = DuplicateFinder(r"D:\Photos")
finder.find()
finder.print_summary()
finder.move_duplicates(dry_run=True)   # preview
finder.move_duplicates()               # extra copies go to D:\Photos\Duplicates (undoable)
```

More options (filters, organise patterns, conflict handling, skip rules, running the tests) are in the [documentation](Filer/documentation.md). Changes between versions are listed in [Changes.md](Filer/Changes.md).

## Contributing

Contributions are welcome! If you encounter any issues or have suggestions for improvement, please don't hesitate to open an issue or submit a pull request.

## License

This project is licensed under the MIT License.
