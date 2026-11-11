"""
Hash cache: remembers file hashes between runs so unchanged files are not read again.

An entry is trusted only while the file's size and modification time (in nanoseconds) are
exactly what they were when the hash was made. Anything else is hashed again. Entries for
files modified within the last two seconds are never stored (the file could still change
without its timestamp changing).

The cache is a JSON file, by default '<root>/.filer_journal/hash_cache.json'. A missing,
unreadable or outdated cache file simply means a normal full run.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Iterable, Optional, Protocol, runtime_checkable

from Filing.journal import JOURNAL_DIR_NAME

CACHE_FILE_NAME = "hash_cache.json"
CACHE_VERSION = 1
RACY_WINDOW_NS = 2_000_000_000  # do not trust timestamps younger than this


@runtime_checkable
class HashStore(Protocol):
    """
    What DuplicateFinder needs from a hash cache. HashCache is the standard implementation; pass your
    own object (a database, a shared cache, a logging wrapper...) with DuplicateFinder(cache=...).

    Methods may be called from several threads at once (lookup and store), so they must be thread-safe.
    kind is 'p' (hash of the first and last 64 KB) or 'f' (hash of the whole file). A store must only
    return a digest for a file whose size and modification time (in nanoseconds) are exactly the ones
    it was stored with, otherwise it must return None.
    """

    def lookup(self, path: str, size: int, mtime_ns: int, kind: str) -> Optional[str]: ...

    def store(self, path: str, size: int, mtime_ns: int, kind: str, digest: str) -> None: ...

    def prune(self, seen_paths: Iterable[str]) -> None: ...

    def save(self) -> bool: ...

    def clear(self) -> None: ...


def _key(path):
    return os.path.normcase(os.path.abspath(path))


class HashCache:
    """Thread-safe store of {path: size, mtime_ns, partial hash, full hash}."""

    def __init__(self, path):
        self.path = path
        self._entries = {}
        self._lock = threading.Lock()
        self._dirty = False
        self.hits = 0
        self.misses = 0
        self.load()

    @staticmethod
    def default_path(root_folder, cache_dir=None):
        folder = cache_dir if cache_dir else os.path.join(os.path.abspath(root_folder), JOURNAL_DIR_NAME)
        return os.path.join(folder, CACHE_FILE_NAME)

    def __len__(self):
        return len(self._entries)

    def load(self):
        """Read the cache file. Anything wrong with it results in an empty cache, never an error."""
        self._entries = {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("version") == CACHE_VERSION and isinstance(data.get("entries"), dict):
                self._entries = {
                    key: entry for key, entry in data["entries"].items()
                    if isinstance(entry, dict) and isinstance(entry.get("s"), int) and isinstance(entry.get("m"), int)
                }
        except (OSError, ValueError):
            pass
        self._dirty = False

    def lookup(self, path, size, mtime_ns, kind):
        """Cached hash of kind 'p' (partial) or 'f' (full) if the file is unchanged, else None."""
        entry = self._entries.get(_key(path))
        digest = entry.get(kind) if entry and entry["s"] == size and entry["m"] == mtime_ns else None
        with self._lock:
            if digest:
                self.hits += 1
            else:
                self.misses += 1
        return digest or None

    def store(self, path, size, mtime_ns, kind, digest):
        """Remember a hash (unless the file was modified too recently to be trusted)."""
        if mtime_ns > time.time_ns() - RACY_WINDOW_NS:
            return
        key = _key(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry["s"] != size or entry["m"] != mtime_ns:
                entry = {"s": size, "m": mtime_ns}
                self._entries[key] = entry
            entry[kind] = digest
            self._dirty = True

    def prune(self, seen_paths):
        """
        Drop entries for files that no longer exist. Files seen in the current scan are kept
        without a disk check; only unseen entries are checked.
        """
        seen = {_key(p) for p in seen_paths}
        with self._lock:
            for key in [k for k in self._entries if k not in seen]:
                if not os.path.exists(key):
                    del self._entries[key]
                    self._dirty = True

    def save(self):
        """Write the cache atomically if anything changed. Returns True if a file was written."""
        with self._lock:
            if not self._dirty:
                return False
            folder = os.path.dirname(self.path)
            os.makedirs(folder, exist_ok=True)
            temporary = self.path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump({"version": CACHE_VERSION, "entries": self._entries}, f, separators=(",", ":"))
            os.replace(temporary, self.path)
            self._dirty = False
            return True

    def clear(self):
        """Forget everything and delete the cache file."""
        with self._lock:
            self._entries = {}
            self._dirty = False
            try:
                os.remove(self.path)
            except OSError:
                pass
