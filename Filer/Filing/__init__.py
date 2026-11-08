__version__ = "1.6.0"

from .filestatser import FileStatsCollector
from .gatherer import MediaGatherer
from .gatherer import Gatherer
from .duplicates import DuplicateFinder, DuplicateGroup
from .filters import FileFilter
from .organizer import Organizer
from .journal import MoveJournal, undo_moves
from .cache import HashCache
from .progress import ConsoleProgress, ProgressReporter

__all__ = [
    "FileStatsCollector", "MediaGatherer", "Gatherer",
    "DuplicateFinder", "DuplicateGroup", "FileFilter", "Organizer",
    "MoveJournal", "undo_moves", "HashCache", "ConsoleProgress", "ProgressReporter", "__version__",
]
