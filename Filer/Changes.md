# Changes
All the Noteable changes will be stated here.

## Version 1.5.0 - 2026-11-09

### Added
- **Hash cache** for `DuplicateFinder` (`Filing.HashCache`): hashes are remembered in `<root>/.filer_journal/hash_cache.json` (or `cache_dir`), so a repeat run does not read unchanged files again. A cached hash is used only while the file's size and modification time (nanoseconds) are exactly as before; files modified within the last two seconds are never cached; deleted files are pruned. A missing, corrupt or outdated cache is ignored. Turn it off with `use_cache=False`, wipe it with `clear_cache()`.
- `link_duplicates(verify=True)` compares each pair of files byte for byte before linking, so a file edited without its size or modification time changing can never be replaced by mistake.

### Changed
- `list_journals()` / `undo_last_move()` now only consider real journal files, so other JSON files in `.filer_journal` (such as the hash cache) are never mistaken for a journal.

## Version 1.4.0 - 2026-11-09

### Added
- **Dry run:** `gather_media(dry_run=True)`, `gather_files(dry_run=True)`, `preview_moves()` and `move_files_to_destination(..., dry_run=True)` return the planned moves `{source: destination}` and touch nothing. Name conflicts, also between planned files, are already resolved in the plan.
- **Undo:** every move is recorded in a JSON journal (`<root>/.filer_journal/`, or `journal_dir`). `undo_last_move()` moves the files back without overwriting anything, removes folders the run created if they are empty, and can be repeated for entries that were skipped. Turn journaling off with `journal=False`. Also available as `Filing.undo_moves()`.
- **Duplicate finder** (`Filing.DuplicateFinder`): stage-wise matching (size, then hash of first and last 64 KB, then whole-file hash only when needed) with threaded hashing. Report (`print_summary`, `generate_duplicates_csv`), `move_duplicates()` (undoable) or `link_duplicates()` (hard links). Choose the copy to keep with `keep='oldest'|'newest'|'first'|'shortest_path'`. Nothing is ever deleted.
- **Organise by rule:** `organize_by` on `MediaGatherer` and `Gatherer` sorts files into subfolders by `'type'`, `'date'`, `'size'`, `'extension'`, a pattern such as `'{type}/{year}/{month}'`, or a function (`Filing.Organizer`).
- **Filters** on `FileStatsCollector`, `MediaGatherer`, `Gatherer` and `DuplicateFinder`: `min_size`, `max_size`, `modified_after`, `modified_before`, `name_glob`, `name_regex` (`Filing.FileFilter`). Applied during the scan; name filters need no stat call.
- New modules: `filters.py`, `journal.py`, `mover.py`, `organizer.py`, `duplicates.py`.
- `MediaGatherer.failed_moves` lists files that could not be moved.

### Changed
- Python 3.9 or newer is now required (was 3.6).
- Moving now continues after a file fails (the failure is reported and kept in `failed_moves`) instead of stopping the whole batch.
- `gather_media()` returns the moved files `{source: destination}` (it returned `None`).
- `MediaGatherer` no longer modifies the `skip_folders` list you pass in.
- Moving logic was moved into `mover.py`; `handle_file_conflict`, `rename_if_exists` and `remove_number_suffix` still exist and behave the same.
- "Total Files Moved" now counts the files actually moved.

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