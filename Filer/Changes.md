# Changes
All the Noteable changes will be stated here.

## Version 1.7.0 - 2026-11-11

### Added
- **Packaging and CI:** All package information now lives in `pyproject.toml` (Python 3.9 to 3.13, the `filer` command, long description from `documentation.md`); `setup.py` is only a small shim that calls `setup()`, so it can no longer disagree with it. The version is still read from `Filing/__init__.py` (single source). A GitHub Actions workflow (`.github/workflows/ci.yml`) runs the tests on Windows and Linux for Python 3.9, 3.10, 3.11, 3.12 and 3.13, builds the package, installs the wheel and runs `filer --version`. `dist/` is no longer committed (build with `python -m build`).
- **Ignore rules:** `.gitignore`-style patterns keep files and folders out of a scan. Put them in `<folder>/.filerignore`, pass `exclude=[...]` / `ignore_file=...` to `FileStatsCollector`, `MediaGatherer`, `DuplicateFinder` and the new finders, or use `--exclude PATTERN`, `--ignore-file FILE` and `--no-ignore-file` on the command line. Ignored folders are never entered, so they also make scans faster. The rules are available as `Filing.IgnoreRules`.
- **`filer empty`** / `EmptyFolderFinder`: lists empty folders (a folder that holds only empty folders counts). `--remove` / `remove()` removes them deepest first with `rmdir` (it can never delete a file), records them in a journal, and `filer undo` / `undo_last_removal()` creates them again.
- **`filer junk`** / `JunkFinder`: reports junk files (`Thumbs.db`, `desktop.ini`, `.DS_Store`, `._*`, `~$*`, `*.tmp`, ...) and zero-byte files; expected empty files such as `__init__.py` and `.gitkeep` are not reported. `--move` / `move()` puts them in a `Junk` folder keeping their sub-folders, undoably. Patterns are configurable (`--pattern`, `--no-default-patterns`, `--kinds`).
- **Public comparison API:** `Filing.compare_snapshots(old, new, only_diff=, compare=, detect_moves=)` compares two lists of file records without touching the disk and returns `Change` objects (`Same`, `Modified`, `New`, `Deleted`, `Moved`). `FileStatsCollector.compare(other)` and `compare_with_csv(path)` use it; the difference CSV is built on it.
- **Cache hook:** `DuplicateFinder(cache=...)` accepts any object with the `HashStore` methods (`lookup`, `store`, `prune`, `save`, `clear`). New read-only `finder.cache`, `finder.cache_stats` and `finder.files`. A store that raises errors cannot break a search, and `clear_cache()` never clears a store you supplied unless you pass `force=True`.
- **Helpers exported at the top level:** `parse_size`, `parse_time`, `format_size`; new `FileFilter.matches(record)` applies filters to records without scanning.
- **Path safety:** `Filing.ensure_inside`, `is_inside`, `PathGuard` and `UnsafePathError` check that a path stays inside allowed folders (`..` and links cannot lead out; Windows device names and alternate data streams are refused).
- `bench/make_tree.py` and `bench/run.py`: reproducible speed measurements in temporary folders. The 1.6.0 baseline for a 10,000-file tree is kept in `bench/results_1.6.0.json`; run `python bench/run.py --json FILE` on an idle machine to compare (timings on a busy machine vary several-fold).
- `filer undo --allow-folder DIR` (see Changed).

### Changed
- **Undo only touches the folders it is allowed to.** A journal is an ordinary file, so a journal that was edited, or came with a downloaded folder, could have asked undo to move files from or to anywhere. `undo_moves()` now checks every path against `allowed_roots` (default: the folder you give, `root_folder`). If you moved files to a destination outside the scanned folder, undo from the same object still works; from a new session or the command line, allow the destination with `allowed_roots=[...]` / `filer undo --allow-folder DIR` (`filer move` prints the exact command). Entries outside the allowed folders are skipped and reported, and stay in the journal.
- Moves made by `MediaGatherer`, `DuplicateFinder` and `JunkFinder` refuse any source or destination outside the scanned and destination folders.
- **Faster scanning:** the folder tree is now listed once, and each folder is read while it is listed, in the main process. The scan no longer starts worker processes (the main cost on Windows). On a 10,000-file test tree, measured on an idle machine, a scan went from about 0.73 s to 0.15 s, a duplicate search from about 2.5 s to 1.3 s (cold) and from 1.5 s to 0.64 s (warm), and a move preview from 1.3 s to 0.65 s. Measured back to back on a busy machine the gains were smaller (scan about 0.70 s to 0.50 s, move preview 4.4 s to about 1 s), so expect roughly 1.4x to 5x depending on the load. A subclass that overrides `process_files` keeps the older two-pass path. The stray folder-count number printed after a scan is gone.
- Hashing the first/last 64 KB of a file now opens the file unbuffered, which is slightly faster.
- `Organizer.relative_path` makes a file name from outside data (a snapshot or CSV) safe: path separators, drive prefixes and `..` in a name can no longer lead out of the destination. Real file names are unchanged.

### Fixed
- **Journal overwrite:** two moves started within the same clock tick got the same journal file name, so the second silently replaced the first and its undo information was lost. Journal names are now always unique.
- `link_duplicates()` could skip files that were written just before the search, because the system clock can lag a few milliseconds behind file timestamps (seen on Windows). A 0.1 s tolerance now covers it; files really changed after the search are still refused.
- A hostile `*` pattern such as `a*a*a*a*a*b` no longer makes name matching take minutes (the ignore rules use a linear-time form of `*`).

## Version 1.6.0 - 2026-11-09

### Added
- **Command-line interface:** `filer` command (also `python -m Filing`) with `stats`, `move`, `undo` and `dupes`. Supports `--dry-run`, `--organize-by`, filters, `--json` output, `--quiet` / `--no-progress`, and asks for confirmation before changing files (`--yes` to skip; without a terminal it refuses instead of guessing). Exit codes: 0 ok, 1 error or partly done, 2 bad usage, 130 interrupted.
- **Progress reporting:** pass `progress=function(stage, done, total)` to `FileStatsCollector`, `MediaGatherer`, `Gatherer` and `DuplicateFinder` to follow scanning, planning, moving, hashing and linking. Stage names are constants in `Filing.progress`; `ConsoleProgress` is a ready-made terminal display (stderr) and `ProgressReporter` throttles and shifts counts. A callback that raises cancels the operation (the undo journal is still saved).
- **`verbose=False`** on the same classes silences informational messages (errors and explicit reports such as `print_summary_stats` still print).
- `generate_file_stats_html(confirm=False)` skips the "large report" question, so scripts never block on `input()`.
- `Filing.__version__`; `setup.py` reads the version from it and registers the `filer` command.

### Fixed
- Scanning many folders in parallel failed silently (no files found) when the gatherer held something a worker process cannot receive, such as a function passed as `organize_by`. Workers now only receive plain data.
- `DuplicateFinder.clear_cache()` now also deletes the cache file when caching is switched off for the run.

### Changed
- Removed the deprecated license classifier (the `license="MIT"` field stays).

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