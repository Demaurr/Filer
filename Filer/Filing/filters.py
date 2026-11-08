"""
File filters applied while scanning, so unwanted files are never stored (and name-based
filters never even need a stat call).

    FileFilter(min_size="10MB", modified_after="2026-01-01", name_glob=["*.jpg", "IMG_*"])
"""
from __future__ import annotations

import fnmatch
import re
from datetime import date, datetime

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4, "PB": 1024 ** 5}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")


def parse_size(value):
    """
    Convert a size to bytes. Accepts None, a number of bytes, or a string such as
    '500', '10KB', '1.5 MB' (units are powers of 1024, like format_size).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("size must be a number or a string like '10MB'")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"size cannot be negative: {value!r}")
        return int(value)
    match = _SIZE_RE.match(str(value))
    if not match:
        raise ValueError(f"Invalid size: {value!r} (expected e.g. 500, '10KB', '1.5 MB')")
    number, unit = match.groups()
    unit = (unit or "B").upper()
    if unit in ("K", "M", "G", "T", "P"):
        unit += "B"
    if unit not in _SIZE_UNITS:
        raise ValueError(f"Invalid size unit in {value!r}; use one of {', '.join(_SIZE_UNITS)}")
    return int(float(number) * _SIZE_UNITS[unit])


def parse_time(value):
    """
    Convert a point in time to a POSIX timestamp. Accepts None, a datetime/date (local time),
    a number (timestamp), or an ISO string such as '2026-11-09' or '2026-11-09 14:30:00'.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise TypeError("time must be a datetime, date, timestamp or ISO string")
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).strip()).timestamp()
    except ValueError:
        raise ValueError(f"Invalid date/time: {value!r} (expected e.g. '2026-11-09' or '2026-11-09 14:30:00')") from None


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or hasattr(value, "pattern"):
        return [value]
    return list(value)


class FileFilter:
    """
    Size, modification-time and name filters. All given filters must pass (AND).

    Parameters:
        min_size, max_size: inclusive size limits (bytes, or strings like '10MB').
        modified_after: keep files modified at or after this time (inclusive).
        modified_before: keep files modified before this time (exclusive).
        name_glob: a glob or list of globs (e.g. '*.jpg'); a file matches if any matches. Case-insensitive.
        name_regex: a regex (str or compiled) or list of them, searched in the file name;
            a file matches if any matches. Case-sensitive unless you use (?i) or re.IGNORECASE.
    """

    def __init__(self, min_size=None, max_size=None, modified_after=None, modified_before=None,
                 name_glob=None, name_regex=None):
        self.min_size = parse_size(min_size)
        self.max_size = parse_size(max_size)
        self.modified_after = parse_time(modified_after)
        self.modified_before = parse_time(modified_before)
        if None not in (self.min_size, self.max_size) and self.min_size > self.max_size:
            raise ValueError("min_size cannot be larger than max_size")
        if None not in (self.modified_after, self.modified_before) and self.modified_after >= self.modified_before:
            raise ValueError("modified_after must be earlier than modified_before")

        self._globs = [g.lower() for g in _as_list(name_glob)]
        self._regexes = [re.compile(r) for r in _as_list(name_regex)]
        self._has_name_filter = bool(self._globs or self._regexes)
        self._has_stat_filter = any(v is not None for v in
                                    (self.min_size, self.max_size, self.modified_after, self.modified_before))
        self.active = self._has_name_filter or self._has_stat_filter

    def name_ok(self, filename):
        """Check the name filters (needs no stat call)."""
        if self._globs:
            lowered = filename.lower()
            if not any(fnmatch.fnmatchcase(lowered, glob) for glob in self._globs):
                return False
        if self._regexes and not any(regex.search(filename) for regex in self._regexes):
            return False
        return True

    def stat_ok(self, size, mtime):
        """Check the size and modification-time filters."""
        if self.min_size is not None and size < self.min_size:
            return False
        if self.max_size is not None and size > self.max_size:
            return False
        if self.modified_after is not None and mtime < self.modified_after:
            return False
        if self.modified_before is not None and mtime >= self.modified_before:
            return False
        return True

    def __repr__(self):
        return (f"FileFilter(min_size={self.min_size}, max_size={self.max_size}, "
                f"modified_after={self.modified_after}, modified_before={self.modified_before}, "
                f"name_glob={self._globs}, name_regex={[r.pattern for r in self._regexes]})")
