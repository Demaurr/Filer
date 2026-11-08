"""
Find duplicate files quickly, then report them, move them aside or replace them with hard links.
Nothing is ever deleted.

How it stays fast: files are compared in stages and a file leaves the process as soon as it is
unique -- (1) same size, (2) same hash of the first and last 64 KB, (3) same hash of the whole
file (only for big files that survived stage 2). Hashing runs in a thread pool.

    finder = DuplicateFinder(r"D:\\Photos", skip_folders=[".git"])
    finder.find()
    finder.print_summary()
    finder.generate_duplicates_csv()
    finder.move_duplicates(dry_run=True)       # preview
    finder.move_duplicates()                   # move extra copies to <root>/Duplicates (undoable)
    finder.undo_last_move()
"""
from __future__ import annotations

import csv
import filecmp
import hashlib
import os
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from Filing.cache import HashCache
from Filing.filestatser import FileStatsCollector
from Filing.filters import parse_size
from Filing.journal import MoveJournal, JOURNAL_DIR_NAME, undo_moves
from Filing.mover import plan_moves, execute_moves
from Filing.progress import make_reporter, STAGE_HASH_PARTIAL, STAGE_HASH_FULL, STAGE_LINK

PARTIAL_CHUNK = 64 * 1024
FULL_BUFFER = 1024 * 1024
KEEP_CHOICES = ("oldest", "newest", "first", "shortest_path")


@dataclass
class DuplicateGroup:
    """Files with identical content."""
    size: int
    digest: str
    files: list = field(default_factory=list)

    @property
    def wasted_bytes(self):
        """Space taken by the extra copies."""
        return self.size * (len(self.files) - 1)


def _partial_hash(path, size):
    digest = hashlib.blake2b(digest_size=16)
    with open(path, "rb") as f:
        digest.update(f.read(PARTIAL_CHUNK))
        if size > PARTIAL_CHUNK:
            f.seek(max(PARTIAL_CHUNK, size - PARTIAL_CHUNK))
            digest.update(f.read(PARTIAL_CHUNK))
    return digest.hexdigest()


def _full_hash(path, size):
    digest = hashlib.blake2b(digest_size=16)
    buffer = bytearray(FULL_BUFFER)
    view = memoryview(buffer)
    with open(path, "rb", buffering=0) as f:
        while True:
            read = f.readinto(view)
            if not read:
                break
            digest.update(view[:read])
    return digest.hexdigest()


class DuplicateFinder:
    """
    Parameters:
        root_folder (str): Folder to search (also where the journal and default report go).
        destination_folder (str): Where move_duplicates() puts extra copies (default '<root>/Duplicates').
            It is excluded from the search.
        skip_folders (list): Folder names or paths to skip.
        min_size: Ignore files smaller than this (default 1 byte; empty files are never duplicates).
        file_stats (list): Reuse the file_stats of an existing FileStatsCollector instead of scanning again.
        journal / journal_dir: Journal of moves so they can be undone (see MoveJournal).
        max_workers (int): Hashing threads (default: Python's choice, based on CPU count).
        use_cache (bool): Remember hashes between runs (default True) so unchanged files are not read
            again. A file's cached hash is used only while its size and modification time are unchanged.
            Stored in '<journal_dir or root/.filer_journal>/hash_cache.json'.
        cache_dir (str): Folder for the cache file (default: the journal folder).
        progress: function(stage, done, total) called while scanning, hashing, moving and linking (see Filing.progress).
        verbose (bool): Set False to silence informational messages (reports such as print_summary still print).
        filter_options: max_size, modified_after, modified_before, name_glob, name_regex (see FileStatsCollector).
    """

    def __init__(self, root_folder, destination_folder=None, skip_folders=None, min_size=1,
                 file_stats=None, journal=True, journal_dir=None, max_workers=None,
                 use_cache=True, cache_dir=None, progress=None, verbose=True, **filter_options):
        self.verbose = verbose
        self._progress = make_reporter(progress)
        self.root_folder = root_folder
        self.destination_folder = destination_folder or os.path.join(root_folder, "Duplicates")
        self.min_size = max(1, parse_size(min_size) or 1)
        self.journal_enabled = journal
        self.journal_dir = journal_dir
        self.max_workers = max_workers
        self.groups = None
        self.errors = []
        self.moved_files = {}
        self._found_at = None
        self._journal = None
        self._cache_path = HashCache.default_path(root_folder, cache_dir or journal_dir)
        self._cache = HashCache(self._cache_path) if use_cache else None
        self.cache_hits = 0

        if file_stats is None:
            skips = list(skip_folders) if skip_folders else []
            skips += [self.destination_folder, JOURNAL_DIR_NAME]
            if journal_dir:
                skips.append(journal_dir)
            if cache_dir:
                skips.append(cache_dir)
            file_stats = FileStatsCollector(
                root_folder, all_files=True, skip_folders=skips, min_size=self.min_size,
                progress=self._progress, verbose=verbose, **filter_options
            ).file_stats
        self._file_stats = file_stats

    # ---- finding -------------------------------------------------------------------------

    def find(self):
        """
        Find duplicate groups. Returns a list of DuplicateGroup, biggest waste first.
        Files that cannot be read are skipped and listed in self.errors.
        """
        self.errors = []
        if self._cache is not None:
            self._cache.hits = self._cache.misses = 0
        started = time.perf_counter()
        by_size = defaultdict(list)
        for stat in self._file_stats:
            size = stat["File Size (Bytes)"]
            if size >= self.min_size:
                by_size[(size,)].append(os.path.join(stat["Source Folder"], stat["File Name"]))
        candidates = {key: paths for key, paths in by_size.items() if len(paths) > 1}

        # Stage 2: first/last 64 KB. Files up to 128 KB are covered completely, so they are final.
        partial = self._refine(candidates, _partial_hash, "p")
        final = {key: paths for key, paths in partial.items() if key[0] <= 2 * PARTIAL_CHUNK}
        needs_full = {key: paths for key, paths in partial.items() if key[0] > 2 * PARTIAL_CHUNK}

        # Stage 3: whole-file hash, only for what is left.
        final.update(self._refine(needs_full, _full_hash, "f"))
        self._save_cache()

        self.groups = sorted(
            (DuplicateGroup(size=key[0], digest=key[-1], files=sorted(paths)) for key, paths in final.items()),
            key=lambda group: (-group.wasted_bytes, group.files[0]),
        )
        self._found_at = time.time()
        cache_note = f" ({self.cache_hits} hash(es) reused from cache)" if self._cache is not None else ""
        self._say(f"Checked {len(self._file_stats)} files, found {self.duplicate_count} duplicate file(s) "
              f"in {len(self.groups)} group(s) in {time.perf_counter() - started:.2f} seconds{cache_note}.")
        return self.groups

    def _say(self, message):
        if self.verbose:
            print(message)

    def _cached_hash(self, kind, hash_function, path, size):
        """hash_function(path, size), or the remembered hash if the file is unchanged since it was made."""
        if self._cache is None:
            return hash_function(path, size)
        info = os.stat(path)  # raises OSError for vanished files, handled by the caller
        if info.st_size != size:
            return hash_function(path, size)  # changed since the scan: do not use the cache
        cached = self._cache.lookup(path, size, info.st_mtime_ns, kind)
        if cached:
            return cached
        digest = hash_function(path, size)
        self._cache.store(path, size, info.st_mtime_ns, kind, digest)
        return digest

    def _save_cache(self):
        if self._cache is None:
            return
        self.cache_hits = self._cache.hits
        try:
            self._cache.prune(os.path.join(s["Source Folder"], s["File Name"]) for s in self._file_stats)
            self._cache.save()
        except OSError as e:
            self.errors.append((self._cache.path, f"could not save hash cache: {e}"))

    def clear_cache(self):
        """Forget all remembered hashes (and delete the cache file)."""
        if self._cache is not None:
            self._cache.clear()
        else:
            HashCache(self._cache_path).clear()  # caching is off for this run, but the file may still exist

    def _refine(self, groups, hash_function, kind):
        """Split each group by a content hash; groups that fall to one file disappear."""
        flat = [(key, path) for key, paths in groups.items() for path in paths]
        if not flat:
            return {}

        stage = STAGE_HASH_PARTIAL if kind == "p" else STAGE_HASH_FULL
        counter = {"done": 0}
        counter_lock = threading.Lock()

        def safe_hash(item):
            key, path = item
            try:
                return self._cached_hash(kind, hash_function, path, key[0])
            except OSError as e:
                self.errors.append((path, str(e)))
                return None
            finally:
                with counter_lock:
                    counter["done"] += 1
                    done = counter["done"]
                self._progress(stage, done, len(flat))

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            digests = list(pool.map(safe_hash, flat))
        refined = defaultdict(list)
        for (key, path), digest in zip(flat, digests):
            if digest is not None:
                refined[key + (digest,)].append(path)
        return {key: paths for key, paths in refined.items() if len(paths) > 1}

    def _ensure_found(self):
        if self.groups is None:
            self.find()

    @property
    def duplicate_count(self):
        """Number of extra copies (files that could go, keeping one per group)."""
        return sum(len(group.files) - 1 for group in self.groups or [])

    @property
    def wasted_bytes(self):
        """Total space taken by the extra copies."""
        return sum(group.wasted_bytes for group in self.groups or [])

    # ---- reporting -----------------------------------------------------------------------

    def print_summary(self, top=10):
        """Print totals and the biggest duplicate groups."""
        self._ensure_found()
        print("\nDuplicate Summary:")
        print(f"Duplicate groups: {len(self.groups)}")
        print(f"Extra copies: {self.duplicate_count}")
        print(f"Reclaimable space: {FileStatsCollector.format_size(self.wasted_bytes)}")
        for group in self.groups[:top]:
            print(f"\n{len(group.files)} copies x {FileStatsCollector.format_size(group.size)}")
            for path in group.files:
                print(f"\t{path}")
        if len(self.groups) > top:
            print(f"\n... and {len(self.groups) - top} more group(s)")

    def generate_duplicates_csv(self, csv_path=None, keep="oldest"):
        """
        Write all duplicate groups to a CSV (one row per file). The 'Action' column says which
        file would be kept ('Keep') and which are extra copies ('Duplicate') for the given keep rule.
        """
        self._ensure_found()
        if not self.groups:
            print("NO DUPLICATES FOUND: Nothing To Create Csv of")
            return -1
        if csv_path is None:
            csv_path = os.path.join(self.root_folder, f"duplicates_{datetime.now():%Y%m%d_%H%M%S}.csv")
        try:
            with open(csv_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["Group", "File Path", "File Size (Bytes)", "File Size (Human Readable)", "Hash", "Action"])
                for number, group in enumerate(self.groups, start=1):
                    keeper = self._choose_keeper(group.files, keep)
                    for path in group.files:
                        writer.writerow([number, path, group.size, FileStatsCollector.format_size(group.size),
                                         group.digest, "Keep" if path == keeper else "Duplicate"])
            print(f"\nDuplicates CSV file generated: {csv_path}")
        except Exception as e:
            print(f"Error generating duplicates CSV: {e}")

    # ---- actions -------------------------------------------------------------------------

    @staticmethod
    def _choose_keeper(files, keep):
        if keep not in KEEP_CHOICES:
            raise ValueError(f"keep must be one of {KEEP_CHOICES}, not {keep!r}")

        def mtime(path):
            try:
                return os.path.getmtime(path)
            except OSError:
                return float("inf")

        if keep == "oldest":
            return min(files, key=lambda p: (mtime(p), p))
        if keep == "newest":
            return max(files, key=lambda p: (mtime(p), p))
        if keep == "shortest_path":
            return min(files, key=lambda p: (len(p), p))
        return min(files)

    def _extra_copies(self, keep):
        """List of (kept_file, extra_copy) pairs for every group."""
        self._ensure_found()
        pairs = []
        for group in self.groups:
            keeper = self._choose_keeper(group.files, keep)
            pairs.extend((keeper, path) for path in group.files if path != keeper)
        return pairs

    def move_duplicates(self, destination_folder=None, keep="oldest", dry_run=False):
        """
        Move every extra copy into destination_folder (default '<root>/Duplicates'), keeping one file
        per group. Name clashes are renamed ('name(1).ext'). Undo with undo_last_move().

        Parameters:
            keep (str): Which copy stays: 'oldest' (default), 'newest', 'first' (by path) or 'shortest_path'.
            dry_run (bool): Move nothing; return the planned moves.

        Returns:
            dict: {original_path: new_path}
        """
        destination_folder = destination_folder or self.destination_folder
        pairs = [(extra, os.path.join(destination_folder, os.path.basename(extra)))
                 for _, extra in self._extra_copies(keep)]
        plan = plan_moves(pairs)
        if dry_run:
            self._say(f"\nDRY RUN: {len(plan)} duplicate(s) would be moved to {destination_folder} (nothing was changed)")
            return dict(plan)
        if not plan:
            self._say("\nNo duplicates to move.")
            return {}
        self._journal = MoveJournal(self.root_folder, self.journal_dir, operation="duplicates") if self.journal_enabled else None
        moved, failed = execute_moves(plan, journal=self._journal, verbose=self.verbose,
                                      progress=self._progress if self._progress.enabled else None)
        self.errors.extend(failed)
        self.moved_files.update(moved)
        self._say(f"Total Duplicates Moved: {len(moved)}")
        if self._journal is not None and self._journal.path:
            self._say(f"Journal saved: {self._journal.path} (undo with undo_last_move())")
        return moved

    def link_duplicates(self, keep="oldest", dry_run=False, verify=True):
        """
        Replace every extra copy with a hard link to the kept file: same content, same paths, but the
        data is stored once. Needs the files on the same drive and a file system that supports hard
        links (NTFS does). Copies that changed since the search are left alone.

        verify (bool): Compare the two files byte for byte before linking (default True). The hash
        cache trusts size and modification time, so this protects against a file that was edited
        without either changing. It reads each pair of files that would be linked.

        Hard links CANNOT be undone by undo_last_move(); use dry_run=True first, or move_duplicates()
        if you want to be able to undo.

        Returns:
            dict: {extra_copy: kept_file} for every copy that was (or, in a dry run, would be) linked.
        """
        linked = {}
        pairs = self._extra_copies(keep)
        for number, (keeper, extra) in enumerate(pairs, start=1):
            self._progress(STAGE_LINK, number - 1, len(pairs))
            size = self._size_of(keeper, extra)
            if size is None or not self._unchanged(keeper, size) or not self._unchanged(extra, size):
                self.errors.append((extra, "changed since the search; skipped"))
                continue
            try:
                if os.path.samefile(keeper, extra):
                    continue  # already the same file
                if verify and not filecmp.cmp(keeper, extra, shallow=False):
                    self.errors.append((extra, "content differs from the kept file; skipped"))
                    continue
                if not dry_run:
                    temporary = extra + ".filer_link_tmp"
                    os.link(keeper, temporary)
                    os.replace(temporary, extra)  # atomic: the path never disappears
                linked[extra] = keeper
            except OSError as e:
                self.errors.append((extra, str(e)))
                print(f"Could not link '{extra}': {e}")
        label = "DRY RUN: would link" if dry_run else "Linked"
        self._progress(STAGE_LINK, len(pairs), len(pairs))
        self._say(f"\n{label} {len(linked)} duplicate(s) (nothing was deleted)")
        return linked

    def _size_of(self, *paths):
        for group in self.groups or []:
            if paths[0] in group.files:
                return group.size
        return None

    def _unchanged(self, path, size):
        try:
            info = os.stat(path)
        except OSError:
            return False
        return info.st_size == size and info.st_mtime <= self._found_at

    def undo_last_move(self, journal=None, dry_run=False):
        """Undo the most recent move_duplicates() (see MediaGatherer.undo_last_move)."""
        result = undo_moves(journal, root_folder=self.root_folder, journal_dir=self.journal_dir, dry_run=dry_run)
        verb = "Would restore" if dry_run else "Restored"
        self._say(f"{verb} {len(result['restored'])} file(s) from {result['journal']}")
        for item in result["skipped"]:
            self._say(f"\tSkipped {item['source']}: {item['reason']}")
        return result
