"""
Gitignore-style ignore rules, so scans never even enter folders you do not care about.

    rules = IgnoreRules(["*.log", "build/", "/cache", "!keep.log", "docs/**/draft*"])
    rules.is_ignored("app/debug.log")            # True
    rules = IgnoreRules.from_file("D:/Photos/.filerignore")

Rule syntax (the common part of .gitignore):
    # comment            blank lines and lines starting with # are ignored (write \\# for a literal #)
    name                 matches a file or folder called name at any depth
    *.log   data?.csv    * matches anything except /, ? one character, [abc] one of a set
    dir/                 a trailing / matches folders only
    /top                 a leading / (or any / inside the pattern) anchors it to the scanned folder
    docs/*.md            anchored: only docs/x.md directly below the scanned folder
    **/cache  a/**/b  logs/**    ** matches any number of folders
    !keep.log            re-includes what an earlier rule ignored (the LAST matching rule wins)
    \\!name               a literal ! at the start

As in git, a file inside an ignored folder is ignored whatever the later rules say, because the
folder is never entered. Matching is case-insensitive on Windows. Only text in the rules is used:
nothing is executed and the disk is never touched. Paths are relative to the scanned folder.
"""
from __future__ import annotations

import itertools
import os
import re
from typing import Iterable, List, Optional

IGNORE_FILE_NAME = ".filerignore"
MAX_IGNORE_FILE_BYTES = 1_000_000
MAX_RULES = 20_000
MAX_PATTERN_LENGTH = 1_000

__all__ = ["IgnoreRules", "IGNORE_FILE_NAME", "load_ignore_rules"]


_group_numbers = itertools.count()


def _tokens(pattern: str):
    """Split a glob into tokens: ('star',), ('dirs',) for '**/', ('rest',) for a trailing '**', or ('atom', regex)."""
    out = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            double = j - i >= 2
            before_ok = i == 0 or pattern[i - 1] == "/"
            after_ok = j == n or pattern[j] == "/"
            if double and before_ok and after_ok:
                if j == n:
                    out.append(("rest",))  # trailing /** : everything below
                else:
                    if not (out and out[-1] == ("dirs",)):  # '**/**/' is the same as '**/'
                        out.append(("dirs",))
                    j += 1  # swallow the slash
            elif not (out and out[-1] == ("star",)):
                out.append(("star",))  # '*', '**' inside a name, '***' ... all mean "anything but /"
            i = j
            continue
        if c == "?":
            out.append(("atom", "[^/]"))
        elif c == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(("atom", re.escape(c)))  # no closing ]: a literal [
            else:
                body = pattern[i + 1:j]
                negate = body[:1] in ("!", "^")
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("]", "\\]")
                out.append(("atom", "[" + ("^" if negate else "") + body + "]"))
                i = j
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(("atom", re.escape(pattern[i])))
        else:
            out.append(("atom", re.escape(c)))
        i += 1
    return out


def _glob_to_regex(pattern: str) -> str:
    """
    Regex source (no anchors) for a glob; / is the only separator.

    Every '*' followed by more pattern is written as an atomic "find the first place the rest of the
    chunk matches, then stay there" step (the technique Python's own fnmatch uses). That is exactly right
    for globs and keeps matching fast; a plain '.*x.*y.*z' form can take minutes on a hostile pattern.
    """
    tokens = _tokens(pattern)
    out: List[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        kind = token[0]
        if kind == "atom":
            out.append(token[1])
        elif kind == "dirs":
            out.append("(?:.*/)?")
        elif kind == "rest":
            out.append(".*")
        else:  # star
            j = i + 1
            while j < len(tokens) and tokens[j][0] == "atom":
                j += 1
            chunk = "".join(t[1] for t in tokens[i + 1:j])
            if chunk:
                name = f"g{next(_group_numbers)}"
                out.append(f"(?=(?P<{name}>[^/]*?{chunk}))(?P={name})")
                i = j
                continue
            out.append("[^/]*")
        i += 1
    return "".join(out)


class _Rule:
    __slots__ = ("pattern", "negate", "dir_only", "source")

    def __init__(self, pattern, negate, dir_only, source):
        self.pattern, self.negate, self.dir_only, self.source = pattern, negate, dir_only, source


def _parse(line: str) -> Optional[_Rule]:
    line = line.rstrip("\r\n")
    if not line.strip() or line.startswith("#"):
        return None
    # trailing spaces are dropped unless escaped with a backslash
    while line.endswith(" ") and not line.endswith("\\ "):
        line = line[:-1]
    if not line:
        return None
    if len(line) > MAX_PATTERN_LENGTH:
        raise ValueError(f"ignore pattern is longer than {MAX_PATTERN_LENGTH} characters")
    negate = False
    if line.startswith("!"):
        negate, line = True, line[1:]
    elif line.startswith("\\!") or line.startswith("\\#"):
        line = line[1:]
    original = line
    dir_only = line.endswith("/") and not line.endswith("\\/")
    if dir_only:
        line = line.rstrip("/")
    anchored = "/" in line
    line = line.lstrip("/")
    if not line:
        return None
    body = _glob_to_regex(line)
    source = body if (anchored or body.startswith("(?:.*/)?")) else "(?:.*/)?" + body
    return _Rule(original, negate, dir_only, source)


class IgnoreRules:
    """
    A list of ignore rules.

    Parameters:
        patterns: an iterable of rule lines (or one multi-line string).
        case_sensitive (bool): default: False on Windows, True elsewhere.

    Raises:
        ValueError: more than MAX_RULES rules, or a rule longer than MAX_PATTERN_LENGTH characters.
    """

    def __init__(self, patterns: Iterable[str] = (), case_sensitive: Optional[bool] = None):
        self.case_sensitive = (os.name != "nt") if case_sensitive is None else bool(case_sensitive)
        self._flags = 0 if self.case_sensitive else re.IGNORECASE
        self._rules: List[_Rule] = []
        self._compiled = None
        self.add(patterns)

    # ---- building -----------------------------------------------------------------------

    @classmethod
    def from_file(cls, path, case_sensitive=None):
        """
        Read rules from a file (UTF-8; undecodable bytes are replaced). Files larger than
        MAX_IGNORE_FILE_BYTES are refused. Raises OSError if the file cannot be read.
        """
        with open(path, "rb") as f:
            data = f.read(MAX_IGNORE_FILE_BYTES + 1)
        if len(data) > MAX_IGNORE_FILE_BYTES:
            raise ValueError(f"{path} is larger than {MAX_IGNORE_FILE_BYTES} bytes; not an ignore file?")
        return cls(data.decode("utf-8-sig", errors="replace").splitlines(), case_sensitive=case_sensitive)

    def add(self, patterns):
        """Add rule lines (a string with several lines, or an iterable of lines)."""
        if isinstance(patterns, str):
            patterns = patterns.splitlines()
        for line in patterns:
            if not isinstance(line, str):
                raise TypeError("ignore patterns must be text")
            rule = _parse(line)
            if rule is None:
                continue
            if len(self._rules) >= MAX_RULES:
                raise ValueError(f"more than {MAX_RULES} ignore rules")
            self._rules.append(rule)
        self._compiled = None
        return self

    @property
    def patterns(self) -> List[str]:
        """The active rules as written (comments and blank lines are gone)."""
        return [("!" if r.negate else "") + r.pattern for r in self._rules]

    def __len__(self):
        return len(self._rules)

    def __bool__(self):
        return bool(self._rules)

    def __repr__(self):
        return f"IgnoreRules({self.patterns!r})"

    # ---- matching -----------------------------------------------------------------------

    def _compile(self):
        def alternation(rules):
            if not rules:
                return None
            return re.compile("^(?:" + "|".join(f"(?:{r.source})" for r in rules) + ")$", self._flags)

        if not any(r.negate for r in self._rules):
            # fast path: no re-inclusion, so one combined pattern per kind decides
            self._compiled = ("simple", alternation(self._rules), alternation([r for r in self._rules if not r.dir_only]))
        else:
            self._compiled = ("ordered", [(re.compile("^(?:" + r.source + ")$", self._flags), r.negate, r.dir_only)
                                          for r in self._rules])
        return self._compiled

    def dir_ignored(self, relpath: str) -> bool:
        """Is this folder (path relative to the scanned folder, / separated) ignored? Ancestors are not looked at."""
        return self._decide(relpath, True)

    def file_ignored(self, relpath: str) -> bool:
        """Is this file ignored by the rules for files? Ancestor folders are not looked at (see is_ignored)."""
        return self._decide(relpath, False)

    def _decide(self, relpath, is_dir):
        if not self._rules:
            return False
        compiled = self._compiled or self._compile()
        if compiled[0] == "simple":
            regex = compiled[1] if is_dir else compiled[2]
            return bool(regex and regex.match(relpath))
        ignored = False
        for regex, negate, dir_only in compiled[1]:
            if dir_only and not is_dir:
                continue
            if regex.match(relpath):
                ignored = not negate
        return ignored

    def is_ignored(self, relpath: str, is_dir: bool = False) -> bool:
        """
        Full answer for a path relative to the scanned folder: ignored if the path itself is ignored or
        any folder above it is. Separators may be / or \\; a leading ./ is removed. Paths that leave the
        folder ('..') or are absolute are never ignored (they are not in the scanned folder).
        """
        text = str(relpath).replace("\\", "/")
        if re.match(r"^[A-Za-z]:", text):
            return False
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if not parts or ".." in parts:
            return False
        text = "/".join(parts)
        for depth in range(1, len(parts)):
            if self.dir_ignored("/".join(parts[:depth])):
                return True
        return self._decide(text, is_dir)


def load_ignore_rules(root_folder, exclude=None, ignore_file=True):
    """
    The rules a scan of root_folder should use, or None if there are none.

    ignore_file: True reads '<root_folder>/.filerignore' if it exists (a damaged one is reported and
    skipped); a path reads that file (ValueError if it cannot be read); False/None reads none.
    exclude: extra rule lines (a list, or one string).
    """
    rules = IgnoreRules()
    if ignore_file:
        explicit = ignore_file is not True
        path = os.fspath(ignore_file) if explicit else os.path.join(root_folder, IGNORE_FILE_NAME)
        if explicit or os.path.isfile(path):
            try:
                rules.add(IgnoreRules.from_file(path).patterns)
            except (OSError, ValueError) as e:
                if explicit:
                    raise ValueError(f"Cannot use ignore file {path}: {e}") from None
                print(f"Warning: ignoring unreadable {path}: {e}")
    if exclude:
        rules.add([exclude] if isinstance(exclude, str) else list(exclude))
    return rules if rules else None
