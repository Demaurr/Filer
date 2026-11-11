"""
Command-line interface:  filer <command> FOLDER [options]   (or: python -m Filing <command> ...)

    filer stats FOLDER            statistics and reports
    filer move FOLDER             gather / organise files (with --dry-run to preview)
    filer undo FOLDER             undo the last move
    filer dupes FOLDER            find duplicate files (and move or link the extra copies)

Exit codes: 0 success, 1 error (or a move/undo that was only partly done), 2 bad usage, 130 interrupted.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys

from Filing import __version__
from Filing.cleanup import EmptyFolderFinder, JunkFinder, JUNK_KINDS, JUNK_PATTERNS
from Filing.duplicates import DuplicateFinder, KEEP_CHOICES
from Filing.filestatser import FileStatsCollector
from Filing.gatherer import MediaGatherer
from Filing.journal import JOURNAL_DIR_NAME, find_latest_journal, list_journals, load_journal, undo_moves
from Filing import paths
from Filing.progress import ConsoleProgress

PLAN_LINES_SHOWN = 20


class CliError(Exception):
    """A problem to report to the user as 'filer: error: ...' (exit code 1)."""


# ------------------------------------------------------------------ option groups

def _common_parent():
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("output")
    group.add_argument("-q", "--quiet", action="store_true", help="only print results and errors (no progress)")
    group.add_argument("--no-progress", action="store_true", help="do not show a progress display")
    group.add_argument("--json", action="store_true",
                       help="print the result as JSON on stdout (messages and progress go to stderr)")
    return parent


def _add_ignore_options(group):
    group.add_argument("--exclude", action="append", metavar="PATTERN",
                       help="leave out files/folders matching this .gitignore-style pattern, e.g. '*.tmp' or 'build/' "
                            "(repeat for more; matching folders are never entered)")
    group.add_argument("--ignore-file", metavar="FILE",
                       help="read exclude patterns from FILE instead of FOLDER/.filerignore")
    group.add_argument("--no-ignore-file", action="store_true", help="do not read FOLDER/.filerignore")


def _tidy_parent():
    """Options for commands that look at folders and files without the size/date/name filters."""
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("which folders")
    group.add_argument("--skip", action="append", metavar="NAME",
                       help="skip folders with this exact name, or this path (repeat for more)")
    _add_ignore_options(group)
    return parent


def _scan_parent(files=True):
    parent = argparse.ArgumentParser(add_help=False)
    group = parent.add_argument_group("which files")
    if files:
        group.add_argument("--all-files", action="store_true", help="all files, not just media files")
        group.add_argument("--ext", action="append", metavar="EXT",
                           help="only these extensions, e.g. --ext .jpg --ext .png or --ext .jpg,.png "
                                "(default: common media files)")
    group.add_argument("--skip", action="append", metavar="NAME",
                       help="skip folders with this exact name, or this path (repeat for more)")
    group.add_argument("--max-size", metavar="SIZE", help="largest size to include, e.g. 500MB")
    group.add_argument("--modified-after", metavar="DATE", help="only files modified on/after this date, e.g. 2026-01-31")
    group.add_argument("--modified-before", metavar="DATE", help="only files modified before this date")
    group.add_argument("--glob", action="append", metavar="PATTERN", help="only file names matching this pattern, e.g. '*.jpg'")
    group.add_argument("--regex", action="append", metavar="PATTERN", help="only file names matching this regular expression")
    _add_ignore_options(group)
    return parent


def build_parser():
    parser = argparse.ArgumentParser(
        prog="filer",
        description="Scan, report on, move, organise and de-duplicate files. Moves can be previewed (--dry-run) and undone.",
        epilog="examples:\n"
               "  filer stats D:\\Photos --all-files --csv report.csv\n"
               "  filer move D:\\Downloads --organize-by type --dry-run\n"
               "  filer move D:\\Downloads --organize-by \"{type}/{year}/{month}\" --yes\n"
               "  filer undo D:\\Downloads\n"
               "  filer dupes D:\\Photos --move --keep oldest\n"
               "  filer empty D:\\Photos --remove --dry-run\n"
               "  filer junk D:\\Photos --move --dry-run\n"
               "  filer stats D:\\Photos --exclude \"*.tmp\" --exclude \"build/\"",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"filer {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="command")
    commands.required = True
    common = _common_parent()

    # ---- stats
    stats = commands.add_parser("stats", parents=[common, _scan_parent()], help="statistics and reports",
                                description="Scan FOLDER and print a summary; optionally write reports.")
    stats.add_argument("folder")
    stats.add_argument("--min-size", metavar="SIZE", help="smallest size to include, e.g. 10MB")
    stats.add_argument("--types", action="store_true", help="also print the file type distribution")
    stats.add_argument("--sizes", action="store_true", help="also print the file size distribution")
    stats.add_argument("--folders", action="store_true", help="also print the size of every folder")
    stats.add_argument("--csv", nargs="?", const="", metavar="PATH", help="write a CSV report (default location: FOLDER)")
    stats.add_argument("--html", nargs="?", const="", metavar="PATH", help="write an HTML report of every file")
    stats.add_argument("--summary-html", nargs="?", const="", metavar="PATH", help="write an HTML summary report")
    stats.add_argument("--diff", metavar="PREVIOUS_CSV",
                       help="compare with an earlier CSV report; writes a new difference CSV in FOLDER "
                            "tagging files New/Modified/Same/Deleted")
    stats.add_argument("--only-diff", action="store_true", help="with --diff: leave out unchanged files")
    stats.set_defaults(func=cmd_stats)

    # ---- move
    move = commands.add_parser("move", parents=[common, _scan_parent()], help="gather or organise files",
                               description="Move files from FOLDER (and its subfolders) into one destination folder.")
    move.add_argument("folder")
    move.add_argument("--min-size", metavar="SIZE", help="smallest size to include, e.g. 10MB")
    move.add_argument("-d", "--destination", metavar="DIR", help="where files go (default: FOLDER/media)")
    move.add_argument("--organize-by", metavar="RULE",
                      help="sort into subfolders: type, date, size, extension, or a pattern like '{type}/{year}/{month}'")
    move.add_argument("--date-field", choices=("modified", "created"), default="modified",
                      help="date used by --organize-by (default: modified)")
    move.add_argument("--dry-run", action="store_true", help="show what would move; change nothing")
    move.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    move.add_argument("--batch-size", type=int, default=15, help="files per batch when moving many files (default 15)")
    move.add_argument("--delay", type=float, default=0.1, help="seconds to pause between batches (default 0.1)")
    move.add_argument("--no-journal", action="store_true", help="do not record the move (it can then not be undone)")
    move.add_argument("--journal-dir", metavar="DIR", help=f"where to keep the undo journal (default: FOLDER/{JOURNAL_DIR_NAME})")
    move.set_defaults(func=cmd_move)

    # ---- undo
    undo = commands.add_parser("undo", parents=[common], help="undo the last move",
                               description="Move the files of the last move (or of --journal) back where they came from. "
                                           "Nothing is overwritten.")
    undo.add_argument("folder")
    undo.add_argument("--journal", metavar="FILE", help="undo this journal instead of the newest one")
    undo.add_argument("--journal-dir", metavar="DIR", help="where the journals are kept")
    undo.add_argument("--list", action="store_true", help="list the journals instead of undoing")
    undo.add_argument("--dry-run", action="store_true", help="show what would be restored; change nothing")
    undo.add_argument("--allow-folder", metavar="DIR", action="append", default=[],
                      help="also allow files to be restored from DIR (needed when the move went to a destination "
                           "outside FOLDER); can be repeated")
    undo.set_defaults(func=cmd_undo)

    # ---- dupes
    dupes = commands.add_parser("dupes", parents=[common, _scan_parent(files=False)], help="find duplicate files",
                                description="Find files with identical content. Nothing is ever deleted.")
    dupes.add_argument("folder")
    dupes.add_argument("--min-size", metavar="SIZE", default="1", help="ignore files smaller than this (default: 1 byte)")
    dupes.add_argument("--keep", choices=KEEP_CHOICES, default="oldest", help="which copy stays (default: oldest)")
    dupes.add_argument("--top", type=int, default=10, help="how many groups to list (default 10)")
    dupes.add_argument("--csv", nargs="?", const="", metavar="PATH", help="write a CSV report of all duplicates")
    action = dupes.add_mutually_exclusive_group()
    action.add_argument("--move", nargs="?", const="", metavar="DIR",
                        help="move the extra copies to DIR (default: FOLDER/Duplicates); can be undone")
    action.add_argument("--link", action="store_true",
                        help="replace the extra copies with hard links (CANNOT be undone)")
    dupes.add_argument("--no-verify", action="store_true", help="with --link: skip the byte-for-byte check before linking")
    dupes.add_argument("--dry-run", action="store_true", help="with --move/--link: show what would happen; change nothing")
    dupes.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    dupes.add_argument("--no-cache", action="store_true", help="do not remember hashes between runs")
    dupes.add_argument("--clear-cache", action="store_true", help="forget remembered hashes first")
    dupes.set_defaults(func=cmd_dupes)

    # ---- empty
    empty = commands.add_parser("empty", parents=[common, _tidy_parent()], help="find (and remove) empty folders",
                                description="List folders that contain no files (a folder holding only empty folders "
                                            "counts). Nothing changes unless you add --remove; no file is ever deleted.")
    empty.add_argument("folder")
    empty.add_argument("--remove", action="store_true",
                       help="remove the empty folders (deepest first); recorded so 'filer undo' can bring them back")
    empty.add_argument("--dry-run", action="store_true", help="with --remove: show what would be removed; change nothing")
    empty.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    empty.add_argument("--no-journal", action="store_true", help="do not record the removal (it can then not be undone)")
    empty.add_argument("--journal-dir", metavar="DIR", help=f"where to keep the undo journal (default: FOLDER/{JOURNAL_DIR_NAME})")
    empty.set_defaults(func=cmd_empty)

    # ---- junk
    junk = commands.add_parser("junk", parents=[common, _tidy_parent()], help="find junk and zero-byte files",
                               description="Find junk files (Thumbs.db, desktop.ini, .DS_Store, temp files ...) and "
                                           "zero-byte files. Nothing changes unless you add --move; nothing is ever deleted.")
    junk.add_argument("folder")
    junk.add_argument("--kinds", nargs="+", choices=JUNK_KINDS, metavar="KIND",
                      help=f"what to look for: {' '.join(JUNK_KINDS)} (default: both)")
    junk.add_argument("--pattern", action="append", metavar="GLOB",
                      help="also treat file names matching this pattern as junk, e.g. '*.bak' (repeat for more)")
    junk.add_argument("--no-default-patterns", action="store_true",
                      help=f"do not use the built-in junk names ({', '.join(JUNK_PATTERNS)})")
    junk.add_argument("--top", type=int, default=20, help="how many files to list (default 20)")
    junk.add_argument("--csv", nargs="?", const="", metavar="PATH", help="write a CSV report of everything found")
    junk.add_argument("--move", nargs="?", const="", metavar="DIR",
                      help="move the files to DIR (default: FOLDER/Junk), keeping their sub-folders; can be undone")
    junk.add_argument("--dry-run", action="store_true", help="with --move: show what would move; change nothing")
    junk.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    junk.add_argument("--no-journal", action="store_true", help="do not record the move (it can then not be undone)")
    junk.add_argument("--journal-dir", metavar="DIR", help=f"where to keep the undo journal (default: FOLDER/{JOURNAL_DIR_NAME})")
    junk.set_defaults(func=cmd_junk)
    return parser


# ------------------------------------------------------------------ helpers

def _split_extensions(values):
    if not values:
        return None
    result = []
    for value in values:
        for part in value.split(","):
            part = part.strip()
            if part:
                result.append(part if part.startswith(".") else "." + part)
    if not result:
        raise CliError("--ext needs at least one extension")
    return result


def _ignore_options(args):
    """Keyword arguments for the ignore rules (--exclude, --ignore-file, --no-ignore-file)."""
    options = {}
    if getattr(args, "exclude", None):
        options["exclude"] = args.exclude
    if getattr(args, "no_ignore_file", False):
        options["ignore_file"] = False
    elif getattr(args, "ignore_file", None):
        options["ignore_file"] = args.ignore_file
    return options


def _filter_options(args):
    options = _ignore_options(args)
    for name in ("max_size", "modified_after", "modified_before"):
        value = getattr(args, name, None)
        if value is not None:
            options[name] = value
    if getattr(args, "glob", None):
        options["name_glob"] = args.glob
    if getattr(args, "regex", None):
        options["name_regex"] = args.regex
    return options


def _is_interactive():
    return sys.stdin is not None and sys.stdin.isatty()


def _confirm(question, assume_yes):
    """Ask before changing files. Without --yes this needs a person at the keyboard."""
    if assume_yes:
        return True
    not_interactive = CliError("this changes files; pass --yes to run without asking, or --dry-run to preview")
    if not _is_interactive():
        raise not_interactive
    try:
        answer = input(f"{question} [y/N] ").strip().lower()
    except EOFError:  # input is closed or piped: nobody can answer
        print(file=sys.stderr)
        raise not_interactive from None
    return answer in ("y", "yes")


def _emit_json(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _info(args, message):
    """Informational line: shown unless --quiet; goes to stderr in --json mode so stdout stays valid JSON."""
    if not args.quiet:
        print(message, file=sys.stderr if args.json else sys.stdout)


@contextlib.contextmanager
def _library_output(args):
    """Library messages (e.g. 'report generated') go to stderr in --json mode."""
    if args.json:
        with contextlib.redirect_stdout(sys.stderr):
            yield
    else:
        yield


def _make_progress(args):
    if args.quiet or args.no_progress:
        return None
    return ConsoleProgress()


def _print_plan(args, plan):
    shown = list(plan.items())[:PLAN_LINES_SHOWN]
    for source, destination in shown:
        _info(args, f"  {source}  ->  {destination}")
    if len(plan) > len(shown):
        _info(args, f"  ... and {len(plan) - len(shown)} more")


def _optional_path(value):
    """--csv with no value means 'default location' (None); with a value, that path."""
    return value or None


# ------------------------------------------------------------------ commands

def cmd_stats(args, progress):
    extensions = _split_extensions(args.ext)
    options = _filter_options(args)
    if args.min_size is not None:
        options["min_size"] = args.min_size
    collector = FileStatsCollector(
        args.folder, media_extensions=extensions, all_files=args.all_files,
        skip_folders=args.skip, progress=progress, verbose=False, **options)
    if progress is not None:
        progress.close()

    json_result = None
    with _library_output(args):
        if args.json:
            total, folders, size, average, common = collector.get_summary_stats()
            json_result = {
                "folder": os.path.abspath(args.folder),
                "total_files": total, "total_folders": folders, "total_size": size,
                "average_size": average, "most_common_type": common,
                "file_types": dict(collector.get_file_typedist()),
            }
        else:
            if collector.total_files:
                collector.print_summary_stats()
            else:
                print("No files found.")
            if args.types:
                collector.print_file_types()
            if args.sizes:
                collector.show_file_size_distribution()
            if args.folders:
                collector.show_folder_size_distribution()

        if args.csv is not None:
            collector.generate_file_stats_csv(_optional_path(args.csv))
        if args.html is not None:
            collector.generate_file_stats_html(_optional_path(args.html), confirm=False)
        if args.summary_html is not None:
            collector.generate_summary_html(_optional_path(args.summary_html))
        if args.diff is not None:
            if not os.path.exists(args.diff):
                raise CliError(f"previous CSV not found: {args.diff}")
            # a non-None output_path makes the library write a NEW difference file instead of overwriting the old CSV
            collector.generate_difference_file_csv(args.diff, output_path="new", only_diff=args.only_diff)
    if json_result is not None:
        _emit_json(json_result)
    return 0


def cmd_move(args, progress):
    if args.batch_size < 1:
        raise CliError("--batch-size must be at least 1")
    options = _filter_options(args)
    if args.min_size is not None:
        options["min_size"] = args.min_size
    kwargs = {}
    extensions = _split_extensions(args.ext)
    if extensions is not None:
        kwargs["media_extensions"] = extensions
    gatherer = MediaGatherer(
        args.folder, destination_folder=args.destination, skip_folders=args.skip, all_files=args.all_files,
        organize_by=_organizer(args), journal=not args.no_journal, journal_dir=args.journal_dir,
        progress=progress, verbose=False, **kwargs, **options)
    if progress is not None:
        progress.close()
    destination = gatherer.dest_folder

    files = gatherer.get_files_to_move(args.all_files)
    if not files:
        if args.json:
            _emit_json({"dry_run": args.dry_run, "destination": destination, "count": 0, "moves": {}})
        else:
            _info(args, "No files to move.")
        return 0

    plan = gatherer.preview_moves(files, destination)
    if progress is not None:
        progress.close()
    if args.dry_run:
        if args.json:
            _emit_json({"dry_run": True, "destination": destination, "count": len(plan), "moves": plan})
        else:
            print(f"DRY RUN: {len(plan)} file(s) would be moved to {destination} (nothing was changed)")
            _print_plan(args, plan)
        return 0

    _info(args, f"{len(plan)} file(s) will be moved to {destination}")
    if not args.quiet and not args.json:
        _print_plan(args, plan)
    if not _confirm(f"Move {len(plan)} file(s) to {destination}?", args.yes):
        _info(args, "Cancelled; nothing was changed.")
        return 0

    moved = gatherer.gather_media(batch_size=args.batch_size, time_delay=args.delay) or {}
    if progress is not None:
        progress.close()
    failed = gatherer.failed_moves
    if args.json:
        _emit_json({"dry_run": False, "destination": destination, "moved": moved,
                    "failed": [{"source": s, "error": e} for s, e in failed],
                    "journal": gatherer.last_journal_path})
    else:
        print(f"Moved {len(moved)} file(s) to {destination}")
        if gatherer.last_journal_path:
            command = f"filer undo \"{args.folder}\"" + (f" --journal-dir \"{args.journal_dir}\"" if args.journal_dir else "")
            if not paths.is_inside(args.folder, destination, allow_root=True):
                command += f" --allow-folder \"{destination}\""
            print(f"Undo with: {command}")
        for source, error in failed:
            print(f"Could not move {source}: {error}", file=sys.stderr)
    return 1 if failed else 0


def _organizer(args):
    if not args.organize_by:
        return None
    from Filing.organizer import Organizer
    try:
        return Organizer(args.organize_by, date_field=args.date_field)
    except ValueError as e:
        raise CliError(str(e))


def _journal_dir(args):
    return args.journal_dir or os.path.join(os.path.abspath(args.folder), JOURNAL_DIR_NAME)


def cmd_undo(args, progress):
    journal_dir = _journal_dir(args)
    if args.list:
        rows = []
        for path in list_journals(journal_dir):
            try:
                data = load_journal(path)
            except (OSError, ValueError):
                continue
            rows.append({"journal": path, "operation": data.get("operation"), "created": data.get("created"),
                         "files": len(data.get("moves", [])), "undone": bool(data.get("undone"))})
        if args.json:
            _emit_json(rows)
        elif not rows:
            print("No journals found.")
        else:
            for row in rows:
                state = "undone" if row["undone"] else "can be undone"
                print(f"{row['created']}  {row['operation']:<10} {row['files']:>6} file(s)  {state}  {row['journal']}")
        return 0

    if not args.journal and find_latest_journal(journal_dir) is None:
        raise CliError(f"nothing to undo (no journal in {journal_dir})")
    result = undo_moves(args.journal, root_folder=args.folder, journal_dir=args.journal_dir, dry_run=args.dry_run,
                        allowed_roots=[args.folder] + list(args.allow_folder))
    if args.json:
        _emit_json(result)
    else:
        verb = "Would restore" if args.dry_run else "Restored"
        print(f"{verb} {len(result['restored'])} file(s)")
        if result.get("recreated"):
            print(f"{'Would recreate' if args.dry_run else 'Recreated'} {len(result['recreated'])} empty folder(s)")
        for item in result["skipped"]:
            print(f"Skipped {item['source']}: {item['reason']}", file=sys.stderr)
        if any(str(item["reason"]).startswith("unsafe path") for item in result["skipped"]):
            print("Some files are outside the folders undo may touch. If you moved files to another destination "
                  "yourself, allow it with --allow-folder DIR.", file=sys.stderr)
    return 1 if result["skipped"] else 0


def cmd_dupes(args, progress):
    options = _filter_options(args)
    finder = DuplicateFinder(
        args.folder, skip_folders=args.skip, min_size=args.min_size, use_cache=not args.no_cache,
        progress=progress, verbose=False, **options)
    if args.clear_cache:
        finder.clear_cache()
    groups = finder.find()
    if progress is not None:
        progress.close()

    result = {
        "folder": os.path.abspath(args.folder),
        "groups": [{"size": g.size, "hash": g.digest, "files": g.files} for g in groups],
        "duplicate_count": finder.duplicate_count,
        "wasted_bytes": finder.wasted_bytes,
    }
    if not args.json:
        if groups:
            finder.print_summary(top=args.top)
        else:
            print("No duplicates found.")
    if args.csv is not None:
        with _library_output(args):
            finder.generate_duplicates_csv(_optional_path(args.csv), keep=args.keep)

    exit_code = 0
    if (args.move is not None or args.link) and groups:
        exit_code = _dupes_action(args, finder, result)
    elif args.move is not None or args.link:
        _info(args, "Nothing to do.")
    if finder.errors:
        _info(args, f"{len(finder.errors)} file(s) could not be checked or changed; the first: {finder.errors[0][0]}: {finder.errors[0][1]}")
    if args.json:
        _emit_json(result)
    return exit_code


def _dupes_action(args, finder, result):
    if args.link:
        question = (f"Replace {finder.duplicate_count} duplicate file(s) with hard links? "
                    "This CANNOT be undone")
        if not args.dry_run and not _confirm(question, args.yes):
            _info(args, "Cancelled; nothing was changed.")
            return 0
        linked = finder.link_duplicates(keep=args.keep, dry_run=args.dry_run, verify=not args.no_verify)
        result["linked"] = linked
        _info(args, f"{'Would link' if args.dry_run else 'Linked'} {len(linked)} duplicate(s)")
        return 0

    destination = finder.destination_folder if not args.move else args.move
    plan = finder.move_duplicates(destination_folder=destination, keep=args.keep, dry_run=True)
    if args.dry_run:
        result["planned_moves"] = plan
        _info(args, f"DRY RUN: {len(plan)} duplicate(s) would be moved to {destination} (nothing was changed)")
        _print_plan(args, plan)
        return 0
    if not _confirm(f"Move {len(plan)} duplicate(s) to {destination}?", args.yes):
        _info(args, "Cancelled; nothing was changed.")
        return 0
    moved = finder.move_duplicates(destination_folder=destination, keep=args.keep)
    result["moved"] = moved
    _info(args, f"Moved {len(moved)} duplicate(s) to {destination}")
    if moved and finder.journal_enabled:
        _info(args, f"Undo with: filer undo \"{args.folder}\"")
    return 1 if finder.errors and len(moved) < len(plan) else 0


def cmd_empty(args, progress):
    finder = EmptyFolderFinder(
        args.folder, skip_folders=args.skip, journal=not args.no_journal, journal_dir=args.journal_dir,
        progress=progress, verbose=False, **_ignore_options(args))
    folders = finder.find()
    if progress is not None:
        progress.close()
    result = {"folder": os.path.abspath(args.folder), "count": len(folders), "empty_folders": folders}

    if not args.remove:
        if args.json:
            _emit_json(result)
        elif folders:
            finder.print_summary()
            _info(args, "Nothing was removed. Add --remove to remove them (add --dry-run to preview).")
        else:
            print("No empty folders found.")
        return 0

    if not folders:
        if args.json:
            result["removed"] = []
            _emit_json(result)
        else:
            _info(args, "No empty folders to remove.")
        return 0
    if args.dry_run:
        planned = finder.remove(dry_run=True)
        result.update({"dry_run": True, "would_remove": planned})
        if args.json:
            _emit_json(result)
        else:
            print(f"DRY RUN: {len(planned)} empty folder(s) would be removed (nothing was changed)")
            for folder in planned[:PLAN_LINES_SHOWN]:
                _info(args, f"  {folder}")
            if len(planned) > PLAN_LINES_SHOWN:
                _info(args, f"  ... and {len(planned) - PLAN_LINES_SHOWN} more")
        return 0
    if not _confirm(f"Remove {len(folders)} empty folder(s)?", args.yes):
        _info(args, "Cancelled; nothing was changed.")
        return 0

    before = len(finder.errors)
    removed = finder.remove()
    problems = finder.errors[before:]
    result.update({"removed": removed, "failed": [{"folder": f, "error": e} for f, e in problems],
                   "journal": finder.last_journal_path})
    if args.json:
        _emit_json(result)
    else:
        print(f"Removed {len(removed)} empty folder(s)")
        if finder.last_journal_path:
            print(f"Undo with: filer undo \"{args.folder}\"" + (f" --journal-dir \"{args.journal_dir}\"" if args.journal_dir else ""))
        for folder, error in problems:
            print(f"Could not remove {folder}: {error}", file=sys.stderr)
    return 1 if problems else 0


def cmd_junk(args, progress):
    finder = JunkFinder(
        args.folder, patterns=[] if args.no_default_patterns else None, extra_patterns=args.pattern,
        kinds=args.kinds or JUNK_KINDS, destination_folder=args.move or None, skip_folders=args.skip,
        journal=not args.no_journal, journal_dir=args.journal_dir, progress=progress, verbose=False,
        **_ignore_options(args))
    finder.find()
    if progress is not None:
        progress.close()
    result = {"folder": os.path.abspath(args.folder), "junk_count": len(finder.junk),
              "zero_byte_count": len(finder.zero_byte), "total_bytes": finder.total_bytes, "files": finder.to_dicts()}
    if not args.json:
        if finder.files:
            finder.print_summary(top=args.top)
        else:
            print("No junk or zero-byte files found.")
    if args.csv is not None:
        with _library_output(args):
            result["csv"] = finder.generate_csv(_optional_path(args.csv))

    exit_code = 0
    if args.move is not None and finder.files:
        destination = finder.destination_folder
        plan = finder.move(dry_run=True)
        if args.dry_run:
            result["planned_moves"] = plan
            _info(args, f"DRY RUN: {len(plan)} file(s) would be moved to {destination} (nothing was changed)")
            _print_plan(args, plan)
        elif not _confirm(f"Move {len(plan)} file(s) to {destination}?", args.yes):
            _info(args, "Cancelled; nothing was changed.")
        else:
            moved = finder.move()
            result["moved"] = moved
            _info(args, f"Moved {len(moved)} file(s) to {destination}")
            if moved and finder.journal_enabled:
                _info(args, f"Undo with: filer undo \"{args.folder}\"" + (f" --journal-dir \"{args.journal_dir}\"" if args.journal_dir else "")
                      + ("" if paths.is_inside(args.folder, destination, allow_root=True)
                         else f" --allow-folder \"{destination}\""))
            if finder.errors:
                exit_code = 1
                _info(args, f"{len(finder.errors)} file(s) could not be moved; the first: {finder.errors[0][0]}: {finder.errors[0][1]}")
    elif args.move is not None:
        _info(args, "Nothing to do.")
    if args.json:
        _emit_json(result)
    return exit_code


# ------------------------------------------------------------------ entry point

def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    progress = _make_progress(args)
    try:
        return args.func(args, progress)
    except CliError as e:
        print(f"filer: error: {e}", file=sys.stderr)
        return 1
    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError, TypeError, OSError) as e:
        print(f"filer: error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nfiler: interrupted. Files already moved can be restored with 'filer undo'.", file=sys.stderr)
        return 130
    finally:
        if progress is not None:
            progress.close()


if __name__ == "__main__":
    sys.exit(main())
