"""
Build a reproducible test folder tree for benchmarks.

    python bench/make_tree.py TARGET_DIR --files 10000
    python bench/make_tree.py --files 100000     # TARGET_DIR defaults to a new folder in the system temp dir

The tree is always the same for the same arguments (fixed seed): about 20 files per folder, folders up
to 4 levels deep, a mix of extensions, small files (0-30 KB, a few bigger) and roughly 5% copies of
other files, so duplicate searches have something to find.

Safety: files are only ever created inside TARGET_DIR, and only if it is missing, empty, or was made by
this script before (it holds the marker file .filer_bench_tree). Anything else is refused, so a real
folder can never be filled with test files by mistake.
"""
import argparse
import os
import random
import tempfile

MARKER = ".filer_bench_tree"
EXTENSIONS = [".jpg", ".png", ".mp3", ".mp4", ".txt", ".pdf", ".docx", ".zip", ".py", ".json", ".csv", ".gif",
              ".mkv", ".log"]


def prepare_target(target):
    """Return the folder to fill (created if needed), or stop if it holds anything this script did not make."""
    target = os.path.abspath(target)
    if os.path.exists(target) and not os.path.isdir(target):
        raise SystemExit(f"{target} exists and is not a folder")
    if os.path.isdir(target) and os.listdir(target) and not os.path.exists(os.path.join(target, MARKER)):
        raise SystemExit(f"Refusing to use {target}: it is not empty and was not created by make_tree.py")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, MARKER), "w") as f:
        f.write("created by bench/make_tree.py; safe to delete\n")
    return target


def make_tree(target, files=10000, seed=1, per_folder=20, duplicate_ratio=0.05):
    """Create `files` files below target (see the module text). Returns the number of folders made."""
    target = prepare_target(target)
    rng = random.Random(seed)
    folder_count = max(1, files // per_folder)
    folders = [target]
    depths = {target: 0}
    while len(folders) < folder_count + 1:
        parent = rng.choice(folders)
        if depths[parent] >= 4:
            continue
        child = os.path.join(parent, f"dir{len(folders):05d}")
        depths[child] = depths[parent] + 1
        folders.append(child)
    for folder in folders:
        os.makedirs(folder, exist_ok=True)

    written = []
    for n in range(files):
        folder = folders[n % len(folders)]
        extension = rng.choice(EXTENSIONS)
        path = os.path.join(folder, f"file{n:07d}{extension}")
        if written and rng.random() < duplicate_ratio:
            with open(rng.choice(written), "rb") as f:
                content = f.read()
        else:
            size = rng.choice([0, 10, 100, 1000, 4096, 4096, 4096, 30000]) if rng.random() < 0.995 else 300_000
            content = rng.randbytes(size)
        with open(path, "wb") as f:
            f.write(content)
        written.append(path)
        stamp = 1_700_000_000 + rng.randint(0, 63_000_000)  # spread over about two years, whole seconds
        os.utime(path, (stamp, stamp))
    return len(folders)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a reproducible benchmark folder tree.")
    parser.add_argument("target", nargs="?", help="folder to fill (default: a new folder in the system temp dir)")
    parser.add_argument("--files", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    target = args.target or tempfile.mkdtemp(prefix="filer_bench_")
    folders = make_tree(target, args.files, seed=args.seed)
    print(f"Created {args.files} files in {folders} folders under {target}")


if __name__ == "__main__":
    main()
