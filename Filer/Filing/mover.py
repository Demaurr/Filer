"""
Shared building blocks for moving files: conflict-free destination names, move planning
(usable for dry runs) and execution with an optional undo journal.
"""
from __future__ import annotations

import os
import re
import shutil

from Filing.progress import STAGE_PLAN, STAGE_MOVE

_NUMBER_SUFFIX = re.compile(r"^(.*?)\(\d+\)$")


def remove_number_suffix(basename):
    """Remove a trailing '(number)' from a basename if present: 'song(2)' -> 'song'."""
    match = _NUMBER_SUFFIX.match(basename)
    return match.group(1) if match else basename


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def unique_destination(path, reserved=None):
    """
    Return path, or 'name(1).ext', 'name(2).ext'... if something already exists there.

    Parameters:
        reserved (set): Optional set of normalised paths that are planned but not on disk yet
            (used by dry runs and when planning several files into the same folder).
    """
    def taken(candidate):
        return os.path.exists(candidate) or (reserved is not None and _key(candidate) in reserved)

    if not taken(path):
        return path
    base, extension = os.path.splitext(path)
    base = remove_number_suffix(base)
    counter = 1
    candidate = path
    while taken(candidate):
        candidate = f"{base}({counter}){extension}"
        counter += 1
    return candidate


def plan_moves(pairs, reserved=None, progress=None):
    """
    Turn (source, wanted_destination) pairs into a conflict-free list of (source, destination).
    Files already at their destination are left out. Nothing on disk is touched.
    progress: optional function(stage, done, total).
    """
    reserved = set() if reserved is None else reserved
    plan = []
    pairs = list(pairs)
    for number, (source, destination) in enumerate(pairs, start=1):
        if progress is not None:
            progress(STAGE_PLAN, number, len(pairs))
        if _key(source) == _key(destination):
            continue
        destination = unique_destination(destination, reserved)
        reserved.add(_key(destination))
        plan.append((source, destination))
    return plan


def execute_moves(plan, journal=None, verbose=True, progress=None):
    """
    Perform a planned list of (source, destination) moves.

    A failing file is reported and skipped; the rest still move. If a journal is given, every
    move (and every folder created) is recorded and the journal is saved, even after errors.
    progress: optional function(stage, done, total), called after every file.

    Returns:
        tuple: (moved, failed) where moved is {source: final_destination} and
            failed is a list of (source, error message).
    """
    moved, failed = {}, []
    try:
        for number, (source, destination) in enumerate(plan, start=1):
            try:
                destination = unique_destination(destination)  # something may have appeared since planning
                folder = os.path.dirname(destination)
                if journal is not None:
                    journal.make_dirs(folder)
                else:
                    os.makedirs(folder, exist_ok=True)
                shutil.move(source, destination)
                moved[source] = destination
                if journal is not None:
                    journal.record_move(source, destination)
                if verbose:
                    print(f"\tMoved: {os.path.basename(source)}")
            except Exception as e:
                failed.append((source, str(e)))
                print(f"Error moving '{source}': {e}")
            if progress is not None:
                progress(STAGE_MOVE, number, len(plan))
    finally:
        if journal is not None:
            journal.save()
    return moved, failed
