"""
Tidy-up helpers: find empty folders and junk files. Both only look first; nothing changes unless you ask.

    finder = EmptyFolderFinder(r"D:\\Photos")
    finder.find()                    # list of empty folders (a folder holding only empty folders counts too)
    finder.remove(dry_run=True)      # preview
    finder.remove()                  # remove them (journaled: undo_last_removal() puts them back)

    junk = JunkFinder(r"D:\\Photos")
    junk.find()                      # Thumbs.db, desktop.ini, .DS_Store, ._*, ~$*, *.tmp ... and zero-byte files
    junk.print_summary()
    junk.move(dry_run=True)          # preview: files would go to D:\\Photos\\Junk, keeping their sub-folders
    junk.move()                      # move them (undoable); nothing is ever deleted

Removing an empty folder deletes no file (there is none in it), and uses rmdir, which refuses to
remove anything that is not empty, so a file that appears at the last moment is safe.
"""
from __future__ import annotations

import csv
import os
from datetime import datetime
from typing import Iterable, List, Optional

from Filing.filestatser import FileStatsCollector
from Filing.filters import FileFilter
from Filing.ignore import IGNORE_FILE_NAME, load_ignore_rules
from Filing.journal import MoveJournal, JOURNAL_DIR_NAME, undo_moves
from Filing.mover import plan_moves, execute_moves
from Filing.paths import PathGuard, UnsafePathError
from Filing.progress import make_reporter, STAGE_SCAN

__all__ = ["EmptyFolderFinder", "JunkFinder", "JUNK_PATTERNS", "KEEP_EMPTY_NAMES", "JUNK_KINDS"]

# Names are matched without regard to case. Only things that are never wanted and always re-created.
JUNK_PATTERNS = (
    "Thumbs.db", "ehthumbs.db", "ehthumbs_vista.db",  # Windows thumbnail caches
    "desktop.ini",                                     # Windows folder settings
    ".DS_Store", "._*",                                # macOS folder settings and AppleDouble files
    "~$*", ".~lock.*#",                                # Office and LibreOffice lock files
    "*.tmp", "*.temp", "*~",                           # temporary and editor backup files
)
# Zero-byte files with these names are normal and are never reported as junk.
KEEP_EMPTY_NAMES = frozenset({
    "__init__.py", ".gitkeep", ".keep", ".gitignore", ".gitattributes", ".nojekyll", ".nomedia", "py.typed",
    ".htaccess", ".dockerignore", ".npmignore", IGNORE_FILE_NAME,
})
KIND_JUNK = "junk"
KIND_ZERO = "zero_byte"
JUNK_KINDS = (KIND_JUNK, KIND_ZERO)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_link(entry):
    """Symbolic link or Windows junction/reparse point (never followed, never removed)."""
    try:
        if entry.is_symlink():
            return True
        is_junction = getattr(entry, "is_junction", None)
        if is_junction is not None:
            return bool(is_junction())
        return bool(getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return True  # cannot tell: treat as something we must not touch


class _Scanning:
    """Shared option handling: skip rules, ignore rules, journal, progress, messages."""

    def _setup(self, root_folder, skip_folders, exclude, ignore_file, journal, journal_dir, progress, verbose):
        if not os.path.isdir(root_folder):
            raise FileNotFoundError(f"'{root_folder}' is not a folder")
        self.root_folder = os.path.abspath(root_folder)
        self.skip_folders = list(skip_folders) if skip_folders else []
        self.exclude = exclude
        self.ignore_file = ignore_file
        self.journal_enabled = journal
        self.journal_dir = journal_dir
        self.verbose = verbose
        self._progress = make_reporter(progress)
        self.errors: List[tuple] = []
        self._journal = None
        self.last_journal_path = None

    def _say(self, message):
        if self.verbose:
            print(message)

    def _journal_folder(self):
        return os.path.abspath(self.journal_dir) if self.journal_dir else os.path.join(self.root_folder, JOURNAL_DIR_NAME)


class EmptyFolderFinder(_Scanning):
    """
    Finds folders that hold no files at all. A folder whose only content is empty folders is empty too
    (removing them all, deepest first, leaves no empty folder behind).

    A folder is NOT empty, and is never listed, if it holds any file or link, a folder that is skipped
    or ignored (it might contain something), or anything that could not be read.

    Parameters:
        root_folder: Folder to search. It is never listed itself unless include_root=True.
        skip_folders: Folder names or paths to leave out (see FileStatsCollector); they count as content.
        exclude / ignore_file: Ignore rules (see Filing.ignore); ignored folders count as content too.
        include_root (bool): also report root_folder when everything below it is empty.
        journal / journal_dir: Record removals so undo_last_removal() can put the folders back.
        progress: function(stage, done, total) while searching. verbose: False silences messages.
    """

    def __init__(self, root_folder, skip_folders=None, exclude=None, ignore_file=True, include_root=False,
                 journal=True, journal_dir=None, progress=None, verbose=True):
        self._setup(root_folder, skip_folders, exclude, ignore_file, journal, journal_dir, progress, verbose)
        self.include_root = include_root
        self.folders: Optional[List[str]] = None
        self.removed: List[str] = []

    # ---- finding -------------------------------------------------------------------------

    def find(self) -> List[str]:
        """Search the tree. Returns the empty folders, parents before their children."""
        collector_rules = load_ignore_rules(self.root_folder, self.exclude, self.ignore_file)
        skip_names, skip_paths = FileStatsCollector._split_skip_folders(self.skip_folders + [JOURNAL_DIR_NAME])
        if self.journal_dir:
            skip_paths.add(os.path.normcase(os.path.abspath(self.journal_dir)))
        root = self.root_folder
        order, children, blocked = [], {}, {}
        stack = [(root, "")]
        while stack:
            folder, rel = stack.pop()
            order.append(folder)
            self._progress(STAGE_SCAN, len(order), None)
            kids, has_content = [], False
            try:
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if not entry.is_dir(follow_symlinks=False) or _is_link(entry):
                            has_content = True  # a file, a link, anything that is not a plain folder
                            continue
                        child_rel = rel + "/" + entry.name if rel else entry.name
                        if (os.path.normcase(entry.name) in skip_names
                                or os.path.normcase(os.path.abspath(entry.path)) in skip_paths
                                or (collector_rules is not None and collector_rules.dir_ignored(child_rel))):
                            has_content = True  # left out on purpose: we cannot say it is empty
                            continue
                        kids.append(entry.path)
                        stack.append((entry.path, child_rel))
            except OSError as e:
                has_content = True  # unreadable: not known to be empty
                self.errors.append((folder, str(e)))
            children[folder] = kids
            blocked[folder] = has_content
        empty = {}
        for folder in reversed(order):  # children are always after their parent in `order`
            empty[folder] = not blocked[folder] and all(empty[k] for k in children[folder])
        self.folders = [f for f in order if empty[f] and (self.include_root or f != root)]
        self._progress(STAGE_SCAN, len(order), len(order))
        self._say(f"Checked {len(order)} folder(s), found {len(self.folders)} empty folder(s).")
        return self.folders

    def to_dicts(self) -> List[dict]:
        """JSON-friendly result: [{'path': ..., 'relative': ...}, ...]."""
        self._ensure_found()
        return [{"path": f, "relative": os.path.relpath(f, self.root_folder)} for f in self.folders]

    def print_summary(self):
        self._ensure_found()
        print(f"\nEmpty folders: {len(self.folders)}")
        for folder in self.folders:
            print(f"\t{folder}")

    def _ensure_found(self):
        if self.folders is None:
            self.find()

    # ---- removing ------------------------------------------------------------------------

    def remove(self, dry_run: bool = False) -> List[str]:
        """
        Remove the empty folders found (deepest first), nothing else. Only folders that are still empty
        are removed: a folder that got a file in the meantime is left alone and listed in self.errors.
        Every removal is journaled (unless journal=False) so undo_last_removal() can recreate the folders.

        Returns:
            list of the folders removed (or, with dry_run=True, that would be removed).
        """
        self._ensure_found()
        deepest_first = sorted(self.folders, key=lambda f: (-f.count(os.sep), f))
        if dry_run:
            self._say(f"\nDRY RUN: {len(deepest_first)} empty folder(s) would be removed (nothing was changed)")
            return deepest_first
        guard = PathGuard([self.root_folder])
        journal = MoveJournal(self.root_folder, self.journal_dir, operation="empty") if self.journal_enabled else None
        removed = []
        try:
            for folder in deepest_first:
                try:
                    guard.check(folder, resolve_name=False, allow_root=self.include_root)
                    if os.path.islink(folder):
                        raise OSError("is a link")
                    os.rmdir(folder)  # refuses unless the folder is empty
                except (OSError, UnsafePathError) as e:
                    self.errors.append((folder, str(e)))
                    continue
                removed.append(folder)
                if journal is not None:
                    journal.record_removed_dir(folder)
        finally:
            if journal is not None:
                journal.save()
                self.last_journal_path = journal.path
        self.removed = removed
        gone = set(removed)
        self.folders = [f for f in self.folders if f not in gone]
        self._say(f"Removed {len(removed)} empty folder(s) (no file was touched)")
        if self.last_journal_path:
            self._say(f"Journal saved: {self.last_journal_path} (undo with undo_last_removal())")
        return removed

    def undo_last_removal(self, journal=None, dry_run=False):
        """Recreate the folders removed by the last remove() (or the given journal). See undo_moves()."""
        result = undo_moves(journal, root_folder=self.root_folder, journal_dir=self._journal_folder(),
                            dry_run=dry_run, allowed_roots=[self.root_folder])
        verb = "Would recreate" if dry_run else "Recreated"
        self._say(f"{verb} {len(result.get('recreated', []))} folder(s) from {result['journal']}")
        return result


class JunkFinder(_Scanning):
    """
    Finds junk files by name (Thumbs.db, desktop.ini, .DS_Store, temp files...) and zero-byte files.
    Reports only; move() puts them in a Junk folder (undoable). Nothing is ever deleted.

    Parameters:
        root_folder: Folder to search.
        patterns: Name patterns (globs, case-insensitive) that mean junk. Default: JUNK_PATTERNS.
        extra_patterns: More patterns added to `patterns`.
        kinds: Which to look for: any of 'junk' (by name) and 'zero_byte' (size 0). Default: both.
        keep_empty_names: Zero-byte files with these names (e.g. __init__.py, .gitkeep) are not reported.
        destination_folder: Where move() puts them (default '<root>/Junk'); it is excluded from the search.
        skip_folders, exclude, ignore_file, journal, journal_dir, progress, verbose: as for the other finders.
    """

    def __init__(self, root_folder, patterns=None, extra_patterns=None, kinds=JUNK_KINDS, keep_empty_names=None,
                 destination_folder=None, skip_folders=None, exclude=None, ignore_file=True, journal=True,
                 journal_dir=None, progress=None, verbose=True):
        self._setup(root_folder, skip_folders, exclude, ignore_file, journal, journal_dir, progress, verbose)
        kinds = (kinds,) if isinstance(kinds, str) else tuple(kinds)
        if not kinds or set(kinds) - set(JUNK_KINDS):
            raise ValueError(f"kinds must be a non-empty selection of {JUNK_KINDS}, got {kinds!r}")
        self.kinds = kinds
        self.patterns = tuple(JUNK_PATTERNS if patterns is None else _text_list(patterns, "patterns"))
        self.patterns += tuple(_text_list(extra_patterns or (), "extra_patterns"))
        self.keep_empty_names = frozenset(n.lower() for n in (KEEP_EMPTY_NAMES if keep_empty_names is None else keep_empty_names))
        self.destination_folder = os.path.abspath(destination_folder or os.path.join(self.root_folder, "Junk"))
        self.files: Optional[List[dict]] = None
        self.moved_files: dict = {}
        self._name_filter = FileFilter(name_glob=list(self.patterns)) if self.patterns else None

    # ---- finding -------------------------------------------------------------------------

    def find(self) -> List[dict]:
        """
        Search the tree. Returns the records of the files found, each with the extra keys 'Kind'
        ('junk' or 'zero_byte') and 'Path'. A file that is both is reported as junk.
        """
        skips = self.skip_folders + [self.destination_folder, JOURNAL_DIR_NAME]
        if self.journal_dir:
            skips.append(self.journal_dir)
        want_zero = KIND_ZERO in self.kinds
        want_junk = KIND_JUNK in self.kinds and self._name_filter is not None
        options = {"name_glob": list(self.patterns)} if (want_junk and not want_zero) else {}
        collector = FileStatsCollector(
            self.root_folder, all_files=True, skip_folders=skips, exclude=self.exclude, ignore_file=self.ignore_file,
            progress=self._progress if self._progress.enabled else None, verbose=False, **options)
        found = []
        for stat in collector.file_stats:
            name = stat["File Name"]
            if want_junk and self._name_filter.name_ok(name):
                kind = KIND_JUNK
            elif want_zero and stat["File Size (Bytes)"] == 0 and name.lower() not in self.keep_empty_names:
                kind = KIND_ZERO
            else:
                continue
            found.append({**stat, "Kind": kind, "Path": os.path.join(stat["Source Folder"], name)})
        found.sort(key=lambda r: r["Path"])
        self.files = found
        self._say(f"Checked {len(collector.file_stats)} files, found {len(found)} junk or empty file(s).")
        return found

    @property
    def junk(self) -> List[dict]:
        self._ensure_found()
        return [r for r in self.files if r["Kind"] == KIND_JUNK]

    @property
    def zero_byte(self) -> List[dict]:
        self._ensure_found()
        return [r for r in self.files if r["Kind"] == KIND_ZERO]

    @property
    def total_bytes(self) -> int:
        self._ensure_found()
        return sum(r["File Size (Bytes)"] for r in self.files)

    def _ensure_found(self):
        if self.files is None:
            self.find()

    # ---- reporting -----------------------------------------------------------------------

    def to_dicts(self) -> List[dict]:
        """JSON-friendly result: [{'path', 'kind', 'size'}, ...]."""
        self._ensure_found()
        return [{"path": r["Path"], "kind": r["Kind"], "size": r["File Size (Bytes)"]} for r in self.files]

    def print_summary(self, top: int = 20):
        self._ensure_found()
        print("\nJunk Summary:")
        print(f"Junk files (by name): {len(self.junk)}")
        print(f"Zero-byte files: {len(self.zero_byte)}")
        print(f"Space taken: {FileStatsCollector.format_size(self.total_bytes)}")
        for record in self.files[:top]:
            print(f"\t[{record['Kind']}] {record['Path']}")
        if len(self.files) > top:
            print(f"\t... and {len(self.files) - top} more")

    def generate_csv(self, csv_path=None) -> Optional[str]:
        """Write the findings to a CSV file (default: junk_<root name>_<time>.csv in the current folder)."""
        self._ensure_found()
        if csv_path is None:
            csv_path = f"junk_{os.path.basename(self.root_folder) or 'root'}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Path", "Kind", "Size (Bytes)", "Modification Date"])
            for r in self.files:
                writer.writerow([r["Path"], r["Kind"], r["File Size (Bytes)"], r["Modification Date"]])
        self._say(f"Junk CSV file generated: {csv_path}")
        return csv_path

    # ---- moving --------------------------------------------------------------------------

    def move(self, destination_folder=None, dry_run: bool = False) -> dict:
        """
        Move the files found into the Junk folder, keeping their sub-folder layout, so nothing collides
        and everything can be found again. Undoable with undo_last_move().

        Returns:
            dict: {original_path: new_path} (planned only, with dry_run=True)
        """
        self._ensure_found()
        destination = os.path.abspath(destination_folder or self.destination_folder)
        pairs = [(r["Path"], os.path.join(destination, os.path.relpath(r["Path"], self.root_folder))) for r in self.files]
        plan = plan_moves(pairs)
        if dry_run:
            self._say(f"\nDRY RUN: {len(plan)} file(s) would be moved to {destination} (nothing was changed)")
            return dict(plan)
        if not plan:
            self._say("\nNo junk files to move.")
            return {}
        self._last_destination = destination
        journal = MoveJournal(self.root_folder, self.journal_dir, operation="junk") if self.journal_enabled else None
        moved, failed = execute_moves(plan, journal=journal, verbose=False, allowed_roots=[self.root_folder, destination])
        self.errors.extend(failed)
        self.moved_files = moved
        self.last_journal_path = journal.path if journal is not None else None
        self._say(f"Total Junk Files Moved: {len(moved)}")
        if self.last_journal_path:
            self._say(f"Journal saved: {self.last_journal_path} (undo with undo_last_move())")
        return moved

    def undo_last_move(self, journal=None, dry_run=False):
        """Undo the most recent move() (see undo_moves)."""
        folders = [self.root_folder, self.destination_folder, getattr(self, "_last_destination", None)]
        result = undo_moves(journal, root_folder=self.root_folder, journal_dir=self._journal_folder(), dry_run=dry_run,
                            allowed_roots=[f for f in folders if f])
        verb = "Would restore" if dry_run else "Restored"
        self._say(f"{verb} {len(result['restored'])} file(s) from {result['journal']}")
        return result


def _text_list(values, name):
    if isinstance(values, str):
        values = [values]
    values = list(values)
    if not all(isinstance(v, str) and v for v in values):
        raise ValueError(f"{name} must be non-empty text patterns")
    return values
