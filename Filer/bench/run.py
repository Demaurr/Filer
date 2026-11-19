"""
Time Filer's main operations on generated trees and print (or save) the numbers.

    python bench/run.py                                   # 10,000 files, 3 repeats
    python bench/run.py --sizes 10000 100000 --repeat 5 --json bench_1.7.json
    python bench/run.py --tree-dir D:/bench_tree --sizes 100000 --keep   # reuse a tree made by make_tree.py

What is timed (seconds, best and median of the repeats):
    scan            FileStatsCollector over every file
    scan_filtered   the same with a name filter and a size filter
    dupes_cold      DuplicateFinder with an empty hash cache (also reads files)
    dupes_warm      DuplicateFinder again, hashes remembered
    move_dry_run    MediaGatherer(organize_by="type") preview, nothing moved
    compare         FileStatsCollector.compare of a scan with an identical one (no disk access)

Notes: the operating system caches directory listings and file contents, so after the first run the
numbers are "warm file system" numbers. Compare runs on the same machine in the same state, and keep
other heavy programs (and antivirus scans) quiet. Trees are created in the system temp folder (or in
--tree-dir) and removed afterwards unless --keep is given. Nothing outside those folders is touched.
"""
import argparse
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import make_tree  # noqa: E402
from Filing import FileStatsCollector, DuplicateFinder, MediaGatherer, __version__  # noqa: E402
from Filing.cache import HashCache  # noqa: E402


def timed(function, repeat):
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        times.append(time.perf_counter() - start)
    return {"best": min(times), "median": statistics.median(times), "runs": len(times)}


def clear_cache(root):
    HashCache(HashCache.default_path(root)).clear()


def benchmark(root, repeat):
    results = {}
    results["scan"] = timed(lambda: FileStatsCollector(root, all_files=True, verbose=False), repeat)
    results["scan_filtered"] = timed(
        lambda: FileStatsCollector(root, all_files=True, verbose=False, name_glob="*.jpg", min_size=100), repeat)

    def dupes_cold():
        clear_cache(root)
        DuplicateFinder(root, verbose=False).find()

    results["dupes_cold"] = timed(dupes_cold, repeat)
    clear_cache(root)
    DuplicateFinder(root, verbose=False).find()  # fill the cache
    results["dupes_warm"] = timed(lambda: DuplicateFinder(root, verbose=False).find(), repeat)
    clear_cache(root)

    results["move_dry_run"] = timed(
        lambda: MediaGatherer(root, all_files=True, organize_by="type", verbose=False).gather_media(dry_run=True),
        repeat)

    older = FileStatsCollector(root, all_files=True, verbose=False)
    newer = FileStatsCollector(root, all_files=True, verbose=False)
    results["compare"] = timed(lambda: newer.compare(older), repeat)
    return results, len(older.file_stats)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark Filer on generated folder trees.")
    parser.add_argument("--sizes", type=int, nargs="+", default=[10000], help="number of files per tree")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--tree-dir", help="reuse (or create) a tree here; only one size is allowed")
    parser.add_argument("--keep", action="store_true", help="do not delete the generated trees")
    parser.add_argument("--json", metavar="FILE", help="also write the results to this file")
    args = parser.parse_args(argv)
    if args.tree_dir and len(args.sizes) != 1:
        parser.error("--tree-dir works with exactly one size")

    report = {"filer": __version__, "python": platform.python_version(), "platform": platform.platform(),
              "cpus": os.cpu_count(), "repeat": args.repeat, "trees": {}}
    for size in args.sizes:
        root = args.tree_dir or tempfile.mkdtemp(prefix="filer_bench_")
        created_here = False
        try:
            if not os.path.exists(os.path.join(root, make_tree.MARKER)):
                print(f"Creating a tree of {size} files in {root} ...", flush=True)
                start = time.perf_counter()
                make_tree.make_tree(root, size)
                created_here = True
                print(f"  done in {time.perf_counter() - start:.1f} s", flush=True)
            results, found = benchmark(root, args.repeat)
            report["trees"][str(size)] = {"files_found": found, "timings": results}
            print(f"\n{size} files ({found} found), Filer {__version__}, Python {report['python']}")
            print(f"  {'operation':<14}{'best (s)':>10}{'median (s)':>12}{'files/s (best)':>16}")
            for name, row in results.items():
                print(f"  {name:<14}{row['best']:>10.3f}{row['median']:>12.3f}{found / row['best']:>16,.0f}")
        finally:
            if not args.keep and (created_here or not args.tree_dir):
                shutil.rmtree(root, ignore_errors=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nResults written to {args.json}")


if __name__ == "__main__":
    main()
