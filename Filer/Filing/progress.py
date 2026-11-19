"""
Progress reporting.

Pass any function as `progress=` to FileStatsCollector, MediaGatherer, Gatherer or DuplicateFinder:

    def show(stage, done, total):
        print(stage, done, total)          # total is None while it is not known yet

    MediaGatherer(root, progress=show, verbose=False).gather_media()

Stages (also available as constants):
    STAGE_SCAN          'Finding folders'       total unknown until finished
    STAGE_READ          'Reading folders'       folders read so far / all folders
    STAGE_PLAN          'Planning moves'
    STAGE_MOVE          'Moving files'
    STAGE_HASH_PARTIAL  'Quick-checking files'  first/last 64 KB of files with the same size
    STAGE_HASH_FULL     'Fully checking files'  whole-file hash, only when still needed
    STAGE_LINK          'Linking files'

The callback is called at most about 10 times a second per stage, plus once when a stage is
finished (done == total). If it raises an exception, the operation stops with that exception,
so a callback can also be used to cancel.
"""
from __future__ import annotations

import sys
import threading
import time

STAGE_SCAN = "Finding folders"
STAGE_READ = "Reading folders"
STAGE_PLAN = "Planning moves"
STAGE_MOVE = "Moving files"
STAGE_HASH_PARTIAL = "Quick-checking files"
STAGE_HASH_FULL = "Fully checking files"
STAGE_LINK = "Linking files"


class ProgressReporter:
    """Calls a progress callback `callback(stage, done, total)`, throttled and thread-safe."""

    def __init__(self, callback=None, min_interval=0.1):
        if isinstance(callback, ProgressReporter):
            callback = callback.callback
        if callback is not None and not callable(callback):
            raise TypeError("progress must be a function taking (stage, done, total)")
        self.callback = callback
        self.min_interval = min_interval
        self._last_emit = {}
        self._lock = threading.Lock()

    def __reduce__(self):
        # Objects holding a reporter can be pickled (e.g. sent to worker processes); the copy is silent.
        return (ProgressReporter, (None, self.min_interval))

    @property
    def enabled(self):
        return self.callback is not None

    def __call__(self, stage, done, total=None):
        if self.callback is None:
            return
        with self._lock:
            now = time.monotonic()
            finished = total is not None and done >= total
            last = self._last_emit.get(stage)
            if not finished and last is not None and now - last < self.min_interval:
                return
            self._last_emit[stage] = None if finished else now  # a finished stage can start again
            self.callback(stage, done, total)

    def offset(self, base, grand_total):
        """A callback for one part of a bigger job: counts continue from base towards grand_total."""
        def shifted(stage, done, total=None):
            self(stage, base + done, grand_total)
        return shifted


def make_reporter(progress):
    """None -> a disabled reporter; a function -> a reporter calling it; a reporter stays as it is."""
    return progress if isinstance(progress, ProgressReporter) else ProgressReporter(progress)


class ConsoleProgress:
    """
    A ready-made progress display for the terminal (written to stderr so it never mixes with results).
    On a terminal it redraws one line; when output is redirected it prints one line per finished stage.
    """

    def __init__(self, stream=None, width=24):
        self.stream = stream if stream is not None else sys.stderr
        self.width = width
        self._stage = None
        self._last_text = ""
        self._open_line = False
        self._is_tty = bool(getattr(self.stream, "isatty", lambda: False)())

    def _text(self, stage, done, total):
        if total:
            filled = min(self.width, int(self.width * done / total))
            bar = "#" * filled + "-" * (self.width - filled)
            return f"{stage}: [{bar}] {done}/{total} ({100 * done // total}%)"
        return f"{stage}: {done}"

    def __call__(self, stage, done, total=None):
        if stage != self._stage:
            self._end_line()
            self._stage = stage
        text = self._text(stage, done, total)
        self._last_text = text
        finished = bool(total) and done >= total
        if self._is_tty:
            self.stream.write("\r" + text.ljust(79))
            self.stream.flush()
            self._open_line = True
            if finished:
                self._end_line()
        elif finished:
            self.stream.write(text + "\n")
            self.stream.flush()
            self._last_text = ""

    def _end_line(self):
        if self._is_tty and self._open_line:
            self.stream.write("\n")
            self.stream.flush()
        elif not self._is_tty and self._last_text:
            self.stream.write(self._last_text + "\n")  # stage that never reported a total
            self.stream.flush()
        self._open_line = False
        self._last_text = ""

    def close(self):
        """Finish the current line (call when the whole operation is over)."""
        self._end_line()
        self._stage = None
