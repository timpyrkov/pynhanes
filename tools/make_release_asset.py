#!/usr/bin/env python
"""
Collect the parsed data files for a GitHub release and print the upload command

The parsed tables are too big, and too much of a choice, to ship inside the
wheel: they are one variables list, one layout and one codebook snapshot, and
they go stale with every new survey. A release asset is the right home - it
lives in GitHub's own storage rather than in the repository, so a `git clone`
never pays for it, bandwidth is free for public repos (unlike Git LFS), and
replacing a file does not rewrite history.

    python tools/make_release_asset.py                    # the default set
    python tools/make_release_asset.py FILE [FILE ...]    # whichever you name

A .csv is gzipped into release/; a .npz is already compressed and is taken as
it is. Not dist/, which is where `python -m build` puts the wheel - a
`twine upload dist/*` must never find a data file there. Nothing is uploaded:
run the printed command yourself when you are ready.
"""
import gzip
import os
import shutil
import sys

TAG = "data-v1"
REPO = "timpyrkov/pynhanes"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.expanduser("~/data/NHANES")
# nhanes_weights_dict.csv travels with the parsed data: the data carries the
# weight columns, the dictionary says which one each variable needs
DEFAULT = [f"{DATA}/CSV/nhanes_userdata.csv",
           f"{DATA}/CSV/nhanes_weights_dict.csv",
           f"{DATA}/NPZ/nhanes_steps.npz",
           f"{DATA}/NPZ/nhanes_counts.npz",
           f"{DATA}/NPZ/nhanes_triax.npz"]
# GitHub refuses a single release asset above this
MAX_ASSET = 2 * 1024 ** 3


def collect(source, into):
    """Gzip a .csv, take a .npz as it is; return the path of the asset"""
    name = os.path.basename(source)
    if name.endswith(".npz"):
        target = os.path.join(into, name)
        if os.path.abspath(target) != os.path.abspath(source):
            shutil.copyfile(source, target)
        return target
    target = os.path.join(into, name + ".gz")
    with open(source, "rb") as f_in, gzip.open(target, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    return target


def provenance(source):
    """The line a parsed .csv carries in the corner cell of its header"""
    if not source.endswith(".csv"):
        return None
    with open(source) as handle:
        stamp = handle.readline().split(";")[0]
    return stamp if stamp.startswith("pynhanes ") else None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    sources = [os.path.expanduser(a) for a in argv] or DEFAULT
    missing = [s for s in sources if not os.path.isfile(s)]
    if missing:
        print("ERROR: not found:\n  " + "\n  ".join(missing))
        return 1
    for source in sources:
        if source.endswith(".csv") and provenance(source) is None:
            print(f"ERROR: '{source}' carries no provenance line - parse it again with "
                  f"pynhanes-parser, so that the release says what it holds")
            return 1

    into = os.path.join(ROOT, "release")
    os.makedirs(into, exist_ok=True)
    assets, total = [], 0
    for source in sources:
        target = collect(source, into)
        size = os.path.getsize(target)
        total += size
        assets.append(target)
        stamp = provenance(source)
        print(f"  {os.path.basename(target):<28} {size / 1e6:8.1f} MB"
              f"{'   ' + stamp if stamp else ''}")
        if size > MAX_ASSET:
            print("  WARNING: above GitHub's 2 GB limit for one asset")
    print(f"  {'total':<28} {total / 1e6:8.1f} MB in {into}")
    print()
    print("Upload them with (nothing has been uploaded):")
    files = " \\\n      ".join(assets)
    print(f"  gh release create {TAG} \\\n      {files} \\")
    print(f"      --repo {REPO} --title 'pynhanes data {TAG}' \\")
    print("      --notes 'Parsed NHANES tables and accelerometry arrays.'")
    print("  # already released? attach or replace single files instead:")
    print(f"  gh release upload {TAG} <file> --repo {REPO} --clobber")
    print()
    print("Readers then fetch them from:")
    for target in assets:
        print(f"  https://github.com/{REPO}/releases/download/{TAG}/{os.path.basename(target)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
