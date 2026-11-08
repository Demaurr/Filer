# Changes
All the Noteable changes will be stated here.

## Version 1.3.0 - 2026-11-09

### Changed
- **Faster file gathering** in `FileStatsCollector` (about 2.5x on a ~73k file tree):
  - The process pool now uses a `chunksize`, and is skipped for small trees (fewer than 32 folders) where starting workers costs more than scanning.
  - Skipped folders are pruned while walking instead of being filtered afterwards, so they are never scanned.
  - Extension matching uses a single `endswith(tuple)` call, and `stat` is only read for files that are actually kept.
  - Dates are formatted with `isoformat` (same `YYYY-MM-DD HH:MM:SS` output as before).
- **`skip_folders` now matches exactly** instead of by substring:
  - A plain name (e.g. `.git`, `node_modules`) skips every folder with exactly that name at any depth. `.git` no longer skips `.github`.
  - A path (contains a path separator or drive) skips that folder and everything under it. `MediaGatherer` uses this for its destination folder.
  - Matching is case-insensitive on Windows. Empty entries are ignored (previously an empty entry skipped everything). The root folder is never skipped by its own name.
- `walk_with_scandir(root, skip_folders=None)` takes an optional `skip_folders` argument.

### Added
- `get_creation_timestamp` and `format_timestamp` helpers in `FileStatsCollector`.
- `_PARALLEL_MIN_FOLDERS` class attribute (default 32) to tune when the process pool is used.
- Many more tests: `tests/tests_filestatser_extra.py` and `tests/tests_gatherer.py`.

### Fixed
- Creation date is now consistent: `process_files` and `get_file_stats` both use `st_birthtime` when available and fall back to `st_ctime`. Previously `process_files` failed silently on Windows with Python older than 3.12.
- `generate_file_stats_csv(allow_empty=True)` no longer crashes when there is no data; it now writes a header-only CSV (returns `0`).
- The default CSV name no longer breaks when the root folder path ends with a path separator.
- Extensions given in uppercase (e.g. `.MP3`) now match, in both `process_files` and `is_media_file`.
- `format_size` now has a `PB` unit; 1 PB used to be shown as `1.00 TB`.

### Removed
- Removed the stray `Filer/testing.py` scratch file.

## Version 1.2.1 - 2025-01-21

### Added
- Added generating difference csv in `FileStatsCollector` which compares the current files with an existing filestats csv and add tag for 'New', 'Modified', 'Same', 'Deleted' respectively.
- Added batchwise moving of files in `MediaGatherer` which reduces load on the pc by added time delay between each batch moves.
- Added helper methods like **handle_conflicts**, **get_files_to_move** in `MediaGatherer` for better scalability and readability and remove redundancy.

### Removed
- Removed Any Un-necessary Comments and Added Detailed docstrings for understanding.