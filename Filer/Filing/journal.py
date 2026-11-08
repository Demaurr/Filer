"""
Move journal: a JSON log of every file moved by a run, so the run can be undone.

Journals are stored in '<root>/.filer_journal/' by default, one file per run:
    move_20261109_143005_123456.json
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime

JOURNAL_DIR_NAME = ".filer_journal"
JOURNAL_VERSION = 1


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
        self.path = None

    def record_move(self, source, destination):
        self.moves.append({"source": os.path.abspath(source), "destination": os.path.abspath(destination)})

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
        if not self.moves and not self.created_dirs:
            return None
        os.makedirs(self.journal_dir, exist_ok=True)
        if self.path is None:
            self.path = os.path.join(self.journal_dir, f"{self.operation}_{self.created:%Y%m%d_%H%M%S_%f}.json")
        _write_json_atomic(self.path, {
            "version": JOURNAL_VERSION,
            "operation": self.operation,
            "created": self.created.isoformat(timespec="seconds"),
            "root": self.root_folder,
            "undone": False,
            "moves": self.moves,
            "created_dirs": self.created_dirs,
        })
        return self.path


def list_journals(journal_dir):
    """Journal files in journal_dir, oldest first."""
    if not os.path.isdir(journal_dir):
        return []
    return [os.path.join(journal_dir, name) for name in sorted(os.listdir(journal_dir))
            if name.endswith(".json")]


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


def undo_moves(journal=None, root_folder=None, journal_dir=None, dry_run=False):
    """
    Move the files of a journal back to where they came from.

    Parameters:
        journal (str): Path of a journal JSON file. If None, the newest journal that has not
            been undone is used (looked up in journal_dir, or '<root_folder>/.filer_journal').
        dry_run (bool): Only report what would be restored.

    Files are never overwritten: if the original location is occupied again, or the moved file
    is gone, that entry is skipped and reported. Skipped entries stay in the journal, so
    undo can be run again after you resolve them. Folders created by the run are removed if empty.

    Returns:
        dict: {"journal": path, "restored": {moved_path: original_path}, "skipped": [...], "dry_run": bool}
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

    restored, skipped = {}, []
    for entry in reversed(data.get("moves", [])):
        if entry.get("restored"):
            continue
        source, destination = entry["source"], entry["destination"]
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

    if not dry_run:
        everything_restored = all(entry.get("restored") for entry in data.get("moves", []))
        if everything_restored:
            for folder in reversed(data.get("created_dirs", [])):
                try:
                    os.rmdir(folder)  # only succeeds if empty
                except OSError:
                    pass
            data["undone"] = True
            data["undone_at"] = datetime.now().isoformat(timespec="seconds")
        _write_json_atomic(journal, data)

    return {"journal": journal, "restored": restored, "skipped": skipped, "dry_run": dry_run}
