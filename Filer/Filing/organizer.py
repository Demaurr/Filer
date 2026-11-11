"""
Organise files into subfolders by rule.

    Organizer("type")                      -> Images/photo.jpg
    Organizer("date")                      -> 2026/11/photo.jpg
    Organizer("size")                      -> Small/photo.jpg
    Organizer("extension")                 -> jpg/photo.jpg
    Organizer("{type}/{year}/{month}")     -> Images/2026/11/photo.jpg
    Organizer("{type}/{year}/{name}")      -> Images/2026/photo.jpg   (pattern ends in the file name)

Pattern fields:
    {type}      category: Images, Videos, Audio, Documents, Archives, Code, Other
    {extname}   extension without the dot, lower case ('jpg'; 'noext' if there is none)
    {ext}       extension with the dot ('.jpg')
    {year} {month} {day}   from the modification date ('2026', '11', '09')
    {size}      size bucket: Tiny (<100 KB), Small (<10 MB), Medium (<100 MB), Large (<1 GB), Huge
    {source}    name of the folder the file came from
    {name}      file name without extension
    {filename}  full file name

A pattern describes the folder, and the file keeps its name. If the last part of the pattern
contains {name}, {filename} or {ext} it describes the whole path including the file name
(the original extension is added automatically if the pattern leaves it out).
"""
from __future__ import annotations

import os
import re
import string

CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp", ".svg", ".ico", ".heic", ".raw"},
    "Videos": {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".mpg", ".mpeg", ".3gp"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt", ".pptx",
                  ".md", ".epub", ".mobi"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "Code": {".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".html", ".css", ".json",
             ".xml", ".yaml", ".yml", ".sh", ".bat", ".ps1", ".sql"},
}
_CATEGORY_BY_EXTENSION = {ext: category for category, exts in CATEGORIES.items() for ext in exts}

PRESETS = {
    "type": "{type}",
    "date": "{year}/{month}",
    "size": "{size}",
    "extension": "{extname}",
}

FIELDS = {"type", "extname", "ext", "year", "month", "day", "size", "source", "name", "filename"}
_NAME_FIELDS = {"name", "filename", "ext"}
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def categorize(extension):
    """Category folder name for an extension such as '.jpg' (case-insensitive)."""
    return _CATEGORY_BY_EXTENSION.get(extension.lower(), "Other")


def size_bucket(size):
    """Size bucket name for a size in bytes."""
    if size < 100 * 1024:
        return "Tiny"
    if size < 10 * 1024 ** 2:
        return "Small"
    if size < 100 * 1024 ** 2:
        return "Medium"
    if size < 1024 ** 3:
        return "Large"
    return "Huge"


def _clean(part):
    part = _ILLEGAL.sub("_", str(part)).strip(" .")
    return part if part else "_"


def _safe_filename(name):
    """
    The file name as a single, harmless path component. Real file names are returned unchanged; a name
    from outside data (a snapshot, a CSV) that holds a separator, a drive ('C:evil') or is '.'/'..' is
    made safe, so joining it can never leave the destination folder.
    """
    name = str(name)
    separators = os.sep + (os.altsep or "") + "\x00"
    if any(ch in name for ch in separators):
        name = "".join("_" if ch in separators else ch for ch in name)
    if os.path.splitdrive(name)[0]:
        name = name.replace(":", "_")
    if name in (".", ".."):
        name = "_"
    return name


class Organizer:
    """
    Decides where a file goes below the destination folder.

    Parameters:
        rule: a preset name ('type', 'date', 'size', 'extension'), a pattern string,
            or a function taking the file's stats dict and returning a relative folder path.
        date_field (str): 'modified' (default) or 'created', the date used by {year}/{month}/{day}.
    """

    def __init__(self, rule, date_field="modified"):
        if date_field not in ("modified", "created"):
            raise ValueError("date_field must be 'modified' or 'created'")
        self.date_key = "Modification Date" if date_field == "modified" else "Creation Date"
        self._function = None
        if callable(rule):
            self._function = rule
            self.pattern = None
            return
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("rule must be a preset name, a pattern string or a function")
        self.pattern = PRESETS.get(rule.strip().lower(), rule.strip())
        self._segments = [seg for seg in re.split(r"[\\/]+", self.pattern) if seg]
        if not self._segments:
            raise ValueError(f"Invalid pattern: {rule!r}")
        used = set()
        for segment in self._segments:
            try:
                for _, field, _, _ in string.Formatter().parse(segment):
                    if field is not None:
                        used.add(field)
            except ValueError as e:
                raise ValueError(f"Invalid pattern {rule!r}: {e}") from None
        unknown = used - FIELDS
        if unknown:
            raise ValueError(f"Unknown field(s) {sorted(unknown)} in pattern {rule!r}; use {sorted(FIELDS)}")
        last = self._segments[-1]
        self._full_path = any("{" + f + "}" in last for f in _NAME_FIELDS)
        self._adds_extension = self._full_path and "{filename}" not in last and "{ext}" not in last

    def _fields(self, stat):
        filename = stat["File Name"]
        name, extension = os.path.splitext(filename)
        date_text = stat.get(self.date_key) or "0000-00-00 00:00:00"
        return {
            "type": categorize(extension),
            "extname": extension.lower().lstrip(".") or "noext",
            "ext": extension,
            "year": date_text[0:4],
            "month": date_text[5:7],
            "day": date_text[8:10],
            "size": size_bucket(stat.get("File Size (Bytes)", 0)),
            "source": os.path.basename(os.path.normpath(stat.get("Source Folder", ""))),
            "name": name,
            "filename": filename,
        }

    def relative_path(self, stat):
        """
        Relative destination path (folders plus file name) for a file stats dict.
        Every part is made safe for file systems, so the result can never leave the destination.
        """
        filename = _safe_filename(stat["File Name"])
        if self._function is not None:
            parts = [_clean(p) for p in re.split(r"[\\/]+", str(self._function(stat))) if p]
            return os.path.join(*parts, filename) if parts else filename
        fields = self._fields(stat)
        parts = [_clean(segment.format(**fields)) for segment in self._segments]
        if not self._full_path:
            return os.path.join(*parts, filename)
        if self._adds_extension and not parts[-1].lower().endswith(fields["ext"].lower()):
            parts[-1] += fields["ext"]
        return os.path.join(*parts)

    def destination(self, stat, destination_folder):
        """Full destination path of a file below destination_folder."""
        return os.path.join(destination_folder, self.relative_path(stat))


def make_organizer(rule, date_field="modified"):
    """None stays None; an Organizer is returned as is; anything else becomes an Organizer."""
    if rule is None or isinstance(rule, Organizer):
        return rule
    return Organizer(rule, date_field=date_field)
