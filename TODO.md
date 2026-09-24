# pynhanes TODO

Status: 2026-09-23, version **1.0.0**, nothing committed yet.

The September 2026 rebuild is finished. The four pipeline stages are commands that a person and an
AI assistant can both run - `pynhanes-downloader` -> `pynhanes-scraper` -> `pynhanes-parser` ->
`pynhanes-activity` - with 113 tests, a codebook snapshot and a curated variables file shipped
inside the package, and the whole of NHANES 1999-2023 parsed into `~/data/NHANES/`. How it works is
written down in [README.md](README.md), [MERGE.md](MERGE.md) and [PARSE.md](PARSE.md);
what was decided along the way is at the bottom of this file.

Two things are left before the release.

---

## 1. Move from `setup.py` to `pyproject.toml`

Done 2026-09-24. `setup.py` is gone; `MANIFEST.in` stays for the source distribution.

- [x] `[build-system]` setuptools >= 64, `[project]` with name, version, readme, license,
      classifiers, `[project.urls]` and `[project.scripts]` - the six console commands unchanged
- [x] **The plotting stack is no longer a hard dependency.** `scipy`, `matplotlib`, `seaborn`,
      `statannotations` and `jsoncomment` moved to `[project.optional-dependencies] plot`, since
      only `pynhanes/utils.py` and one reader in `loader.py` use them and both already import
      inside `try/except`. A plain install now pulls only numpy, pandas, requests,
      beautifulsoup4, lxml and tqdm - verified in a clean virtual environment
- [x] `find_packages(exclude=("docs"))` - the tuple with the missing comma - replaced by
      `[tool.setuptools.packages.find] include = ["pynhanes*"]`
- [x] `[tool.setuptools.package-data]` keeps the snapshot and the variables file in the wheel
- [x] `license = { text = "MIT License" }` rather than the PEP 639 form, which needs
      setuptools >= 77 in the build environment

### Release rehearsal - done, and it passed

```
python -m build                     # 1.85 MB wheel, 1.88 MB sdist
python -m twine check dist/*        # both PASSED
```

Installed into an empty virtual environment, which resolved **pandas 3.0.6 and numpy 2.5.3** -
both newer than the development environment, so the code is not pinned to what happens to be
installed here. In a folder with no `~/data` and no network:

- `pynhanes-scraper -o CSV/nhanes_codebook.csv` wrote 12774 variables from the shipped snapshot
- `pynhanes-parser -n` found the shipped variables file, listed the 50 missing data files and
  printed the `pynhanes-downloader` command that fetches them

All modules compile on Python 3.9, 3.10 and 3.12, so the declared `requires-python = ">=3.9"`
is honest - worth re-running before each release, since nothing in the test suite catches a
syntax feature that is too new.

### Left to do

- [ ] `twine upload dist/*` once the code is committed and pushed

## 2. Documentation on Read the Docs

Drafted and **building locally with zero warnings** (2026-09-24): `make -C docs html`, about
45 seconds including the execution of all four notebooks. Serve it with
`python -m http.server 8765 --directory docs/_build/html`.

```
.readthedocs.yaml           ubuntu-22.04, python 3.11, docs/conf.py, installs the checkout
docs/conf.py                rtd theme, the logo, autodoc + napoleon + myst + nbsphinx
docs/requirements.txt       sphinx stack, pynhanes' own deps, scikit-learn and lifelines
docs/Makefile  make.bat
docs/index.rst              includes README.md through myst, then the toctree
docs/citation.rst           how every field was processed - replaces MERGE.md and PARSE.md
docs/downloader.rst  scraper.rst  parser.rst  activity.rst  loader.rst  utils.rst
docs/img/logo.png
docs/notebook/              generated at build time by conf.py, gitignored
```

### Done

- [x] **The introduction is `README.md` itself**, included through `myst-parser`, so there is
      one text to keep up to date rather than two that drift
- [x] **`citation.rst`** written from `MERGE.md`, `PARSE.md` and the code: the merge rule, how
      variables are chosen, what becomes missing, the Yes/No recoding, how codes are merged,
      every derived variable, truncation, accelerometry, what is **not** done - and a paragraph
      ready to paste into a Materials and Methods section. `MERGE.md` and `PARSE.md` are gone,
      and the eleven places in the code and the README that pointed at them now point at the
      docs page
- [x] **All four notebooks are Examples**, numbered `1_codebook` ... `4_analysis`. `conf.py`
      copies them out of `scripts/` at build time under those names and rewrites the links
      between them, so `scripts/` stays the single source and a reader on GitHub still follows
      working links
- [x] **Order does not matter.** nbsphinx runs a notebook when Sphinx reads it, which is
      alphabetical in practice but is not a contract and breaks under a parallel build. Rather
      than depend on it, each notebook now fetches its own prerequisites - notebook 2 unpacks
      the codebook if it is not there, notebooks 3 and 4 fetch the parsed table if it is not
      there. A reader landing on page 3 from a search engine gets a page that runs
- [x] Notebooks are **executed at build time**, not committed with output: `nbsphinx_execute =
      "always"`, `nbsphinx_allow_errors = False`, so a broken example fails the build
- [x] Docstring fixes so that `autodoc` is clean: the `Notes` blocks of `paxraw_parser` and
      `paxmin_parser` are bullet lists now, and `scraper.rst` no longer asks for two methods as
      if they were module functions

### The Read the Docs connection - yours to do, not mine

1. Sign in at readthedocs.org with the GitHub account that owns `timpyrkov/pynhanes`
2. **Import a Project** -> refresh the repository list -> pick `pynhanes`
3. Check the project slug is `pynhanes`, so the address is `pynhanes.readthedocs.io`
4. Let it add the GitHub webhook, so every push to `master` rebuilds the docs
5. In Admin -> Advanced Settings, confirm it reads `.readthedocs.yaml` from the repository root
6. Tell me the first build log if it fails, and whether you want a `stable` version pinned to a
   git tag alongside `latest`
7. Uncomment the docs badge already sitting in `README.md` line 4 once the first build is green

### Still open

- [ ] The build downloads about 36 MB of release assets each time it runs the notebooks. Read
      the Docs allows 15 minutes and 7 GB, and the whole build takes under a minute here, so
      this is comfortable - but worth watching if the examples grow
- [ ] `docs/img/figures.png` - the introduction has no figure yet, by decision. Add one when
      there is something worth showing

## Decisions taken (not to be reopened)

- **Naming**: survey (2017-2018) -> component (Laboratory) -> data file (CBC, file `CBC_J`) ->
  variable (LBXWBCSI). The word "category" is only left in codebook column names.
- **The cross-survey merge stays**: one row per variable, newest label, union of value codes,
  oldest wording wins - described in `MERGE.md` instead of being changed.
- **`-d core` is the downloader default** (0.8 GB); the scraper always reads the full set of
  documentation pages.
- **Yes/No becomes 1/0**, "Refused" / "Don't know" / "Screened out" / "did not apply" become NaN,
  and top- or bottom-coded values are kept as ordinary numbers with a `Truncated` warning column.
- **`nhanes_userdata.csv` holds names, not codes**: layout `names` is the parser default and
  computes the derived variables. `-l codes` stays available but derives nothing.
- **One curated variables file**, `pynhanes/data/nhanes_variables.json`, 341 names and 484 codes,
  one variable per line, shipped with the package and used whenever `-v` names a file that is not
  there.
- **The codebook snapshot ships too**, so `pynhanes-scraper` answers in a second without reading
  1603 documentation pages. `--refresh` reads the website; rebuild the snapshot before a release
  with `tools/make_snapshot.py`.
- **Accelerometry is a separate pipeline**, never part of `core`. Only the minute-level files are
  parsed; `PAXDAY` and `PAXHD` are read for the start time and the monitor status, nothing else.
- **7-day arrays are rolled to Monday, the 9-day array is not** - a roll wraps the end back to the
  front, which only works over a whole week.
- **Every command is named `pynhanes-...`** and every `.csv` we write is `;` separated.
- **The 2017-March 2020 pre-pandemic files are never parsed by default** - they renumber the
  participants. `--prepandemic` asks for them explicitly.
