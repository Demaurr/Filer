# Filer documentation

Filer scans folders, reports on the files in them, and moves, organises and de-duplicates files **safely**: every move can be previewed first and undone afterwards, and nothing is ever deleted. It works as a Python library (`import Filing`) and as a command (`filer`). It needs Python 3.9 or newer and has no third-party dependencies.

This document describes version 1.7.0.

## Contents

1. [Install](#1-install)
2. [Quick start](#2-quick-start)
3. [Key ideas](#3-key-ideas)
4. [Command-line reference](#4-command-line-reference)
5. [Python reference](#5-python-reference)
6. [Recipes](#6-recipes)
7. [Troubleshooting and FAQ](#7-troubleshooting-and-faq)
8. [Development](#8-development)

---

## 1. Install

Filer is not on PyPI yet. Install it from the project folder that contains `pyproject.toml`:

```
git clone https://github.com/demaurr/filer.git
cd filer/Filer
pip install --upgrade .
```

`--upgrade` replaces an older installed version, so there is no need to uninstall first. Useful variants:

| Goal | Command |
|---|---|
| Install, replacing any older version | `pip install --upgrade .` |
| Same version number, changed code | `pip install --upgrade --force-reinstall --no-deps .` |
| Work on the code (changes take effect immediately) | `pip install -e .` |
| Check what is installed | `pip show Filer` |
| Remove | `pip uninstall Filer` |

The package is installed under the name **Filer** but imported as **`Filing`**. Installing also adds the **`filer`** command. If an old copy keeps being used, delete the `build/` folder next to `pyproject.toml` and install again.

---

## 2. Quick start

### From the command line

```
filer stats D:\Photos --all-files --csv report.csv          # statistics and a CSV report
filer move D:\Downloads --organize-by type --dry-run        # preview: nothing is changed
filer move D:\Downloads --organize-by type                  # asks before moving
filer undo D:\Downloads                                     # changed your mind
filer dupes D:\Photos                                       # find duplicate files
filer dupes D:\Photos --move                                # move the extra copies to D:\Photos\Duplicates
```

### From Python

```python
from Filing import MediaGatherer, FileStatsCollector, DuplicateFinder

if __name__ == "__main__":          # required on Windows (see Troubleshooting)
    # 1. Statistics
    stats = FileStatsCollector(r"D:\Photos", all_files=True)
    stats.print_summary_stats()
    stats.generate_file_stats_csv(r"D:\report.csv")

    # 2. Move files: preview, do it, undo it
    gatherer = MediaGatherer(r"D:\Downloads", organize_by="type")
    plan = gatherer.gather_media(dry_run=True)    # {source: destination}, nothing touched
    gatherer.gather_media()
    gatherer.undo_last_move()

    # 3. Duplicates
    finder = DuplicateFinder(r"D:\Photos")
    finder.find()
    finder.print_summary()
    finder.move_duplicates(dry_run=True)
```

---

## 3. Key ideas

### Which files are included

- **Media files by default.** `FileStatsCollector` and `MediaGatherer` only look at common media files unless you say otherwise: `.mp3 .mp4 .avi .mkv .jpg .jpeg .png .gif`.
- **`all_files=True`** includes every file. **`media_extensions=[...]`** (CLI: `--ext`) chooses your own list. Matching ignores case.
- If a scan finds nothing, the usual reason is that the files are not media files: add `all_files=True` / `--all-files`.

### Skipping folders

`skip_folders` (CLI: `--skip`) entries are matched **exactly** and ignore case on Windows:

- A plain **name** such as `.git` or `node_modules` skips every folder with exactly that name, at any depth. (`.github` is not skipped by `.git`.)
- A **path** (anything with a path separator or drive, such as `D:\Photos\Backup`) skips that folder and everything below it.
- Empty entries are ignored. The folder you scan is never skipped by its own name.

Skipped folders are never entered, so skipping big folders makes scans faster. Destination and journal folders are skipped automatically.

### Filters

Filters apply **while scanning**, so files that do not match are never stored, reported or moved.

| Option (Python / CLI) | Meaning |
|---|---|
| `min_size`, `max_size` / `--min-size`, `--max-size` | Size limits, inclusive. Bytes or text such as `500`, `10KB`, `1.5 MB` (units are powers of 1024). |
| `modified_after` / `--modified-after` | Modified at or after this time (inclusive). |
| `modified_before` / `--modified-before` | Modified before this time (exclusive). |
| `name_glob` / `--glob` | File name matches a pattern such as `*.jpg` (case-insensitive). One pattern or a list. |
| `name_regex` / `--regex` | File name matches a regular expression (searched anywhere in the name; case-sensitive unless you use `(?i)`). One or a list. |

Times can be a `datetime`, a `date`, a timestamp, or text such as `2026-11-09` or `2026-11-09 14:30:00` (local time). When several filters are given, a file must pass all of them. Several globs (or several regexes) match if any of them does.

### Previewing, journals and undo

- A **dry run** (`dry_run=True`, CLI `--dry-run`) works out exactly where every file would go, including renames for name clashes, and changes nothing.
- Every real move is written to a **journal**: a JSON file in `<scanned folder>/.filer_journal/` (change with `journal_dir`, switch off with `journal=False` / `--no-journal`). Each run has its own journal.
- **Undo** moves the files of the newest journal that has not been undone back to where they came from. It **never overwrites** anything: if the original spot is occupied again, or the moved file has disappeared, that file is skipped and reported, and stays in the journal so you can fix the problem and run undo again. Folders the run created are removed again if they are empty.
- Name clashes in the destination are resolved by renaming the incoming file: `song.mp3`, `song(1).mp3`, `song(2).mp3`, ... Existing files are never replaced.
- A move continues after a single file fails; failures are listed afterwards and the journal still records everything that did move.

### Ignore rules (`.filerignore`, `--exclude`)

Besides `skip_folders`, you can keep files and folders out of a scan with `.gitignore`-style rules. Put them in a file called `.filerignore` in the folder you scan, pass them in code (`exclude=["*.tmp", "build/"]`, `ignore_file=path_or_False`), or on the command line (`--exclude`, `--ignore-file`, `--no-ignore-file`). Folders that match are never entered, so big ignored trees cost nothing.

```
# .filerignore
*.tmp              # any file called *.tmp, at any depth
build/             # folders only
/cache             # only the top-level 'cache'
docs/**/draft*     # ** matches any number of folders
!keep.tmp          # re-include (the last matching rule wins)
```

| Rule | Meaning |
|---|---|
| `name`, `*.log`, `data?.csv`, `[abc]` | Matches a file or folder name at any depth. `*` never crosses a `/`. |
| `dir/` | Folders only. |
| `/top`, or a `/` inside the rule (`docs/*.md`) | Anchored to the scanned folder. |
| `**/x`, `a/**/b`, `logs/**` | Any number of folders. |
| `!rule` | Re-includes what an earlier rule ignored (not possible inside an ignored folder, as in git). |
| `# text`, blank lines | Ignored. `\#` and `\!` give a literal `#` or `!`. |

Matching ignores case on Windows. Only the `.filerignore` in the scanned folder is read (not ones in sub-folders). The same rules work for `stats`, `move`, `dupes`, `empty` and `junk`. In code: `Filing.IgnoreRules(["*.log"]).is_ignored("a/b.log")`.

### What Filer never does

- It **never deletes** files.
- It **never overwrites** files (moves rename on clashes, undo skips occupied spots).
- The only operation that cannot be undone is `link_duplicates` / `filer dupes --link`, which asks for confirmation and warns about it.

### The file record

Scans produce a list of dictionaries (`collector.file_stats`), one per file:

```python
{
  "File Name": "photo.jpg",
  "File Type": ".jpg",                       # lower case extension
  "File Size (Bytes)": 2048,
  "File Size (Human Readable)": "2.00 KB",
  "Creation Date": "2026-11-09 14:30:00",
  "Modification Date": "2026-11-09 14:30:00",
  "Source Folder": "D:\\Photos\\Holiday",
}
```

### Hash cache (duplicates)

`DuplicateFinder` remembers file hashes in `<folder>/.filer_journal/hash_cache.json`, so running it again only reads files that are new or changed. A remembered hash is used only while the file's size and modification time are exactly the same; files modified in the last two seconds are not remembered; deleted files are forgotten. A missing, damaged or outdated cache is ignored and rebuilt. Disable it with `use_cache=False` / `--no-cache`, reset it with `clear_cache()` / `--clear-cache`.

A file edited without changing its size **or** modification time would fool any cache of this kind, which is why linking duplicates re-checks each pair byte for byte first.

---

## 4. Command-line reference

```
filer [--version] <command> FOLDER [options]
python -m Filing <command> FOLDER [options]
```

Put the folder directly after the command. Options that can be given several times (`--skip`, `--ext`, `--glob`, `--regex`) are repeated: `--skip .git --skip node_modules`. Use quotes around anything containing spaces or braces.

### Options shared by every command

| Option | Meaning |
|---|---|
| `-q`, `--quiet` | Print only results and errors (no progress). |
| `--no-progress` | Hide the progress display only. |
| `--json` | Print the result as JSON on stdout. Messages and progress go to stderr, so stdout stays valid JSON. |

Progress is shown on stderr (one updating line in a terminal, one line per finished stage when redirected).

### Options that choose files (`stats`, `move`; `dupes` has all but `--all-files` and `--ext`)

| Option | Meaning |
|---|---|
| `--all-files` | Every file, not just media files. |
| `--ext EXT` | Only these extensions: `--ext .jpg --ext .png` or `--ext .jpg,.png` (the dot is optional). |
| `--skip NAME_OR_PATH` | Skip folders (see Skipping folders). |
| `--min-size SIZE`, `--max-size SIZE` | Size limits, e.g. `10MB`. |
| `--modified-after DATE`, `--modified-before DATE` | Time limits, e.g. `2026-01-31`. |
| `--glob PATTERN`, `--regex PATTERN` | File name patterns. |
| `--exclude PATTERN` | Leave out files/folders matching a `.gitignore`-style rule (see Ignore rules). Repeatable. |
| `--ignore-file FILE`, `--no-ignore-file` | Read rules from FILE instead of `FOLDER/.filerignore`, or ignore that file. |

### `filer stats FOLDER` — statistics and reports

Prints a summary (file count, folders, total and average size, most common type).

| Option | Meaning |
|---|---|
| `--types`, `--sizes`, `--folders` | Also print the file type distribution, the size distribution, or the size of every folder. |
| `--csv [PATH]` | Write a CSV report of every file (default location: inside FOLDER). |
| `--html [PATH]` | Write an HTML report of every file. |
| `--summary-html [PATH]` | Write an HTML summary report. |
| `--diff PREVIOUS.csv` | Compare with an earlier CSV report and write a **new** difference CSV in FOLDER, tagging every file `New`, `Modified`, `Same` or `Deleted`. The old report is not overwritten. |
| `--only-diff` | With `--diff`: leave out unchanged (`Same`) files. |

```
filer stats D:\Photos --all-files --types --csv D:\snapshot.csv
filer stats D:\Photos --all-files --diff D:\snapshot.csv --only-diff      # what changed since then
```

### `filer move FOLDER` — gather or organise files

Moves files from FOLDER and its subfolders into one destination (default `FOLDER\media`).

| Option | Meaning |
|---|---|
| `-d`, `--destination DIR` | Destination folder. |
| `--organize-by RULE` | Sort into subfolders: `type`, `date`, `size`, `extension`, or a pattern such as `"{type}/{year}/{month}"` (see Organising by rule). |
| `--date-field modified\|created` | Which date `{year}`, `{month}`, `{day}` and `date` use (default `modified`). |
| `--dry-run` | Show what would move; change nothing. |
| `-y`, `--yes` | Do not ask for confirmation. |
| `--batch-size N`, `--delay SECONDS` | When moving many files: files per batch (default 15) and the pause between batches (default 0.1). |
| `--no-journal` | Do not record the move (it then cannot be undone). |
| `--journal-dir DIR` | Keep the journal here instead of `FOLDER\.filer_journal`. |

Before moving, the command lists what will happen and asks for confirmation. Without a terminal (scripts, pipes) it refuses unless `--yes` is given.

```
filer move D:\Downloads --organize-by type --dry-run
filer move D:\Downloads --organize-by "{type}/{year}/{month}" --yes
filer move D:\Downloads --all-files --skip Archive -d D:\Sorted --min-size 1MB
```

### `filer undo FOLDER` — undo the last move

Moves the files of the newest journal that has not been undone back where they came from.

| Option | Meaning |
|---|---|
| `--list` | List the journals (date, kind, number of files, whether undone) instead of undoing. |
| `--dry-run` | Show what would be restored; change nothing. |
| `--journal FILE` | Undo this journal instead of the newest one. |
| `--journal-dir DIR` | Where the journals are kept (if you used `--journal-dir` when moving). |

Files that cannot be restored (original spot occupied, or the file is gone) are listed and the command exits with code 1; run it again after fixing the problem.

**Safety of undo.** A journal is just a file, so `filer undo` only touches files inside FOLDER. If you moved files to a destination outside FOLDER (`--destination`, `--move DIR`), allow that folder when undoing: `filer undo FOLDER --allow-folder DIR` (the move command prints this for you). In code: `undo_moves(..., allowed_roots=[root, destination])`. Files outside the allowed folders are skipped and reported, and stay in the journal.

### `filer dupes FOLDER` — find duplicate files

Finds files with identical content and prints a summary: number of groups, extra copies and reclaimable space.

| Option | Meaning |
|---|---|
| `--min-size SIZE` | Ignore smaller files (default 1 byte; empty files are never reported). |
| `--keep oldest\|newest\|first\|shortest_path` | Which copy of each group stays (default `oldest`; `first` is first by path). |
| `--top N` | How many groups to list (default 10). |
| `--csv [PATH]` | Write every duplicate group to a CSV; the `Action` column shows `Keep` or `Duplicate`. |
| `--move [DIR]` | Move the extra copies to DIR (default `FOLDER\Duplicates`). Can be undone with `filer undo`. |
| `--link` | Replace the extra copies with hard links (same drive, NTFS or similar). **Cannot be undone.** Cannot be combined with `--move`. |
| `--no-verify` | With `--link`: skip the byte-for-byte check before linking. |
| `--dry-run` | With `--move` or `--link`: show what would happen; change nothing. |
| `-y`, `--yes` | Do not ask for confirmation. |
| `--no-cache`, `--clear-cache` | Do not remember hashes / forget remembered hashes first. |

### `filer empty FOLDER` — find and remove empty folders

Lists folders that hold no files. A folder whose only content is empty folders is empty too. Nothing changes unless you add `--remove`, and no file can ever be deleted: folders are removed with `rmdir`, which refuses anything that is not empty. A folder is never listed if it holds a file, a link, or a folder you skipped or ignored.

| Option | Meaning |
|---|---|
| `--remove` | Remove the empty folders (deepest first). Asks first; recorded so `filer undo` creates them again. |
| `--dry-run` | With `--remove`: show what would be removed. |
| `-y`, `--yes` | Do not ask. |
| `--skip`, `--exclude`, `--ignore-file`, `--no-ignore-file` | Leave folders out (they count as content). |
| `--no-journal`, `--journal-dir DIR` | Do not record the removal, or keep the journal elsewhere. |

```
filer empty D:\Photos                       # list
filer empty D:\Photos --remove --dry-run    # preview
filer empty D:\Photos --remove              # asks, then removes
filer undo D:\Photos                        # puts the folders back
```

### `filer junk FOLDER` — find junk and zero-byte files

Reports junk files by name (`Thumbs.db`, `desktop.ini`, `.DS_Store`, `._*`, `~$*`, `.~lock.*#`, `*.tmp`, `*.temp`, `*~`; matched without regard to case) and zero-byte files. Zero-byte files that are normal, such as `__init__.py`, `.gitkeep` and `.gitignore`, are not reported. Nothing changes unless you add `--move`; nothing is ever deleted.

| Option | Meaning |
|---|---|
| `--kinds junk zero_byte` | What to look for (default: both). |
| `--pattern GLOB` | Also treat this name pattern as junk, e.g. `--pattern "*.bak"`. Repeatable. |
| `--no-default-patterns` | Use only your `--pattern`s. |
| `--top N` | How many files to list (default 20). |
| `--csv [PATH]` | Write a CSV report of everything found. |
| `--move [DIR]` | Move the files to DIR (default `FOLDER/Junk`), keeping their sub-folders. Asks first; undoable. |
| `--dry-run`, `-y/--yes`, `--no-journal`, `--journal-dir` | As for `move`. |
| `--skip`, `--exclude`, `--ignore-file`, `--no-ignore-file` | Leave folders and files out. |

```
filer junk D:\Photos
filer junk D:\Photos --kinds junk --pattern "*.bak" --move --dry-run
filer junk D:\Photos --move --yes
filer undo D:\Photos
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (also: you answered "no" to a confirmation, or there was nothing to do). |
| 1 | An error (message printed as `filer: error: ...`), or a move/undo that was only partly done. |
| 2 | Wrong usage (unknown option, missing folder argument, ...). |
| 130 | Interrupted with Ctrl+C. Files already moved can be restored with `filer undo`. |

### JSON output

With `--json` the result is printed as JSON, for example `filer move D:\Downloads --dry-run --json`:

```json
{ "dry_run": true, "destination": "D:\\Downloads\\media", "count": 2,
  "moves": { "D:\\Downloads\\a.mp3": "D:\\Downloads\\media\\a.mp3", "...": "..." } }
```

`stats --json` gives the summary numbers and file type counts, `dupes --json` the groups, `undo --json` the restored and skipped files, and `undo --list --json` the journals.

---

## 5. Python reference

```python
from Filing import (FileStatsCollector, MediaGatherer, Gatherer, DuplicateFinder, DuplicateGroup,
                    FileFilter, Organizer, MoveJournal, undo_moves, HashCache,
                    ConsoleProgress, ProgressReporter, __version__)
```

All classes can scan many folders with several processes. **On Windows, run scripts under `if __name__ == "__main__":`.**

### Options every class that scans accepts

| Parameter | Meaning |
|---|---|
| `skip_folders` | List of folder names or paths to skip. |
| `min_size`, `max_size`, `modified_after`, `modified_before`, `name_glob`, `name_regex` | Filters (see Key ideas). `MediaGatherer`, `Gatherer` and `DuplicateFinder` pass them on, so they accept them too. |
| `progress` | Function `progress(stage, done, total)` (see Progress). |
| `verbose` | `True` (default) prints informational messages; `False` silences them. Errors and explicit reports such as `print_summary_stats()` always print. |

### `FileStatsCollector(root_folder, media_extensions=None, all_files=False, skip_folders=None, ...)`

Scans `root_folder` when created. Raises `FileNotFoundError` if the folder does not exist.

Attributes: `file_stats` (list of file records), `total_files`, `file_types`, `root_folder`.

| Method | What it does |
|---|---|
| `print_summary_stats()` | Prints total files, folders, total and average size, most common type. |
| `get_summary_stats()` | Returns `(total_files, total_folders, total_size, average_size, most_common_type)`. |
| `get_file_typedist()` / `print_file_types()` | File type counts (`Counter`) / print them. |
| `get_folders_info()` | `Counter` of files per folder. |
| `get_folder_size_distribution()` / `show_folder_size_distribution()` | Bytes per folder / print them. |
| `show_file_size_distribution()` | Prints how many files fall into size ranges (0-1KB, 1KB-100KB, ... 500MB+). |
| `generate_file_stats_csv(csv_path=None, sort_by="File Name", allow_empty=False)` | CSV of every file. With no data it returns `-1`, or with `allow_empty=True` writes a header-only CSV and returns `0`. |
| `generate_file_stats_html(html_path=None, confirm=True)` | HTML report of every file. For more than 1500 files it asks first unless `confirm=False`. |
| `generate_summary_html(html_path=None)` | HTML summary report. |
| `generate_difference_file_csv(csv_path, output_path=None, only_diff=False, sort_by="File Name")` | Compares the current files with the CSV at `csv_path` and tags each `New`, `Modified`, `Same` or `Deleted`. **Note:** with `output_path=None` the result **replaces** `csv_path`; with any other value (it is only a switch) a new file named `<folder>_difference_<time>.csv` is written inside the scanned folder. |
| `get_file_stats(file_path)` (static) | Record for a single file, as a one-item list. |
| `sort_by_key(file_stats, key, case_sense=True, reverse=False)` (static) | Sort records by a key. |
| `format_size(size)` (static) | `2048` -> `"2.00 KB"`. |

Default report locations are inside the scanned folder, named after it plus a timestamp.

### `MediaGatherer(root_folder, destination_folder=None, skip_folders=None, media_extensions=[...], all_files=False, organize_by=None, journal=True, journal_dir=None, ...)`

A `FileStatsCollector` that can also move the files it found. Default destination: `<root_folder>/media`.

| Method / attribute | What it does |
|---|---|
| `gather_media(destination_folder=None, move_all=False, batch_size=15, time_delay=0.1, dry_run=False)` | Moves the files. Returns `{source: destination}` of what was moved (with `dry_run=True`: of what would move, changing nothing). Moves are done in batches with a short pause when there are more than 100 files. |
| `preview_moves(files_to_move=None, destination_folder=None, move_all_files=False)` | The dry-run plan as `{source: destination}`. |
| `undo_last_move(journal=None, dry_run=False)` | Undoes the newest journal (or the given one). Returns `{"journal", "restored", "skipped", "dry_run"}`. |
| `moved_files` | `{source: destination}` of everything moved by this object. |
| `failed_moves` | List of `(source, error)` for files that could not be moved. |
| `last_journal_path` | Path of the journal written by the last `gather_media()`. |
| `get_files_to_move(move_all_files=False)` | File records that would be moved. |
| `show_moved_stats()` | Prints how many files and bytes were moved. |

`organize_by` chooses subfolders below the destination (see Organising by rule). `journal`, `journal_dir`: see Key ideas.

### `Gatherer(root_folder, destination_folder=None, extensions=None, skip_folders=None, all_files=False, ...)`

Like `MediaGatherer`, but you give the extensions to collect, and the default destination is `<root_folder>/Extracted Files`. Use `gather_files(destination_folder=None, all_files=False, batch_size=100, time_delay=0.5, dry_run=False)` instead of `gather_media`. Give real extensions (`[".pdf", ".docx"]`); an empty entry makes it ask for input.

```python
Gatherer(r"D:\Work", extensions=[".pdf", ".docx"]).gather_files()
```

### Organising by rule (`organize_by`)

Pass a preset, a pattern, an `Organizer`, or a function.

| Rule | Result |
|---|---|
| `"type"` | `Images/photo.jpg` (categories: Images, Videos, Audio, Documents, Archives, Code, Other) |
| `"date"` | `2026/11/photo.jpg` (from the modification date) |
| `"size"` | `Small/photo.jpg` (Tiny under 100 KB, Small under 10 MB, Medium under 100 MB, Large under 1 GB, Huge) |
| `"extension"` | `jpg/photo.jpg` (`noext` if there is none) |
| `"{type}/{year}/{month}"` | `Images/2026/11/photo.jpg` |
| `lambda stat: "Big" if stat["File Size (Bytes)"] > 10**8 else "Small"` | your own folder choice (receives the file record, returns a relative folder) |

Pattern fields: `{type}`, `{extname}` (`jpg`), `{ext}` (`.jpg`), `{year}`, `{month}`, `{day}`, `{size}`, `{source}` (name of the folder the file came from), `{name}` (without extension), `{filename}`. A pattern describes the **folder** and the file keeps its name. If the last part of the pattern contains `{name}`, `{filename}` or `{ext}`, it describes the whole path (`"{type}/{year}/{name}"` works and keeps the extension). Unknown fields raise `ValueError`. Folder names are made safe for the file system, so a file can never be placed outside the destination. `Organizer(rule, date_field="modified")` also accepts `date_field="created"`.

### `DuplicateFinder(root_folder, destination_folder=None, skip_folders=None, min_size=1, ...)`

Finds identical files in stages, ruling files out as early as possible: same size, then the same hash of the first and last 64 KB, then the same hash of the whole file (only for big files that are still candidates). Hashing runs in threads.

| Method | What it does |
|---|---|
| `find()` | Returns a list of `DuplicateGroup(size, digest, files)`, biggest waste first. Unreadable files are skipped and listed in `finder.errors`. |
| `print_summary(top=10)` | Prints totals and the biggest groups. |
| `duplicate_count`, `wasted_bytes` | Number of extra copies and the space they take. |
| `generate_duplicates_csv(csv_path=None, keep="oldest")` | One row per file with a `Keep` / `Duplicate` column. |
| `move_duplicates(destination_folder=None, keep="oldest", dry_run=False)` | Moves the extra copies (default folder `<root>/Duplicates`). Returns `{original: new_path}`. Undoable. |
| `link_duplicates(keep="oldest", dry_run=False, verify=True)` | Replaces extra copies with hard links; returns `{extra: kept}`. Same drive only. Compares each pair byte for byte first (`verify`). Skips copies that changed after the search. **Not undoable.** |
| `undo_last_move(journal=None, dry_run=False)` | Undoes the last `move_duplicates`. |
| `clear_cache()` | Forgets remembered hashes. |

`keep` is `"oldest"`, `"newest"`, `"first"` (by path) or `"shortest_path"`. Other parameters: `file_stats=` (reuse the records of an existing collector), `use_cache`, `cache_dir`, `max_workers`, `journal`, `journal_dir`.

### Public API (stable names)

Everything below is exported from `Filing` (listed in `Filing.__all__`), takes plain data and works without a command line. Anything with a leading underscore may change at any time.

**Comparing scans without reading files**

```python
from Filing import compare_snapshots, FileStatsCollector

changes = compare_snapshots(old_records, new_records, only_diff=True, detect_moves=True)
for change in changes:                 # Change(path, tag, old, new)
    print(change.tag, change.path)     # Same | Modified | New | Deleted | Moved

now = FileStatsCollector(r"D:\Photos", all_files=True)
now.compare(earlier_collector_or_records)       # the other scan is the OLDER state
now.compare_with_csv(r"D:\snapshot.csv")        # compare with a CSV report; writes nothing
```

Records are the dicts in `file_stats` (or rows of a Filer CSV). Sizes may be numbers or text, so CSV rows and live records compare correctly. `compare=("size", "mtime")` chooses what counts as a change; records with `mtime_ns` on both sides are compared exactly. With `detect_moves=True`, a deleted and a new file with the same name, size and modification time (or the same `Hash` and size, if records carry one) are reported as one `Moved` file. A path listed twice on one side raises `ValueError`. Nothing touches the disk.

**Hash cache hook**

```python
class MyStore:                          # any object with these five methods is a Filing.HashStore
    def lookup(self, path, size, mtime_ns, kind): ...   # digest or None; kind is "p" (partial) or "f" (full)
    def store(self, path, size, mtime_ns, kind, digest): ...
    def prune(self, seen_paths): ...
    def save(self): ...
    def clear(self): ...

finder = DuplicateFinder(root, cache=MyStore())
finder.cache          # the store in use (or None)
finder.cache_stats    # {'enabled', 'hits', 'misses', 'entries', 'path'}
finder.files          # tuple of the scanned records
finder.clear_cache(force=True)   # a supplied store is only cleared with force=True
```

Stores are called from several threads, so they must be thread-safe, and must only return a digest for a file whose size and modification time are exactly those it was stored with. A store that raises an error is treated as empty for that file and the problem is listed in `finder.errors`. Linking duplicates compares files byte for byte, so even a wrong digest cannot cause a wrong link.

**Helpers**

| Name | Use |
|---|---|
| `parse_size("10MB")`, `parse_time("2026-11-09")`, `format_size(2048)` | The same input and output rules as the command line. |
| `FileFilter(...).matches(record)` | Apply size, date and name filters to a record without scanning. |
| `Organizer(rule).relative_path(record)` | Where a file would go, without a gatherer. Always stays inside the destination. |
| `IgnoreRules(patterns).is_ignored(relative_path)` | Test ignore rules (see Ignore rules). |
| `ensure_inside(roots, path)`, `is_inside(roots, path)`, `PathGuard(roots)` | Check that a path lies inside allowed folders. `..` and links cannot lead out; raises `UnsafePathError` (a `ValueError`). |

**Cleaning up**

```python
from Filing import EmptyFolderFinder, JunkFinder

empty = EmptyFolderFinder(r"D:\Photos", exclude=["build/"])
empty.find()                       # list of empty folders
empty.remove(dry_run=True)         # preview; remove() does it (journaled); undo_last_removal() reverses it

junk = JunkFinder(r"D:\Photos", extra_patterns=["*.bak"], kinds=("junk", "zero_byte"))
junk.find()                        # records with extra keys 'Kind' and 'Path'
junk.junk, junk.zero_byte, junk.total_bytes, junk.to_dicts()
junk.generate_csv("junk.csv")
junk.move(dry_run=True)            # preview; move() does it (undoable); undo_last_move() reverses it
```

Both accept `skip_folders`, `exclude`, `ignore_file`, `journal`, `journal_dir`, `progress` and `verbose` like the other classes.

### Progress

```python
from Filing import MediaGatherer, ConsoleProgress

bar = ConsoleProgress()                       # one updating line on stderr
MediaGatherer(root, progress=bar, verbose=False).gather_media()
bar.close()

def show(stage, done, total):                 # total is None while it is not known yet
    print(f"{stage}: {done}/{total}")
```

| Stage (constant in `Filing.progress`) | Text |
|---|---|
| `STAGE_SCAN` | `Finding folders` (total unknown until finished) |
| `STAGE_READ` | `Reading folders` |
| `STAGE_PLAN` | `Planning moves` |
| `STAGE_MOVE` | `Moving files` |
| `STAGE_HASH_PARTIAL` | `Quick-checking files` |
| `STAGE_HASH_FULL` | `Fully checking files` |
| `STAGE_LINK` | `Linking files` |

The callback runs at most about ten times a second per stage, plus once when a stage finishes (`done == total`). If it raises an exception, the operation stops with that exception, so a callback can also cancel a run; the journal is still saved, so what was already moved can be undone. `ProgressReporter(callback)` is the throttling wrapper the classes use internally.

### Journals

```python
from Filing import undo_moves
from Filing.journal import list_journals, find_latest_journal, load_journal

undo_moves()                                          # needs root_folder= or journal_dir= or journal=
undo_moves(root_folder=r"D:\Downloads", dry_run=True)
list_journals(r"D:\Downloads\.filer_journal")         # journal files, oldest first
```

A journal is a JSON file: `operation` (`move` or `duplicates`), `created`, `root`, `undone`, `moves` (`source`, `destination`, and `restored` once undone) and `created_dirs`.

### Lower-level helpers (`Filing.mover`)

`plan_moves(pairs, reserved=None, progress=None)`, `execute_moves(plan, journal=None, verbose=True, progress=None)` and `unique_destination(path, reserved=None)` are the building blocks used by both gatherers and the duplicate finder.

---

## 6. Recipes

**Tidy a Downloads folder, safely**

```
filer move D:\Downloads --all-files --organize-by "{type}/{year}" --dry-run
filer move D:\Downloads --all-files --organize-by "{type}/{year}"
filer undo D:\Downloads                      # only if you want it back
```

**Free up space from duplicates**

```
filer dupes D:\Photos                        # how much is wasted?
filer dupes D:\Photos --csv dupes.csv        # review the list
filer dupes D:\Photos --move --dry-run
filer dupes D:\Photos --move                 # undoable; look through D:\Photos\Duplicates, then delete them yourself
```

**See what changed since last time**

```
filer stats D:\Docs --all-files --csv D:\snapshot.csv
...later...
filer stats D:\Docs --all-files --diff D:\snapshot.csv --only-diff
```

**Only big, recent videos**

```
filer move D:\Media --ext .mp4,.mkv --min-size 500MB --modified-after 2026-01-01 -d D:\Big --dry-run
```

**Use it from a script and read the result**

```python
import json, subprocess
out = subprocess.run(["filer", "move", r"D:\Downloads", "--dry-run", "--json"], capture_output=True, text=True)
plan = json.loads(out.stdout)["moves"]
```

**Show progress in your own tool**

```python
def on_progress(stage, done, total):
    window.update(stage, done, total)      # any function works
MediaGatherer(root, progress=on_progress, verbose=False).gather_media()
```

---

## 7. Troubleshooting and FAQ

**The scan finds no files.** By default only media files are included. Add `all_files=True` / `--all-files`, or choose extensions with `--ext`. Also check `skip_folders` and any filters.

**"An attempt has been made to start a new process before the current process has finished its bootstrapping phase" (Windows).** Scanning many folders uses several processes, and Windows requires scripts to protect their entry point:

```python
if __name__ == "__main__":
    ...your code...
```

The `filer` command already does this.

**`move` says "this changes files; pass --yes".** The command has no terminal to ask you on (a script, a pipe, or a closed input). Add `--yes`, or use `--dry-run` to preview.

**`undo` skipped some files.** The reason is printed for each: *original location is occupied* (something now exists at the old path; move or remove it and run `undo` again) or *moved file no longer exists*. Nothing is ever overwritten. `filer undo FOLDER --list` shows the journals.

**Where are the journals and the cache?** In `<scanned folder>\.filer_journal\`. It is skipped by scans. You can delete it; you just lose undo history and remembered hashes.

**`--link` reports "Linked 0".** Hard links need both files on the same drive and a file system that supports them (NTFS does; some network and FAT drives do not). Nothing was changed.

**`skip_folders` skips less than before 1.3.** Matching is now exact (names or paths), not substring. `.git` no longer skips `.github`. Use the exact folder name, or a path.

**Does it work on Linux and macOS?** The code is cross-platform and the tests are run on Windows. Creation dates fall back to the inode change time where the system has no creation time.

**Is anything sent over the network?** No. Filer only reads and moves local files, and has no dependencies.

**Which Python versions?** 3.9 and newer (the syntax is checked against 3.9; the tests are run on the newest installed version).

---

## 8. Development

Package layout (`Filing/`):

| File | Contents |
|---|---|
| `filestatser.py` | `FileStatsCollector`: scanning, statistics, reports |
| `gatherer.py` | `MediaGatherer`, `Gatherer`: moving files |
| `duplicates.py` | `DuplicateFinder` |
| `organizer.py` | `Organizer`: organise-by rules |
| `filters.py` | `FileFilter` and size/time parsing |
| `journal.py` | `MoveJournal`, `undo_moves` |
| `mover.py` | planning and executing moves |
| `cache.py` | `HashCache`, the `HashStore` protocol |
| `compare.py` | `compare_snapshots`, `Change` |
| `ignore.py` | `IgnoreRules` (`.filerignore`) |
| `paths.py` | `ensure_inside`, `PathGuard`, `UnsafePathError` |
| `cleanup.py` | `EmptyFolderFinder`, `JunkFinder` |
| `progress.py` | progress callback helpers |
| `cli.py`, `__main__.py` | the `filer` command |

Run the tests from the repository root (they use temporary folders, so they never touch your files):

```
PYTHONPATH=Filer python -m unittest discover -s Filer/tests -p "tests_*.py"
```

On PowerShell: `$env:PYTHONPATH = "Filer"` first. The original `tests_filestatser.py` uses relative paths, so run from the repository root; it rewrites `tests/have_files/file_stats.csv`.

Speed measurements: `python Filer/bench/run.py --sizes 10000 100000 --repeat 3 --json result.json` builds reproducible test trees in a temporary folder, times scan, duplicate search (cold and warm), move preview and compare, and deletes the trees afterwards. `bench/make_tree.py` only fills folders that are empty or were made by itself.

Contributions are welcome: open an issue or a pull request on the project's GitHub page. Changes between versions are listed in `Changes.md`.

**License:** MIT.
