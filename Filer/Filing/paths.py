"""
Path safety: make sure a path really lies inside the folder(s) it is supposed to.

    from Filing.paths import ensure_inside, is_inside

    safe = ensure_inside(r"D:\\Photos", r"D:\\Photos\\2026\\a.jpg")        # returns the resolved path
    ensure_inside(r"D:\\Photos", r"D:\\Photos\\..\\Secrets\\a.txt")       # raises UnsafePathError
    is_inside([r"D:\\Photos", r"E:\\Backup"], some_path)                  # True / False

The check is made on resolved paths (os.path.realpath), so '..' parts and symbolic links or
junctions cannot lead out of the folder. On Windows, device names (CON, NUL, COM1...) and
alternate data streams ('file.txt:stream') are refused as well. Nothing is created or changed.
"""
from __future__ import annotations

import os
import re

__all__ = ["UnsafePathError", "PathGuard", "ensure_inside", "is_inside", "check_path_syntax"]


class UnsafePathError(ValueError):
    """A path is not allowed: it leaves the permitted folders or has a form that is not safe to use."""


_DEVICE_NAME = re.compile(r"^(con|prn|aux|nul|conin\$|conout\$|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3])$",
                          re.IGNORECASE)
_SEPARATORS = re.compile(r"[\\/]+")


def check_path_syntax(path, windows=None):
    """
    Refuse paths that are unsafe by form alone. Returns the path as a string.

    Always refused: non-text paths, empty paths and paths containing a NUL character.
    Refused when windows is true (default: when running on Windows): a component that is a device
    name (also with an extension, like 'nul.txt') and a component with ':' after the drive
    (an alternate data stream).
    """
    try:
        text = os.fspath(path)
    except TypeError:
        raise UnsafePathError(f"path must be text, not {type(path).__name__}") from None
    if not isinstance(text, str):
        raise UnsafePathError("path must be text (bytes paths are not accepted)")
    if not text.strip():
        raise UnsafePathError("path is empty")
    if "\x00" in text:
        raise UnsafePathError("path contains a NUL character")
    if windows is None:
        windows = os.name == "nt"
    if windows:
        remainder = text
        if text.startswith("\\\\?\\") or text.startswith("\\\\.\\"):
            remainder = text[4:]
            if remainder[:4].upper() == "UNC\\":
                remainder = remainder[4:]
        if len(remainder) >= 2 and remainder[1] == ":" and remainder[0].isalpha():
            remainder = remainder[2:]  # the drive letter's own colon is fine
        for part in _SEPARATORS.split(remainder):
            if ":" in part:
                raise UnsafePathError(f"alternate data streams are not allowed: {text!r}")
            stem = part.rstrip(" .").split(".")[0].rstrip(" ")
            if stem and _DEVICE_NAME.match(stem):
                raise UnsafePathError(f"device names are not allowed: {text!r}")
    return text


def _resolved(path):
    return os.path.normcase(os.path.realpath(path))


def _roots(roots):
    if isinstance(roots, (str, bytes, os.PathLike)):
        return [roots]
    roots = list(roots)
    if not roots:
        raise UnsafePathError("no folder is allowed")
    return roots


class PathGuard:
    """
    Checks many paths against the same allowed folders. The folders are resolved once, and so is
    every distinct parent folder, which keeps checking thousands of paths cheap.

    guard.check(path) works like ensure_inside. With resolve_name=False the last part of the path is
    not followed if it is a link: the check then answers "may something be created or moved HERE?",
    which is what moving a file needs (a move acts on the link itself, never on its target).
    """

    def __init__(self, roots, windows=None):
        self.allowed = _roots(roots)
        self.windows = windows
        self._resolved_roots = []
        for root in self.allowed:
            self._resolved_roots.append(_resolved(check_path_syntax(root, windows=False)))  # chosen by the caller
        self._base = os.fspath(self.allowed[0])
        self._parents = {}

    def _parent(self, folder):
        key = os.path.normcase(folder)
        found = self._parents.get(key)
        if found is None:
            found = self._parents[key] = os.path.realpath(folder)
        return found

    def check(self, path, *, allow_root=False, resolve_name=True):
        text = check_path_syntax(path, windows=self.windows)
        candidate = text if os.path.isabs(text) else os.path.join(self._base, text)
        parent, name = os.path.split(candidate)
        if resolve_name or name in ("", ".", ".."):
            resolved = os.path.realpath(candidate)
        else:
            resolved = os.path.join(self._parent(parent), name)
        key = os.path.normcase(resolved)
        for root in self._resolved_roots:
            if key == root:
                if allow_root:
                    return resolved
                continue
            try:
                if os.path.commonpath([root, key]) == root:
                    return resolved
            except ValueError:  # different drives
                continue
        raise UnsafePathError(f"{text!r} is not inside the allowed folder(s): {', '.join(map(str, self.allowed))}")


def ensure_inside(roots, path, *, allow_root=False, windows=None):
    """
    Return the resolved path if it lies inside one of the allowed folders; raise UnsafePathError if not.

    Parameters:
        roots: a folder or a list of folders the path may be in.
        path: the path to check. A relative path is taken relative to the first folder.
        allow_root (bool): also accept the folder itself (default: only paths below it).
        windows (bool): force or skip the Windows-only form checks (default: auto).

    The path does not have to exist. Links are resolved first, so a link inside a folder that points
    outside of it is refused. To check many paths, use PathGuard.
    """
    return PathGuard(roots, windows=windows).check(path, allow_root=allow_root)


def is_inside(roots, path, *, allow_root=False, windows=None):
    """True if ensure_inside would accept the path."""
    try:
        ensure_inside(roots, path, allow_root=allow_root, windows=windows)
    except UnsafePathError:
        return False
    return True
