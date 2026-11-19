__version__ = "1.7.0"

from .filestatser import FileStatsCollector
from .gatherer import MediaGatherer
from .gatherer import Gatherer
from .duplicates import DuplicateFinder, DuplicateGroup
from .filters import FileFilter, parse_size, parse_time
from .compare import compare_snapshots, Change
from .organizer import Organizer
from .journal import MoveJournal, undo_moves
from .cache import HashCache, HashStore
from .ignore import IgnoreRules
from .paths import ensure_inside, is_inside, UnsafePathError
from .cleanup import EmptyFolderFinder, JunkFinder, JUNK_PATTERNS
from .progress import ConsoleProgress, ProgressReporter

format_size = FileStatsCollector.format_size  # 2048 -> '2.00 KB'

__all__ = [
    "IgnoreRules", "ensure_inside", "is_inside", "UnsafePathError", "EmptyFolderFinder", "JunkFinder", "JUNK_PATTERNS",
    "compare_snapshots", "Change", "HashStore", "parse_size", "parse_time", "format_size",
    "FileStatsCollector", "MediaGatherer", "Gatherer",
    "DuplicateFinder", "DuplicateGroup", "FileFilter", "Organizer",
    "MoveJournal", "undo_moves", "HashCache", "ConsoleProgress", "ProgressReporter", "__version__",
]
