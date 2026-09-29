[![Python Versions](https://img.shields.io/pypi/pyversions/pynhanes?style=plastic)](https://pypi.org/project/pynhanes/)
[![PyPI](https://img.shields.io/pypi/v/pynhanes?style=plastic)](https://pypi.org/project/pynhanes/)
[![License](https://img.shields.io/pypi/l/pynhanes?style=plastic)](https://opensource.org/licenses/MIT)
[![Documentation Status](https://readthedocs.org/projects/pynhanes/badge/?version=latest)](https://pynhanes.readthedocs.io/en/latest/?badge=latest)

# NHANES parser

## Python parser and scraper for NHANES accelerometry and questionnaire

[https://wwwn.cdc.gov/nchs/nhanes/default.aspx](https://wwwn.cdc.gov/nchs/nhanes/default.aspx)

> **Disclaimer**
>
> **pyNHANES is an independent project. It is not affiliated with, endorsed by or reviewed by
> the CDC, the National Center for Health Statistics, or any other body responsible for NHANES.**
>
> It is published as is, with no warranty of correctness, completeness or fitness for any purpose, and certainly may contain bugs. NHANES is a large and irregular dataset, and every decision this package makes on your behalf - which values become missing, how a variable is merged across surveys, how a derived column is computed - is a judgement that may not suit your question. Those decisions are written down in [How the data were processed](https://pynhanes.readthedocs.io/en/latest/citation.html) so
> that you can check them.
>
> Treat pyNHANES as a **fast way to screen a large dataset and find out what is worth pursuing**,
> not as a source of final numbers. Before anything is published, it is strongly recommended to 
> verify the variables you rely on against the NHANES documentation, and reproduce the result 
> with your own implementation of the steps that matter.



# Quick start

**The default path uses data that is already prepared, so nothing is downloaded from NHANES.**
The codebook of every variable ships inside the package, and the parsed tables are published as
a [GitHub release](https://github.com/timpyrkov/pynhanes/releases). Both are snapshots taken at
release time, which is what makes the first look at NHANES take seconds instead of spending a whole evening.

```
pip install pynhanes
pynhanes-scraper -o CSV/nhanes_codebook.csv     # the shipped codebook, ~1 second
```

**Reach for** `--refresh` **when the snapshot is not enough** - when NHANES has published a survey
since this release, or when you want variables that are not in the curated list, or values that
are not recoded. Then the full pipeline below runs for real: the documentation is scraped, the
data files are downloaded, and the table is parsed on your machine.

The four notebooks in `[scripts/](https://github.com/timpyrkov/pynhanes/tree/master/scripts)` walk through both precalc and true scrape/download/parse paths, and are also the
[Examples](https://pynhanes.readthedocs.io/en/latest/) in the documentation.

## Yes/No is recoded to 1/0, not 1/2

Worth knowing before you compare a parsed user data table with a NHANES documentation page. **NHANES encodes
Yes as 1 and No as 2.** Other biobanks - the UK Biobank among them - often encode it differently, Yes as 1 and No as 0,
and the same analysis run over both would be silently wrong. pyNHANES therefore rewrites values to more intuitive notation of No to 0, so that a column of answers can be averaged and compared.

The codebook carries **both dictionaries**: `Codebook` is what NHANES publishes, `Recoded Codebook` is what the default parsed table holds. You may require to keep the original codes with `--no-recode`, or
`recode=False` in `pynhanes.parser.parse()`.

# Features

- Scrape the NHANES documentation into a codebook - what every variable means and what its values mean
- Parse `.xpt` and mortality `.dat` files into one Pandas table, all surveys stacked
- Parse minute-level accelerometry of 2003-2006 and 2011-2014 into NumPy arrays



# The pipeline

NHANES is organised as surveys (`2011-2012`) -> components (`Questionnaire`) -> data files
(`OCQ`) -> variables (`OCD150`). pynhanes walks that in four steps, and **each step is one
command**:

```
  1. pynhanes-scraper     what NHANES has          free, reads nothing from the website
  2. pynhanes-downloader  get the data files       ~0.8 GB with the default "core" preset
  3. pynhanes-parser      one table of everything  CSV/nhanes_userdata.csv
  4. pynhanes-activity    minute-level movement    optional, needs ~18 GB of raw files
     -> pynhanes.NhanesLoader reads it all back for analysis
```

**Start with the scraper.** It writes the codebook of every NHANES variable, and it costs
nothing - read it before downloading anything, because it is the overview of what NHANES has.
`--availability` and `--datafiles` add the per-answer counts and the catalogue of data files.

```
pynhanes-scraper -o CSV/nhanes_codebook.csv --availability --datafiles
```

**Then download.** A bare run takes only the data files the parser uses - the `core` preset,
about 0.8 GB for all surveys. `-n` shows the plan and downloads nothing.

```
pynhanes-downloader -n -o XPT          # plan with sizes
pynhanes-downloader -o XPT             # core preset, ~0.8 GB
```

**Then parse.** One row per participant, one column per variable name, all surveys stacked. With
no `-v` it uses the 351 variables curated in `pynhanes/data/nhanes_variables.json`, which ships
with the package.

```
pynhanes-parser -i XPT -o CSV/nhanes_userdata.csv
```

**Accelerometry is optional and large**: download it explicitly, then parse it into `.npz`.

```
pynhanes-downloader -o XPT -a 2003,2005,2011,2013      # ~18 GB on disk
pynhanes-activity -i XPT -o NPZ -c CSV/nhanes_activity.csv
```

**Then analyse.** `pynhanes.NhanesLoader` reads the table back, attaches the accelerometry from
`NPZ/`, turns numeric answers into labels with the codebook, and `encode()` spreads categorical
answers into one column per answer. See `scripts/load_and_plot.ipynb`.

The four notebooks in `scripts/` do the same as the four commands, if you would rather work in
Jupyter. Get them with:

```
wget https://github.com/timpyrkov/pynhanes/archive/master/scripts.zip
unzip -j scripts.zip 'pynhanes-master/scripts/*' -d workfolder
cd workfolder; mkdir XPT NPZ CSV
```

> **A word of warning.** NHANES has recoded fields since 1999. pynhanes merges a variable across
> surveys into one row, taking the newest label and the union of value codes - which is usually
> what you want, and sometimes not. Check the categorical variables you rely on against the NHANES
> website. [How the data were processed](https://pynhanes.readthedocs.io/en/latest/citation.html) says exactly what the merge does.



# Command line scripts


| command               | what it does                                                            |
| --------------------- | ----------------------------------------------------------------------- |
| `pynhanes-scraper`    | scrape the variable codebook into a `.csv`                              |
| `pynhanes-downloader` | download data files (`.xpt`, zipped `.xpt`, mortality `.dat`)           |
| `pynhanes-parser`     | parse the data files into one table                                     |
| `pynhanes-activity`   | parse the minute-level accelerometry into `.npz` arrays                 |
| `pynhanes-wgetxpt`    | the old one-file-at-a-time downloader, now a wrapper (also `pywgetxpt`) |


Each is also `python -m pynhanes.scraper`, and usable from Python as `pynhanes.scraper` and so on.
They all take `-n`/`--noload` to see what would happen without doing it, `-j`/`--json` for
machine-readable output, and describe themselves for AI assistants in `-h`.

Every `.csv` pynhanes writes is `;` **separated**, because variable names and value labels contain
commas. Read one back with `pd.read_csv(path, sep=";", index_col=0, header=[0, 1])`.

## The codebook

`pynhanes-scraper` writes up to three files. The package carries all three gzipped (1.8 MB in the
wheel), so **the default reads nothing from the website**; `--refresh`, or picking a survey with
`-s`, goes and looks (~5 minutes for 1603 pages).


| file                      | one row per         | holds                                                           |
| ------------------------- | ------------------- | --------------------------------------------------------------- |
| `nhanes_codebook.csv`     | variable            | 12774 variables of 385 data files, their labels and value codes |
| `nhanes_availability.csv` | variable and survey | 50873 rows: who was asked, and the count of every single answer |
| `nhanes_datafiles.csv`    | data file           | 482 data files, the surveys they span and their size            |


Besides the value labels, every variable carries:


| column                                           | meaning                                                                                                                                                                                                  |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Type`                                           | `Continuous` (6978), `Binary` (2464), `Categorical` (2106), `Flag` (933, present or missing), `Text` (270), `Unknown` (23)                                                                               |
| `Truncated`                                      | `top` (159), `bottom` (106) or both (28): an end of the measurement is replaced by one value - everybody over 80 is reported as `80`. The parser uses it as an ordinary number, but the tail is not real |
| `Core`                                           | the variable is in one of the 50 data files the parser uses - exactly what `pynhanes-downloader` fetches by default                                                                                      |
| `Codebook`                                       | value labels as NHANES publishes them: `{"1": "Yes", "2": "No", "7": "Refused", "9": "Don't know"}`                                                                                                      |
| `Recoded Codebook`                               | value labels after parsing: `{"1": "Yes", "0": "No"}` - No is 0 and the missing answers are gone. Comparing the two is how you tell whether a variable is recoded                                        |
| `Surveys`, `Availability`, one column per survey | in how many surveys it exists, how many answered in all, and how many in each                                                                                                                            |


```python
import pynhanes
pynhanes.scraper.snapshot_info()          # {'scraped': '2026-09-23', 'variables': 12774, ...}
book = pynhanes.scraper.read_snapshot()   # the codebook as a DataFrame, no file needed
```

Useful options: `--availability` and `--datafiles` write the other two files, `-s`/`-c`/`-d` narrow
it to a survey, component or data file, `--store` keeps the pages in `~/.cache/pynhanes` for a
re-run. Full help in `pynhanes-scraper -h`.

## Downloading data

`pynhanes-downloader` reads the NHANES website, skips files already present, checks free disk
space, resumes interrupted downloads and never deletes anything.


| option              | meaning                                                                                                                                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-o`, `--output`    | destination folder, created if missing (default `./XPT`)                                                                                                                                                          |
| `-s`, `--surveys`   | survey(s) by first year: `1999` ... `2017`, `2021`, or `prepandemic` (default: all)                                                                                                                               |
| `-c`, `--component` | `Demographics`, `Dietary`, `Examination`, `Laboratory`, `Questionnaire`, `Mortality`                                                                                                                              |
| `-d`, `--data-file` | **default** `core` - the 50 data files the parser uses (~0.8 GB). `full` - everything NHANES publishes (~4.5 GB); `existing` - codes already in your folder; `@path` - your own list; or codes such as `DEMO,BMX` |
| `-a`, `--activity`  | minute-level accelerometry: `2003`, `2005` (PAXRAW), `2011`, `2013` (PAXMIN). Nothing by default                                                                                                                  |
| `--yes`             | re-download selected files even if present                                                                                                                                                                        |


```
pynhanes-downloader -o XPT -d full                      # everything NHANES publishes
pynhanes-downloader -o XPT -s 2021 -c Laboratory
pynhanes-downloader -o XPT -d DEMO,BMX,SMQ              # selected data files, all surveys
pynhanes-downloader -o XPT -d existing                  # fill missing surveys of what you have
pynhanes-downloader -o XPT -s 2021 --yes                # re-download the latest survey
```

The `core` preset keeps data files that have one row per participant, cover the whole examined
sample, are data rather than look-up tables, and exist across several surveys. NHANES publishes
much more: 482 data file codes in all, of which only 26 appear in every survey and 41% in just one.


|                                                    | GB   |
| -------------------------------------------------- | ---- |
| core - what the parser uses                        | 0.77 |
| everything except accelerometry                    | 4.47 |
| accelerometry (PAXRAW 2003-2006, PAXMIN 2011-2014) | 17.4 |


Sizes come from the NHANES listings and the `.zip` directories, so they are exact - within 0.4% of
real downloads. Requests are polite: one per 0.2 s per server, with backoff on `429`/`5xx`.
Listings are cached in `~/.cache/pynhanes/`, finished surveys once and the latest survey daily, so
a repeat run normally sends no requests at all. PAXMIN is served by the slow `ftp.cdc.gov`; the
plan measures the speed and warns. NHANES keeps adding data to recent surveys, so `--yes` is worth
using for the latest one - it has to be narrowed with `-s` or `-d`.

## Parsing

`pynhanes-parser` turns the downloaded files into one table - one row per participant, one column
per variable, all surveys stacked (113249 participants, 358 variables with the shipped list).


| option                        | meaning                                                                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-i`, `--input`               | folder with `.xpt` and mortality `.dat` files (default `XPT`)                                                                                                                            |
| `-o`, `--output`              | output `.csv` (default `CSV/nhanes_userdata.csv`)                                                                                                                                        |
| `-b`, `--codebook`            | codebook of `pynhanes-scraper`; falls back to the shipped one                                                                                                                            |
| `-v`, `--variables`           | `.json` of variable names, or a comma-separated list; falls back to the shipped one                                                                                                      |
| `-l`, `--layout`              | `names` - one column per variable name, `(topic, name)`, codes merged and derived variables computed (default); `codes` - one column per NHANES variable code, nothing merged or derived |
| `--write-variables`           | write the resolved name -> codes mapping for you to curate                                                                                                                               |
| `--prepandemic`               | read the 2017-March 2020 files (`P_*`) instead of the 2017-2018 ones - the same people, renumbered                                                                                       |
| `--no-derived`, `--no-recode` | skip derived variables / keep Yes/No as 1/2                                                                                                                                              |


The variables file is where the curation lives: one human-readable name, the NHANES codes it is
made of, newest first. This matters because NHANES renames things (`INDFMINC` became `INDFMIN2`)
and because some questions belong together (`SMQ020`, `SMQ120`, `SMQ150` are cigarettes, a pipe and
cigars). `--write-variables` writes a template from the codebook with the ambiguous names marked
for review - a name that means more than one thing is refused rather than guessed, since `Gender`
is both the participant and the household reference person.

**The table says how it was made.** The first cell of the first header row - which pandas leaves
empty - carries a provenance line, so a table that has been copied away from its folder still says
what it holds:

```
pynhanes 1.0.2 | names | recoded | derived | 2026-09-29;Demographic;Demographic
;Age;Gender
SEQN;;
```

Reading it the documented way is unaffected; the line comes back as `table.columns.names[0]`, or
from `pynhanes.parser.read_provenance(table)`.

**[How the data were processed](https://pynhanes.readthedocs.io/en/latest/citation.html) describes every step** - which values become empty,
how Yes/No becomes 1/0, and every derived variable - in a form meant to be quoted in a
Materials and Methods section.

## Accelerometry

Minute-level movement exists for two eras only: **PAXRAW** in 2003-2006 (a hip monitor, intensity
counts, plus steps in 2005-2006) and **PAXMIN** in 2011-2014 (a wrist monitor, MIMS triaxial units,
ambient light and a wake/sleep/non-wear label). `pynhanes-activity` reads them in chunks - about
1.5 GB of memory, ~40 s for 2003-2006 and ~2 min for 2011-2014 - and writes:


| file                        | holds                                                   |
| --------------------------- | ------------------------------------------------------- |
| `NPZ/nhanes_counts.npz`     | activity counts, 2003-2006, 7 days                      |
| `NPZ/nhanes_steps.npz`      | step counts, 2005-2006, 7 days                          |
| `NPZ/nhanes_triax_full.npz` | triaxial, light and status, 2011-2014, 9 days           |
| `NPZ/nhanes_triax.npz`      | the best 7 of those 9 days                              |
| `CSV/nhanes_activity.csv`   | one row per participant: how many days hold usable data |


Every array is one row per participant and one column per minute, and **column 0 is midnight of the
first day of wear**. The 7-day arrays are rolled so that column 0 is **Monday 00:00**, and every
array of one participant is rolled by the same number of minutes. The 9-day array is left in
calendar order, because a roll wraps the end back to the front and would put the 8th and 9th day
before the 1st.

`status` says what each minute was - `0` Missing, `1` Wake wear, `2` Sleep wear, `3` Non-wear,
`4` Unknown - and each file carries the same list under `Status keys`;
`pynhanes.activity.decode_status()` reads it back. Only 2011-2014 has the wake/sleep/non-wear
prediction; for 2003-2006 the status is `0` or `4`, which is how a minute with no data is told
apart from a minute of perfect stillness.

pynhanes parses only the minute-level files. The hourly (`PAXHR`) and daily (`PAXDAY`) aggregates
NHANES also publishes are not read as data - `PAXDAY` is used only for the start time of the first
day, and `PAXHD` only for the monitor status.



# Roadmap

Planned, not yet implemented. Open an issue if one of these matters to you - it moves things up
the list.

- **Raw 80 Hz accelerometry (`PAX80`).** NHANES has begun publishing the unaggregated sensor
  output, three axes at 80 samples per second, alongside the minute-level summaries pynhanes
  already parses. It is a different order of magnitude - a single participant's week is larger
  than the whole minute-level release - so downloading and parsing it needs its own streaming
  path rather than an extra flag on `pynhanes-activity`. Planned for a future version.
- **A survey-design helper.** pynhanes ships the weights, PSU and strata
  (see [Sample weights](https://pynhanes.readthedocs.io/en/latest/weights.html)) but no
  estimator. Design-based means and regressions are left to `samplics` or R's `survey`.
- **Accelerometry weights.** `CSV/nhanes_activity.csv` carries no weight column; join it to the
  parsed table on `SEQN` and use the examination weight.
