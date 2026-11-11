"""
Move journal: a JSON log of every file moved by a run, so the run can be undone.

Journals are stored in '<root>/.filer_journal/' by default, one file per run:
    move_20261109_143005_123456.json
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timedelta

from Filing.paths import PathGuard, UnsafePathError

JOURNAL_DIR_NAME = ".filer_journal"
JOURNAL_VERSION = 1
_JOURNAL_NAME = re.compile(r"^[a-z]+_\d{8}_\d{6}_\d{6}\.json$")  # e.g. move_20261109_143005_123456.json


def _write_json_atomic(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp_path, path)


class MoveJournal:
    """Collects moves (and the folders created for them) and saves them as a JSON journal."""

    def __init__(self, root_folder, journal_dir=None, operation="move"):
        self.root_folder = os.path.abspath(root_folder)
        self.journal_dir = os.path.abspath(journal_dir) if journal_dir else os.path.join(self.root_folder, JOURNAL_DIR_NAME)
        self.operation = operation
        self.created = datetime.now()
        self.moves = []
        self.created_dirs = []
        self.removed_dirs = []
        self.path = None

    def record_move(self, source, destination):
        self.moves.append({"source": os.path.abspath(source), "destination": os.path.abspath(destination)})

    def record_removed_dir(self, folder):
        """Remember an (empty) folder that was removed, so undo can create it again."""
        self.removed_dirs.append(os.path.abspath(folder))

    def make_dirs(self, folder):
        """Create folder (and parents), remembering which ones did not exist so undo can remove them."""
        folder = os.path.abspath(folder)
        missing = []
        current = folder
        while current and not os.path.isdir(current):
            missing.append(current)
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        os.makedirs(folder, exist_ok=True)
        for created in reversed(missing):  # top-most first
            if created not in self.created_dirs:
                self.created_dirs.append(created)

    def save(self):
        """Write the journal to disk (atomically). Returns its path, or None if nothing was recorded."""
        if not self.moves and not self.created_dirs and not self.removed_dirs:
            return None
        os.makedirs(self.journal_dir, exist_ok=True)
        if self.path is None:
            # Two runs can start within one clock tick: never reuse a name, or the older journal would be overwritten
            stamp = self.created
            self.path = os.path.join(self.journal_dir, f"{self.operation}_{stamp:%Y%m%d_%H%M%S_%f}.json")
            while os.path.exists(self.path):
                stamp += timedelta(microseconds=1)
                self.path = os.path.join(self.journal_dir, f"{self.operation}_{stamp:%Y%m%d_%H%M%S_%f}.json")
        _write_json_atomic(self.path, {
            "version": JOURNAL_VERSION,
            "operation": self.operation,
            "created": self.created.isoformat(timespec="seconds"),
            "root": self.root_folder,
            "undone": False,
            "moves": self.moves,
            "created_dirs": self.created_dirs,
            "removed_dirs": self.removed_dirs,
        })
        return self.path


def list_journals(journal_dir):
    """Journal files in journal_dir, oldest first (other files there, such as the hash cache, are ignored)."""
    if not os.path.isdir(journal_dir):
        return []
    return [os.path.join(journal_dir, name) for name in sorted(os.listdir(journal_dir))
            if _JOURNAL_NAME.match(name)]


def load_journal(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_latest_journal(journal_dir, include_undone=False):
    """Path of the newest journal that has not been undone yet (or None)."""
    for path in reversed(list_journals(journal_dir)):
        try:
            data = load_journal(path)
        except (OSError, ValueError):
            continue
        if include_undone or not data.get("undone"):
            return path
    return None


def undo_moves(journal=None, root_folder=None, journal_dir=None, dry_run=False, allowed_roots=None):
    """
    Move the files of a journal back to where they came from.

    Parameters:
        journal (str): Path of a journal JSON file. If None, the newest journal that has not
            been undone is used (looked up in journal_dir, or '<root_folder>/.filer_journal').
        dry_run (bool): Only report what would be restored.
        allowed_roots (list): The only folders undo may touch. A journal is just a file, and one that was
            edited (or came with a downloaded folder) must not be able to move files from or to other
            places, so every path in it is checked and entries outside these folders are skipped and
            reported. Default: root_folder if given, otherwise the root recorded in the journal itself
            (weaker: pass allowed_roots or root_folder when the journal is not fully trusted).
            If the files were moved to a destination outside the scanned folder, include it here.

    Files are never overwritten: if the original location is occupied again, or the moved file
    is gone, that entry is skipped and reported. Skipped entries stay in the journal, so
    undo can be run again after you resolve them. Folders created by the run are removed if empty.

    Returns:
        dict: {"journal": path, "restored": {moved_path: original_path}, "skipped": [...], "dry_run": bool,
            "recreated": [folders removed by `filer empty` that were created again]}
    """
    if journal is None:
        if journal_dir is None:
            if root_folder is None:
                raise ValueError("Give a journal path, a journal_dir or a root_folder")
            journal_dir = os.path.join(os.path.abspath(root_folder), JOURNAL_DIR_NAME)
        journal = find_latest_journal(journal_dir)
        if journal is None:
            raise FileNotFoundError(f"No journal to undo in '{journal_dir}'")
    data = load_journal(journal)
    if not isinstance(data, dict) or not isinstance(data.get("moves", []), list):
        raise ValueError(f"'{journal}' is not a Filer journal")

    if allowed_roots is None:
        anchor = root_folder if root_folder is not None else data.get("root")
        if not isinstance(anchor, str) or not anchor:
            raise UnsafePathError("the journal does not say which folder it belongs to; pass root_folder or allowed_roots")
        allowed_roots = [anchor]
    guard = PathGuard(allowed_roots)

    restored, skipped = {}, []
    for entry in reversed(data.get("moves", [])):
        if not isinstance(entry, dict):
            skipped.append({"source": None, "destination": None, "reason": "unreadable journal entry"})
            continue
        if entry.get("restored"):
            continue
        source, destination = entry.get("source"), entry.get("destination")
        try:
            # a move acts on a link itself, so only the folder part of each path is followed
            guard.check(source, resolve_name=False)
            guard.check(destination, resolve_name=False)
        except UnsafePathError as e:
            skipped.append({"source": source, "destination": destination, "reason": f"unsafe path: {e}"})
            continue
        if not os.path.exists(destination):
            skipped.append({"source": source, "destination": destination, "reason": "moved file no longer exists"})
        elif os.path.exists(source):
            skipped.append({"source": source, "destination": destination, "reason": "original location is occupied"})
        else:
            if not dry_run:
                try:
                    os.makedirs(os.path.dirname(source), exist_ok=True)
                    shutil.move(destination, source)
                except OSError as e:
                    skipped.append({"source": source, "destination": destination, "reason": str(e)})
                    continue
                entry["restored"] = True
            restored[destination] = source

    # folders removed by an "empty folders" run are created again (parents first)
    recreated, pending = [], []
    removed_dirs = data.get("removed_dirs", [])
    for folder in reversed(removed_dirs if isinstance(removed_dirs, list) else []):
        try:
            guard.check(folder, resolve_name=False)
            if not dry_run:
                os.makedirs(folder, exist_ok=True)
            recreated.append(folder)
        except (OSError, UnsafePathError) as e:
            skipped.append({"source": folder, "destination": None, "reason": f"folder not recreated: {e}"})
            pending.append(folder)

    if not dry_run:
        data["removed_dirs"] = list(reversed(pending))
        everything_restored = not pending and all(isinstance(entry, dict) and entry.get("restored") for entry in data.get("moves", []))
        if everything_restored:
            created_dirs = data.get("created_dirs", [])
            for folder in reversed(created_dirs if isinstance(created_dirs, list) else []):
                try:
                    guard.check(folder, resolve_name=False)
                    os.rmdir(folder)  # only succeeds if empty
                except (OSError, UnsafePathError):
                    pass
            data["undone"] = True
            data["undone_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json_atomic(journal, data)

    return {"journal": journal, "restored": restored, "skipped": skipped, "dry_run": dry_run, "recreated": recreated}
