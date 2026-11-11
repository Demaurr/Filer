"""
Compare two lists of file records without touching the disk.

    from Filing import compare_snapshots

    changes = compare_snapshots(old_records, new_records, only_diff=True)
    for change in changes:
        print(change.tag, change.path)

A record is a dict like the ones in FileStatsCollector.file_stats (or a row read from a Filer
CSV report). Only these keys are used:

    'Source Folder', 'File Name'      identify the file (folder + name is the path)
    'File Size (Bytes)'               int, or text such as '2048' (CSV rows)
    'mtime_ns'                        optional exact modification time; if both records have it, it is used
    'Modification Date'               text, used when 'mtime_ns' is not on both records
    'Hash'                            optional content hash, used to recognise moved files

Tags: Same, Modified, New, Deleted and (only with detect_moves=True) Moved.
Nothing here reads, writes or stats a file.
"""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional

TAG_SAME = "Same"
TAG_MODIFIED = "Modified"
TAG_NEW = "New"
TAG_DELETED = "Deleted"
TAG_MOVED = "Moved"
TAGS = (TAG_SAME, TAG_MODIFIED, TAG_NEW, TAG_DELETED, TAG_MOVED)

COMPARE_FIELDS = ("size", "mtime")

__all__ = ["Change", "compare_snapshots", "TAGS", "TAG_SAME", "TAG_MODIFIED", "TAG_NEW", "TAG_DELETED", "TAG_MOVED"]


@dataclass(frozen=True)
class Change:
    """
    One line of a comparison.

    path: the file's path (the new path for Moved files, the old path for Deleted files).
    tag: one of TAGS.
    old / new: the record on each side (None where the file does not exist on that side).
    """
    path: str
    tag: str
    old: Optional[Mapping[str, Any]] = None
    new: Optional[Mapping[str, Any]] = None

    @property
    def record(self):
        """The newest record available: the new one, or the old one for Deleted files."""
        return self.new if self.new is not None else self.old

    def as_row(self):
        """A copy of the record with a 'Tag' key added (the layout of the difference CSV)."""
        return {**self.record, "Tag": self.tag}

    def to_dict(self):
        """Plain, JSON-friendly form."""
        return {"path": self.path, "tag": self.tag,
                "old": None if self.old is None else dict(self.old),
                "new": None if self.new is None else dict(self.new)}


def _path_of(record, side, position):
    try:
        folder, name = record["Source Folder"], record["File Name"]
    except (KeyError, TypeError):
        raise ValueError(
            f"{side} record #{position} needs 'Source Folder' and 'File Name' (got {_describe(record)})") from None
    if not isinstance(folder, str) or not isinstance(name, str) or not name:
        raise ValueError(f"{side} record #{position} has an invalid 'Source Folder' or 'File Name'")
    return os.path.join(folder, name)


def _describe(record):
    return f"keys {sorted(record)}" if isinstance(record, Mapping) else type(record).__name__


def _size_of(record, side, path):
    value = record.get("File Size (Bytes)")
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{side} record for {path!r} has no 'File Size (Bytes)'")
    try:
        number = int(str(value).strip()) if not isinstance(value, int) else value
    except ValueError:
        raise ValueError(f"{side} record for {path!r} has an invalid size: {value!r}") from None
    if number < 0:
        raise ValueError(f"{side} record for {path!r} has a negative size: {value!r}")
    return number


def _mtime_token(old, new, old_path, new_path):
    """The two comparable modification-time values (exact nanoseconds if both sides have them)."""
    a, b = old.get("mtime_ns"), new.get("mtime_ns")
    if _is_int(a) and _is_int(b):
        return a, b
    a, b = old.get("Modification Date"), new.get("Modification Date")
    if a is None or b is None:
        raise ValueError(f"Cannot compare modification times of {new_path!r}: "
                         "records need 'mtime_ns' or 'Modification Date' on both sides")
    return str(a).strip(), str(b).strip()


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _index(records, side):
    """{normalised path: (original path, record)} in input order. Two records for one path is an error."""
    if isinstance(records, Mapping):
        records = records.values()
    indexed = {}
    for position, record in enumerate(records, start=1):
        path = _path_of(record, side, position)
        key = os.path.normcase(os.path.normpath(path))
        if key in indexed:
            raise ValueError(f"{side} snapshot lists {path!r} more than once")
        indexed[key] = (path, record)
    return indexed


def _same(old, new, path, compare):
    if "size" in compare and _size_of(old, "old", path) != _size_of(new, "new", path):
        return False
    if "mtime" in compare:
        a, b = _mtime_token(old, new, path, path)
        if a != b:
            return False
    return True


def _move_signature(record, side, path, compare):
    """What must be equal for a deleted and a new file to count as the same file that moved."""
    digest = record.get("Hash")
    parts = []
    if "size" in compare or digest:
        parts.append(_size_of(record, side, path))
    if digest:
        parts.append(("hash", str(digest)))  # a content hash identifies the file even if it was renamed
        return tuple(parts)
    if "mtime" in compare:
        mtime_ns = record.get("mtime_ns")
        date = record.get("Modification Date")
        parts.append(mtime_ns if _is_int(mtime_ns) else (None if date is None else str(date).strip()))
    parts.append(("name", os.path.normcase(str(record["File Name"]))))
    return tuple(parts)


def compare_snapshots(old: Iterable[Mapping], new: Iterable[Mapping], *, only_diff: bool = False,
                      compare=COMPARE_FIELDS, detect_moves: bool = False):
    """
    Compare an older and a newer list of file records.

    Parameters:
        old, new: iterables of record dicts (see the module text). A dict of records is accepted too
            (its values are used), e.g. what FileStatsCollector.get_existing_csvdata returns.
        only_diff (bool): leave out files tagged Same.
        compare: which properties decide whether a file changed: any of 'size' and 'mtime'.
        detect_moves (bool): a Deleted and a New file that match (same name, size and modification
            time; or the same 'Hash' and size if records carry one) are reported as one Moved file.
            Each file takes part in at most one pairing.

    Returns:
        list of Change: files of the new snapshot in their order, then files that are only in the old one.

    Raises:
        ValueError: for records that lack required keys, invalid sizes, a path listed twice on one side,
            or an unknown compare field.
    """
    compare = (compare,) if isinstance(compare, str) else tuple(compare)
    unknown = set(compare) - set(COMPARE_FIELDS)
    if unknown or not compare:
        raise ValueError(f"compare must be a non-empty selection of {COMPARE_FIELDS}, got {compare!r}")

    old_index = _index(old, "old")
    new_index = _index(new, "new")

    changes = []
    created = []  # positions of New changes
    for key, (path, record) in new_index.items():
        if key in old_index:
            old_record = old_index[key][1]
            tag = TAG_SAME if _same(old_record, record, path, compare) else TAG_MODIFIED
            changes.append(Change(path, tag, old_record, record))
        else:
            created.append(len(changes))
            changes.append(Change(path, TAG_NEW, None, record))
    deleted = []  # positions of Deleted changes
    for key, (path, record) in old_index.items():
        if key not in new_index:
            deleted.append(len(changes))
            changes.append(Change(path, TAG_DELETED, record, None))

    if detect_moves and created and deleted:
        waiting = defaultdict(list)  # signature -> positions of Deleted changes, in order
        for position in deleted:
            change = changes[position]
            waiting[_move_signature(change.old, "old", change.path, compare)].append(position)
        paired_deleted = set()
        for position in created:
            change = changes[position]
            candidates = waiting.get(_move_signature(change.new, "new", change.path, compare))
            if candidates:
                other = candidates.pop(0)
                paired_deleted.add(other)
                changes[position] = Change(change.path, TAG_MOVED, changes[other].old, change.new)
        changes = [c for n, c in enumerate(changes) if n not in paired_deleted]

    if only_diff:
        changes = [c for c in changes if c.tag != TAG_SAME]
    return changes
