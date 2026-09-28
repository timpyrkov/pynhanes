#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Parse NHANES data files (.xpt and linked mortality .dat) into one table,
one row per participant and one column per variable, decoded with the
codebook of pynhanes-scraper.

What the parser does, in order (see the documentation for the details):

1. resolve the requested variable names to NHANES variable codes
2. read the data files, stacking surveys on top of each other
3. replace "Refused", "Don't know", "Screened out", ... with NaN
4. recode Yes/No and Male/Female from 1/2 to 1/0
5. merge the codes of one variable into a single column
6. compute derived variables (smoking status, blood pressure, ...)
7. write a .csv with two column levels: topic and variable name

Example
-------
>>> import pynhanes
>>> df = pynhanes.parser.parse("XPT", "CSV/nhanes_userdata.csv",
...                            variables="CSV/nhanes_variables.json")

Command line
------------
    pynhanes-parser -h                          # options and workflow
    pynhanes-parser -n                          # what would be parsed, parse nothing
    pynhanes-parser                             # XPT -> CSV/nhanes_userdata.csv
    pynhanes-parser -v "Age,Gender,BMI (kg/m2)"
    pynhanes-parser --write-variables CSV/variables.json

"""

import os
import re
import sys
import json
import glob
import argparse
import warnings
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd



# Value labels that mean "no data" - the code they are attached to becomes NaN
MISSING_LABELS = [
    "refuse", "refused", "sp refused", "no response", "blank", "< blank >", "error",
    "unknown", "don't know", "dont know", "don't  know", "don't know/not sure",
    "no / don't know", "missing", "screened out", "not determined, picture missing",
    "cannot assess", "could not assess", "cannot be assessed", "can not be assessed",
    "can not assess", "could not obtain", "could not interpret", "could not determine",
    "calculation cannot be determined", "data acquisition problems",
    "text present but uncodable", "blank but applicable",
    # the question or the measurement did not apply to this participant
    "not applicable", "no result", "no lab result", "not done", "no examination",
    "not examined, pregnancy", "not examined, other reason",
    "not examined, weight > 300 lbs",
    "not examined, jewelry or other objects not removed or metal in body",
    "tested but vo2max estimate missing",
]

# Yes/No variables with a third substantive answer: they are not simple 1/0,
# so each one says explicitly what to do with every code. The third answer
# becomes empty - it is too rare to carry weight and would otherwise sit in a
# 0/1 column as a 3
THIRD_OPTION = {
    "DIQ010": {1: 1, 2: 0, 3: np.nan},      # 3 - borderline diabetes
    "PAD020": {1: 1, 2: 0, 3: np.nan},      # 3 - unable to do activity
    "PAD200": {1: 1, 2: 0, 3: np.nan},
    "PAD320": {1: 1, 2: 0, 3: np.nan},
    "PAQ650": {1: 1, 2: 0, 3: np.nan},
    "PAQ665": {1: 1, 2: 0, 3: np.nan},
    "PAQ685": {1: 1, 2: 0, 3: np.nan},      # 3 - never thought about air quality
}

# Values that mean something else than the number they are, in variables whose
# other values are ordinary numbers. NHANES writes them in each variable's own
# words, so no rule can recognise them
# SAS stores "not in this subsample" as a zero weight, which the XPORT reader
# renders as 5.4e-79.  It is harmless in a weighted average but wrecks any
# filter, ratio or logarithm, so it is forced back to a true zero.
WEIGHT_FLOOR = 1e-9

SENTINELS = {
    "DED120": {3333: np.nan},               # does not work or go to school
    "DED125": {3333: np.nan},
    "DBD090": {6666: 0, 5555: 22},          # less than weekly -> 0, more than 21 -> 22
    "DBD091": {6666: 0, 5555: 22},
    "DBD895": {6666: 0, 5555: 22},
}

# Two layouts of the output table, see the documentation
#   "codes" - one column per NHANES variable code, (data file, code)
#   "names" - one column per variable name, (topic, name)
# Every .csv pynhanes writes is ";" separated, codebook and parsed table alike,
# because variable names and value labels contain commas
# Data files are read in parallel threads. Most of a read is waiting for the
# file, which is what threads are good at - and processes are not, because the
# frames would have to be pickled back
WORKERS = min(8, os.cpu_count() or 1)
# below this many files the threads cost more than they save
PARALLEL_MIN_FILES = 8

# Variables of the linked mortality files, which are .dat and not .xpt
MORTALITY_CODES = {"ELIGSTAT", "MORTSTAT", "UCOD_LEADING", "DIABETES", "HYPERTEN",
                   "DODQTR", "DODYEAR", "WGT_NEW", "SA_WGT_NEW", "PERMTH_INT", "PERMTH_EXM"}

LAYOUTS = ("codes", "names")
SEPARATOR = ";"
SEPARATORS = {"codes": SEPARATOR, "names": SEPARATOR}


# ---------------------------------------------------------------------------
# Codebook and variable selection
# ---------------------------------------------------------------------------

def read_codebook(path="CSV/nhanes_codebook.csv", log=print):
    """
    Read the codebook written by pynhanes-scraper

    When there is no codebook at that path, the one that ships with pynhanes is
    used, so that parsing works on a fresh install. Its date is printed, and
    `pynhanes-scraper -o <path> --refresh` writes a current one.

    Parameters
    ----------
    path : str, default "CSV/nhanes_codebook.csv"
        Path to the codebook .csv
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Codebook with the "Codebook" column parsed into dictionaries

    """
    path = os.path.expanduser(path)
    if os.path.isfile(path):
        df = pd.read_csv(path, delimiter=SEPARATOR, index_col=0, low_memory=False)
    else:
        from pynhanes import scraper                  # lazy: keeps the import order simple
        df = scraper.read_snapshot("codebook")
        if df is None:
            raise ValueError(f"Codebook '{path}' not found - make it with: "
                             f"pynhanes-scraper -o {path}")
        info = scraper.snapshot_info()
        log(f"No codebook at '{path}' - using the one that ships with pynhanes, scraped on "
            f"{info.get('scraped', 'an unknown date')} ({len(df)} variables). "
            f"Write a current one with: pynhanes-scraper -o {path} --refresh")
    for column in ("Codebook", "Recoded Codebook"):
        if column in df.columns:
            df[column] = [json.loads(c) if isinstance(c, str) else c for c in df[column].values]
    return df


def shipped_variables():
    """
    Path of the curated variables file that ships with pynhanes

    Returns
    -------
    pathlib.Path or None
        Path of "nhanes_variables.json" inside the installed package

    """
    try:
        from importlib.resources import files
        path = files("pynhanes") / "data" / "nhanes_variables.json"
        return path if path.is_file() else None
    except (ImportError, FileNotFoundError, ModuleNotFoundError):   # pragma: no cover
        return None


def read_variables(variables, log=None):
    """
    Read the requested variables

    A path that looks like a .json file but is not there falls back to the
    curated file that ships with pynhanes, so that parsing works on a fresh
    install. Anything else is read as a comma-separated list of names.

    Parameters
    ----------
    variables : str, list or dict
        Path to a .json file, a comma-separated string, a list of variable
        names or codes, or a dict of name -> list of variable codes
    log : callable or None
        Function to print messages; None keeps quiet

    Returns
    -------
    list or dict
        List of names / codes, or an explicit name -> codes mapping

    """
    if isinstance(variables, dict):
        return variables
    if isinstance(variables, (list, tuple)):
        return list(variables)
    text = str(variables)
    path = os.path.expanduser(text)
    if not os.path.isfile(path) and text.lower().endswith(".json"):
        shipped = shipped_variables()
        if shipped is None:
            raise ValueError(f"Variables file '{text}' not found, and pynhanes ships none")
        content = shipped.read_text()
        if log is not None:
            names = json.loads(re.sub(r"^\s*//.*$", "", content, flags=re.M))
            log(f"No variables file at '{text}' - using the {len(names)} variables that ship "
                f"with pynhanes. Write your own with: pynhanes-parser --write-variables {text}")
        return json.loads(re.sub(r"^\s*//.*$", "", content, flags=re.M))
    if os.path.isfile(path):
        with open(path) as f:
            content = f.read()
        content = re.sub(r"^\s*//.*$", "", content, flags=re.M)     # allow // comments
        return json.loads(content)
    return [t.strip() for t in text.split(",") if t.strip()]


def resolve_variables(variables, codebook, merge_ambiguous=False, log=print,
                      warn_ambiguous=True):
    """
    Resolve requested variables to an ordered mapping name -> variable codes

    A name given in the codebook column "Combined Name" can belong to several
    variable codes (e.g. "Gender" is both RIAGENDR of the participant and
    DMDHRGND of the household reference person). Such names are refused
    unless merge_ambiguous is True, because merging them would mix data of
    different people or age groups.

    Parameters
    ----------
    variables : list, dict or str
        See read_variables()
    codebook : DataFrame
        Codebook from read_codebook()
    merge_ambiguous : bool, default False
        If True - merge every code of an ambiguous name instead of refusing,
        in the order they appear in the codebook
    log : callable, default print
        Function to print messages
    warn_ambiguous : bool, default True
        Warn about names that belong to several codes; not needed in layout
        "codes", where every code becomes its own column

    Returns
    -------
    dict
        Variable name -> list of variable codes, in merge order

    """
    variables = read_variables(variables, log if warn_ambiguous else None)
    if isinstance(variables, dict):
        unknown = sorted({c for codes in variables.values() for c in codes
                          if c not in codebook.index})
        if unknown:
            raise ValueError(f"Unknown variable code(s): {', '.join(unknown[:10])}. "
                             f"Codes come from the codebook, e.g. RIDAGEYR.")
        return {name: list(codes) for name, codes in variables.items()}

    by_name = {}
    for code, name in codebook["Combined Name"].items():
        if isinstance(name, str) and name:
            by_name.setdefault(name, []).append(code)
    mapping, ambiguous, unknown = {}, {}, []
    for item in variables:
        if item in by_name:
            codes = by_name[item]
            if len(codes) > 1:
                ambiguous[item] = codes
            mapping[item] = list(codes)
        elif item in codebook.index:                      # a variable code was given
            mapping[codebook.loc[item, "Combined Name"] if isinstance(
                codebook.loc[item, "Combined Name"], str) else item] = [item]
        else:
            unknown.append(item)
    if unknown:
        raise ValueError(f"Unknown variable name(s): {', '.join(unknown[:10])}"
                         f"{'' if len(unknown) <= 10 else f' and {len(unknown) - 10} more'}. "
                         f"Names come from the codebook column 'Combined Name', "
                         f"or give variable codes such as RIDAGEYR.")
    if ambiguous and not merge_ambiguous:
        listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in list(ambiguous.items())[:5])
        raise ValueError(
            f"{len(ambiguous)} requested name(s) belong to more than one variable code, and "
            f"merging them may mix different people or age groups ({listed}"
            f"{' ...' if len(ambiguous) > 5 else ''}). Write a template with "
            f"--write-variables FILE, keep the code you want in each entry, and use that file; "
            f"or pass --merge-ambiguous to merge them in codebook order.")
    if warn_ambiguous:
        for name, codes in ambiguous.items():
            log(f"WARNING: '{name}' merges {', '.join(codes)} (first non-missing value wins)")
    return mapping


def write_variables(mapping, path, codebook, log=print):
    """
    Write a name -> codes mapping to .json for the user to curate

    Parameters
    ----------
    mapping : dict
        Variable name -> list of variable codes
    path : str
        Path to the output .json
    codebook : DataFrame
        Codebook from read_codebook()
    log : callable, default print
        Function to print messages

    """
    path = os.path.expanduser(path)
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    lines, review = ["{"], 0
    for i, (name, codes) in enumerate(mapping.items()):
        comma = "," if i < len(mapping) - 1 else ""
        entry = json.dumps(codes)
        if len(codes) > 1:
            review += 1
            labels = " | ".join(f"{c}: {codebook.loc[c, 'Name']}" for c in codes if c in codebook.index)
            lines.append(f"    // REVIEW - {labels}")
        lines.append(f"    {json.dumps(name)}: {entry}{comma}")
    lines.append("}")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    marked = ""
    if review:
        marked = f" - {review} entr{'y' if review == 1 else 'ies'} marked REVIEW"
    log(f"Wrote {len(mapping)} variables to {path}{marked}")


# ---------------------------------------------------------------------------
# Reading data files
# ---------------------------------------------------------------------------

def _data_file_code(path):
    """
    Data file code of a local file, e.g. 'XPT/DEMO_J.xpt' -> 'DEMO'

    The survey is written as a suffix (_B ... _L, and whatever letter the next
    cycle gets) or, for the 2017-March 2020 pre-pandemic release, as the
    prefix P_.
    """
    stem = os.path.splitext(os.path.basename(path))[0].upper()
    if "_MORT_" in stem:
        return "MORT"
    stem = re.sub(r"^P_", "", stem)
    return re.sub(r"_[A-Z]$", "", stem)


def _is_prepandemic(path):
    """
    True for a file of the 2017-March 2020 pre-pandemic release (P_DEMO.xpt)
    """
    return os.path.basename(path).upper().startswith("P_")


def _is_survey_2017(path):
    """
    True for a file of the 2017-2018 survey (DEMO_J.xpt), whose participants
    the pre-pandemic release holds again, under other SEQN
    """
    stem = os.path.splitext(os.path.basename(path))[0].upper()
    return stem.endswith("_J")


def local_data_files(folder):
    """
    Map data file code -> list of local file paths

    Parameters
    ----------
    folder : str
        Folder with .xpt and mortality .dat files

    Returns
    -------
    dict
        Data file code -> sorted list of paths

    """
    folder = os.path.expanduser(folder)
    files = {}
    for path in sorted(glob.glob(os.path.join(folder, "*"))):
        if os.path.splitext(path)[1].lower() in (".xpt", ".dat") and os.path.isfile(path):
            files.setdefault(_data_file_code(path), []).append(path)
    return files


def data_files_of(codes, codebook):
    """
    Data files that hold the requested variable codes

    A variable can live in several data files - AGQ030 (hay fever) is in MCQ,
    RDQ and AGQ depending on the survey - so every one of them is named here,
    and the values are merged later.

    Parameters
    ----------
    codes : iterable
        Variable codes
    codebook : DataFrame
        Codebook from read_codebook()

    Returns
    -------
    set
        Data file codes, e.g. {"DEMO", "BMX"}; "MORT" for mortality variables

    """
    files = set()
    for code in codes:
        if code in MORTALITY_CODES:
            files.add("MORT")
        if code in codebook.index:
            files.update(str(codebook.loc[code, "Categories"]).split(","))
    return {f.strip() for f in files if f.strip()}


def _read_xpt(path, codes, log=print):
    """
    Read one .xpt file, keeping the requested columns
    """
    df = pd.read_sas(path, format="xport")
    if "SEQN" not in df.columns:
        log(f"WARNING: {os.path.basename(path)} has no SEQN column, skipped")
        return pd.DataFrame()
    df = df.set_index("SEQN")
    duplicated = int(df.index.duplicated().sum())
    if duplicated:
        log(f"WARNING: {os.path.basename(path)} has {duplicated} extra row(s) for participants "
            f"who appear more than once (individual foods, supplements, per-tooth or per-ear "
            f"tables): only the first row of each participant is used")
        df = df[~df.index.duplicated(keep="first")]
    keep = [c for c in df.columns if c in codes]
    return df[keep]


def _read_dat(path):
    """
    Read one public-use linked mortality .dat file
    """
    widths = [14, 1, 1, 3, 1, 1, 1, 4, 8, 8, 3, 3]
    names = ["SEQN", "ELIGSTAT", "MORTSTAT", "UCOD_LEADING", "DIABETES", "HYPERTEN",
             "DODQTR", "DODYEAR", "WGT_NEW", "SA_WGT_NEW", "PERMTH_INT", "PERMTH_EXM"]
    df = pd.read_fwf(path, widths=widths, names=names, na_values=".")
    return df.set_index("SEQN")


def _read_xpt_task(task):
    """
    Read one .xpt file, collecting messages instead of printing them, so that
    several files can be read at once without their warnings interleaving

    """
    path, codes = task
    messages = []
    try:
        return path, _read_xpt(path, set(codes), messages.append), messages
    except Exception as e:
        messages.append(f"WARNING: could not read {os.path.basename(path)}: "
                        f"{e.__class__.__name__}: {e}")
        return path, None, messages


def _read_xpts(paths, codes, log, workers):
    """
    Read .xpt files, in parallel unless workers is 1, and keep their order

    Parameters
    ----------
    paths : list
        Paths of the .xpt files to read
    codes : set
        Variable codes to keep
    log : callable
        Function to print messages
    workers : int
        Number of threads; 1 reads the files one after another

    Returns
    -------
    dict
        Path -> DataFrame of the columns found in that file

    """
    tasks = [(path, tuple(codes)) for path in paths]
    if workers <= 1 or len(tasks) < PARALLEL_MIN_FILES:
        results = [_read_xpt_task(t) for t in tasks]
    else:
        try:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(_read_xpt_task, tasks))
        except Exception as e:      # no threads here (restricted environment)
            log(f"WARNING: reading files in parallel failed ({e.__class__.__name__}: {e}), "
                f"reading them one by one")
            results = [_read_xpt_task(t) for t in tasks]
    frames = {}
    for path, frame, messages in results:
        for message in messages:
            log(message)
        if frame is not None:
            frames[path] = frame
    return frames


def choose_prepandemic(available, prepandemic=False, log=print):
    """
    Decide between the 2017-2018 files and the 2017-March 2020 pre-pandemic ones

    The pre-pandemic release combines the 2017-2018 survey with the part of
    2019-2020 that was collected before the pandemic, and **renumbers the
    participants**: P_DEMO holds 15560 people with SEQN 109263-124822, while
    DEMO_J holds 9254 with SEQN 93703-102956, and the 9254 are inside both.
    Reading both would count those people twice without any SEQN colliding, so
    only one of the two is read.

    Parameters
    ----------
    available : dict
        Data file code -> list of paths, from local_data_files()
    prepandemic : bool, default False
        False - read the 2017-2018 files and leave the pre-pandemic ones;
        True - the other way round
    log : callable, default print
        Function to print messages

    Returns
    -------
    dict
        The same mapping without the files that must not be read

    """
    dropped, kept = 0, 0
    out = {}
    for code, paths in available.items():
        has_pre = any(_is_prepandemic(p) for p in paths)
        if prepandemic and has_pre:
            keep = [p for p in paths if not _is_survey_2017(p)]
        elif has_pre:
            keep = [p for p in paths if not _is_prepandemic(p)]
        else:
            keep = list(paths)
        dropped += len(paths) - len(keep)
        kept += 1 if has_pre else 0
        out[code] = keep
    if dropped:
        if prepandemic:
            log(f"Reading the 2017-March 2020 pre-pandemic files of {kept} data file(s) "
                f"instead of the 2017-2018 ones: the same people, renumbered, plus the "
                f"2019-March 2020 part. Their sample weights are WTINTPRP and WTMECPRP")
        else:
            log(f"Leaving out {dropped} pre-pandemic file(s) (P_*): they hold the 2017-2018 "
                f"participants again, under other SEQN. Use --prepandemic to read them "
                f"instead of the 2017-2018 files")
    return out


def read_data(folder, codes, log=print, data_files=None, workers=None, prepandemic=False):
    """
    Read every data file that holds a requested variable

    Parameters
    ----------
    folder : str
        Folder with .xpt and mortality .dat files
    codes : list
        Variable codes to keep
    log : callable, default print
        Function to print messages
    data_files : iterable or None, default None
        Data file codes to read, e.g. the result of data_files_of(); None -
        every .xpt and .dat file in the folder, which means reading the
        accelerometry files too if they are there
    workers : int or None, default None
        Threads reading files in parallel; None - WORKERS, 1 - one by one
    prepandemic : bool, default False
        Read the 2017-March 2020 pre-pandemic files instead of the 2017-2018
        ones, see choose_prepandemic()

    Returns
    -------
    DataFrame
        One row per participant (SEQN), one column per variable code found

    """
    codes = set(codes)
    workers = WORKERS if workers is None else max(1, int(workers))
    available = local_data_files(folder)
    if data_files is not None:
        wanted = set(data_files)
        available = {k: v for k, v in available.items() if k in wanted}
    available = choose_prepandemic(available, prepandemic, log)
    xpt_paths = [p for code, paths in sorted(available.items()) if code != "MORT" for p in paths]
    read = _read_xpts(xpt_paths, codes, log, workers)
    frames = []
    for code, paths in sorted(available.items()):
        if code == "MORT":
            if not codes & MORTALITY_CODES:
                continue
            year = max(int(p.split("_")[-2]) for p in paths)      # latest follow-up release
            paths = [p for p in paths if f"_MORT_{year}_" in p]
            stacked = pd.concat([_read_dat(p) for p in paths])
            stacked = stacked[[c for c in stacked.columns if c in codes]]
        else:
            parts = [read[p] for p in paths if p in read and not read[p].empty]
            if not parts:
                continue
            stacked = pd.concat(parts, join="outer", axis=0)
        if stacked.empty:
            continue
        stacked = stacked[~stacked.index.duplicated(keep="first")]
        frames.append(stacked)
    if not frames:
        raise ValueError(f"No data files with the requested variables found in '{folder}' - "
                         f"download them with: pynhanes-downloader -o {folder}")
    data = pd.concat(frames, join="outer", axis=1)
    # one variable code can live in several data files (AGQ030 is in MCQ, RDQ
    # and AGQ): keep one column, filled from every file that has it
    if data.columns.duplicated().any():
        merged = {}
        for column in dict.fromkeys(data.columns):
            part = data[column]
            if isinstance(part, pd.DataFrame):
                values = part.iloc[:, 0]
                for i in range(1, part.shape[1]):
                    values = values.fillna(part.iloc[:, i])
                log(f"'{column}' is stored in {part.shape[1]} data files, values merged")
                part = values
            merged[column] = part
        data = pd.DataFrame(merged, index=data.index)
    data.index = data.index.astype(np.int64)
    return data.sort_index()


# ---------------------------------------------------------------------------
# Decoding and recoding
# ---------------------------------------------------------------------------

def _as_text(series):
    """
    Text values of a character variable (SAS stores them as bytes), empty
    strings becoming NaN

    Returns
    -------
    Series or None
        Text values, or None if the variable is numeric

    """
    if series.dtype.kind in "fiub":
        return None
    values = series.map(lambda v: v.decode("utf8", "replace").strip()
                        if isinstance(v, (bytes, bytearray)) else
                        ("" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v).strip()))
    return values.replace("", np.nan)


def missing_codes(book):
    """
    Numeric codes of one variable that mean "no data"

    Parameters
    ----------
    book : dict
        Value code -> label, from the codebook

    Returns
    -------
    list
        Codes to replace with NaN

    """
    codes = []
    for code, label in book.items():
        if code.isdigit() and " ".join(str(label).split()).lower() in MISSING_LABELS:
            codes.append(int(code))
    return codes


def decode_missing(data, codebook, log=print):
    """
    Replace codes that mean "Refused", "Don't know", "Screened out", ... with NaN

    Parameters
    ----------
    data : DataFrame
        Raw values, one column per variable code
    codebook : DataFrame
        Codebook from read_codebook()
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Values with missing codes replaced by NaN

    """
    books = codebook["Codebook"].to_dict()
    unknown, text = [], []
    for column in data.columns:
        as_text = _as_text(data[column])
        if as_text is not None:          # character variable, e.g. a time "23:30"
            # a character variable stores its missing codes as text too ("99999")
            gone = {str(c) for c in missing_codes(books.get(column, {}))}
            data[column] = as_text.where(~as_text.isin(gone), np.nan) if gone else as_text
            text.append(column)
            continue
        if column not in books:
            unknown.append(column)
            continue
        codes = missing_codes(books[column])
        if not codes:
            continue
        values = data[column].to_numpy(copy=True, dtype=float)
        for code in codes:
            values[values == code] = np.nan
        data[column] = values
    if unknown:
        log(f"WARNING: no codebook entry for {len(unknown)} variable(s): "
            f"{', '.join(unknown[:8])} - values are kept as they are")
    if text:
        log(f"{len(text)} variable(s) hold text rather than numbers and are written as they are: "
            f"{', '.join(text[:8])}{' ...' if len(text) > 8 else ''}")
    return data


def apply_sentinels(data, log=print):
    """
    Replace the values that mean something else than the number they are

    Parameters
    ----------
    data : DataFrame
        Values, one column per variable code
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Values with the codes of SENTINELS replaced

    """
    done = []
    for column, replacements in SENTINELS.items():
        if column not in data.columns or data[column].dtype.kind not in "fiub":
            continue
        values = data[column].to_numpy(copy=True, dtype=float)
        changed = 0
        for code, target in replacements.items():
            found = values == code
            changed += int(found.sum())
            values[found] = target
        if changed:
            data[column] = values
            done.append(f"{column} ({changed})")
    if done:
        log(f"Replaced value(s) that mean something else than their number: {', '.join(done)}")
    return data


# the weight columns pynhanes writes, keyed by the NHANES weight they come from
WEIGHT_COLUMN = {"WTINT2YR": "Sample weight (interview)",
                 "WTINTPRP": "Sample weight (interview)",
                 "WTMEC2YR": "Sample weight (exam)", "WTMECPRP": "Sample weight (exam)",
                 "WTPH2YR": "Sample weight (blood draw)",
                 "WTSAF2YR": "Sample weight (fasting)", "WTSAFPRP": "Sample weight (fasting)",
                 "WTDRD1": "Sample weight (dietary)", "WTDRD1PP": "Sample weight (dietary)"}

# the weight and design columns are not analysis variables, so they have no weight
DESIGN_COLUMNS = ("Sample weight", "Survey PSU", "Survey strata")

# Mortality comes from the linked NCHS files, not from an NHANES data file, so no
# data file tells us its weight. Death is an outcome: the weight of a survival
# model is set by its covariates, not by the outcome, and the interview weight is
# the one valid for everyone linked. The follow-up counted from the examination
# exists only for people who were examined, so it takes the exam weight.
MORTALITY_WEIGHT = "Sample weight (interview)"
MORTALITY_WEIGHT_BY_CODE = {"PERMTH_EXM": "Sample weight (exam)"}


def _mortality_linked(survey):
    """True if the public linked mortality file covers this survey

    The same rule the downloader uses to fetch the files: the linkage runs to
    MORTALITY_YEAR, and the pre-pandemic 2017-2020 release is not linked.
    """
    from pynhanes.downloader import MORTALITY_YEAR
    try:
        return survey != "2017-2020" and int(str(survey)[-4:]) <= MORTALITY_YEAR
    except ValueError:
        return False


def weights_dict(mapping, weights=None, availability=None):
    """
    Build the table of which weight column each parsed variable needs

    Rows are pynhanes variable names, columns are surveys, and each cell names one
    of the weight columns written into nhanes_userdata.csv. The answer depends on
    the survey, because NHANES moves things: general health was asked at home in
    2001-2004 and in the examination centre otherwise, and the blood counts moved
    onto their own blood-draw weight in 2021-2023.

    A name built from codes that live in several data files takes the weight of
    the file supplying the most values, since that is where most of the merged
    column comes from. A cell holding a raw NHANES code rather than a column name
    means pynhanes does not ship that weight; add it to your variables file.

    Parameters
    ----------
    mapping : dict
        Variable name -> list of NHANES codes, as read_variables() returns
    weights : DataFrame, optional
        The table of scraper.weights_to_pandas(); the shipped one by default
    availability : DataFrame, optional
        The table of scraper.availability_to_pandas(); the shipped one by default

    Returns
    -------
    DataFrame
        One row per variable, one column per survey

    """
    from pynhanes import scraper

    if weights is None:
        weights = scraper.read_snapshot("weights")
    if availability is None:
        availability = scraper.read_snapshot("availability")
    if weights is None or availability is None:
        raise ValueError("pynhanes ships no weights or availability table; "
                         "pass them, or write them with pynhanes-scraper --weights")

    if "Code" not in availability.columns:
        availability = availability.reset_index()
    surveys = [c for c in weights.columns]
    where = {}
    for code, survey, data_file, valid in zip(availability["Code"], availability["Survey"],
                                              availability["Data File"], availability["Valid"]):
        where.setdefault((code, survey), []).append((data_file, valid))
    seen = {survey for _, survey in where}

    table = {}
    for name, codes in mapping.items():
        if str(name).startswith(DESIGN_COLUMNS):
            continue
        codes = codes if isinstance(codes, list) else [codes]
        if codes and all(c in MORTALITY_CODES for c in codes):
            weight = MORTALITY_WEIGHT_BY_CODE.get(codes[0], MORTALITY_WEIGHT)
            table[name] = {s: weight for s in surveys if s in seen and _mortality_linked(s)}
            continue
        for survey in surveys:
            found = {}
            for code in codes:
                for data_file, valid in where.get((code, survey), []):
                    if data_file not in weights.index:
                        continue
                    weight = weights.at[data_file, survey]
                    if pd.notna(weight):
                        found[weight] = found.get(weight, 0) + (valid or 0)
            if found:
                best = max(found, key=found.get)
                table.setdefault(name, {})[survey] = WEIGHT_COLUMN.get(best, best)

    out = pd.DataFrame(table).T.reindex(columns=surveys)
    out = out.reindex([n for n in mapping if n in out.index])
    out.index.name = "Variable"
    return out


def clean_weights(data, log=print):
    """
    Force the near-zero sample weights of people outside a subsample to zero

    A participant who was interviewed but never examined has an exam weight of
    zero, and one who was examined but did not fast has a zero fasting weight.
    SAS writes those zeros in a form the XPORT reader turns into 5.4e-79, so
    ``weight > 0`` keeps them and ``log(weight)`` returns nonsense.

    Parameters
    ----------
    data : DataFrame
        Values, one column per variable code
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Values with weights below WEIGHT_FLOOR set to zero

    """
    done = []
    for column in data.columns:
        if not str(column).startswith("WT") or data[column].dtype.kind not in "fiub":
            continue
        values = data[column].to_numpy(copy=True, dtype=float)
        found = (values > 0) & (values < WEIGHT_FLOOR)
        if found.any():
            values[found] = 0.0
            data[column] = values
            done.append(f"{column} ({int(found.sum())})")
    if done:
        log(f"Zeroed the weight of people outside the subsample: {', '.join(done)}")
    return data


def recode_binary(data, codebook, third_option=True, log=print):
    """
    Recode Yes/No and Male/Female from 1/2 to 1/0, and map Yes/No variables
    that have a third answer (see THIRD_OPTION)

    Parameters
    ----------
    data : DataFrame
        Values with missing codes already replaced by NaN
    codebook : DataFrame
        Codebook from read_codebook()
    third_option : bool, default True
        Also map Yes/No variables that have a third answer (see THIRD_OPTION),
        whose third answer becomes empty
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Values with binary variables as 1/0

    """
    books = codebook["Codebook"].to_dict()
    if "Recode" in codebook.columns:          # codebook written before 1.0.0
        flags = codebook["Recode"].to_dict()
    else:
        recoded = codebook["Recoded Codebook"].to_dict()
        flags = {code: recodes_to_binary(book, recoded.get(code, {}))
                 for code, book in books.items()}
    recoded = []
    for column in data.columns:
        if data[column].dtype.kind not in "fiub":
            continue                     # character variable
        values = None
        if third_option and column in THIRD_OPTION:
            values = data[column].to_numpy(copy=True, dtype=float)
            mapped = np.full_like(values, np.nan)
            for code, target in THIRD_OPTION[column].items():
                mapped[values == code] = target
            values = mapped
        elif flags.get(column) is True or _is_checkbox_yes_no(books.get(column, {})):
            values = data[column].to_numpy(copy=True, dtype=float)
            values[values == 2] = 0
        if values is not None:
            data[column] = values
            recoded.append(column)
    if recoded:
        log(f"Recoded {len(recoded)} binary variable(s) from 1/2 to 1/0")
    return data


def recodes_to_binary(book, recoded):
    """
    True when the parsed table holds 0 where NHANES publishes 2

    Read off the two dictionaries of the codebook rather than a flag of its
    own: "Recoded Codebook" is what comes out of the parser, so the "No" label
    having moved from code 2 to code 0 is the recoding itself.

    Parameters
    ----------
    book : dict
        Value code -> label as NHANES publishes it ("Codebook")
    recoded : dict
        Value code -> label after parsing ("Recoded Codebook")

    Returns
    -------
    bool

    """
    if not book or not recoded:
        return False
    no = " ".join(str(book.get("2", "")).split())
    return bool(no) and "2" not in recoded and recoded.get("0") == no


def _is_checkbox_yes_no(book):
    """
    True for "Yes (checkbox checked)" / "No (checkbox unchecked)" variables,
    which the codebook does not flag
    """
    if "0" in book or "3" in book:
        return False
    first = " ".join(str(book.get("1", "")).split()).lower()
    second = " ".join(str(book.get("2", "")).split()).lower()
    return first.startswith("yes") and second.startswith("no")


def _is_yes_no(book):
    """
    True when the only answers of a variable are Yes and No

    Male/Female is 1/0 as well after recoding, but the two are not the same
    question asked twice, so those are merged in the usual order instead.

    """
    labels = set()
    for code, label in book.items():
        text = " ".join(str(label).split()).lower()
        if code == "." or text in MISSING_LABELS:
            continue
        labels.add("yes" if text.startswith("yes") else "no" if text.startswith("no") else text)
    return bool(labels) and labels <= {"yes", "no"}


def _is_flag(book):
    """
    True for a variable with a single meaningful value, marked or not marked

    NHANES writes some checkbox questions this way: ARQ020B, ARQ020C and
    ARQ020D are "upper back", "mid back" and "low back", coded 2, 3 and 4 -
    the value names the box, and having a value means yes.

    """
    labels = {code: " ".join(str(label).split()) for code, label in book.items()
              if code != "." and " ".join(str(label).split()).lower() not in MISSING_LABELS}
    return len({t.lower() for t in labels.values()}) == 1 and len(labels) >= 1


def _yes_no_column(data, code, books):
    """
    A Yes/No code as 1/0, or None when it is not a Yes/No question

    A flag ("low back" coded 4) counts as a yes wherever it has a value.

    """
    values = data[code]
    if values.dtype.kind not in "fiub":
        return None
    book = books.get(code, {})
    if _is_flag(book):
        return values.notna().astype(float).where(values.notna(), np.nan)
    if _is_yes_no(book) and values.dropna().isin((0, 1)).all():
        return values
    return None


def _all_yes_no(data, codes, books):
    """
    Yes/No columns of these codes, or None when one of them is not Yes/No
    """
    columns = []
    for code in codes:
        column = _yes_no_column(data, code, books)
        if column is None:
            return None
        columns.append(column)
    return columns


def merge_codes(data, mapping, log=print, codebook=None):
    """
    Merge the variable codes of one variable into a single column

    The first code that has a value for a participant wins, so the order of
    codes in the mapping matters - except for Yes/No variables, where a Yes
    in any of the codes wins. Several codes of a Yes/No variable usually ask
    the same thing in different words ("ever told you had asthma" and "do you
    still have asthma", upper, mid and low back pain), and the answer wanted
    is "yes, at least one of them".

    Parameters
    ----------
    data : DataFrame
        Values, one column per variable code
    mapping : dict
        Variable name -> list of variable codes
    log : callable, default print
        Function to print messages
    codebook : DataFrame or None, default None
        Codebook from read_codebook(), which says which variables are Yes/No;
        None - merge every variable in the order of its codes

    Returns
    -------
    DataFrame
        One column per variable name

    """
    books = codebook["Codebook"].to_dict() if codebook is not None else {}
    columns, missing, any_yes = {}, [], []
    for name, codes in mapping.items():
        present = [c for c in codes if c in data.columns]
        if not present:
            missing.append(name)
            continue
        yes_no = _all_yes_no(data, present, books) if len(present) > 1 and books else None
        if yes_no is not None:
            merged = pd.concat(yes_no, axis=1).max(axis=1, skipna=True)
            any_yes.append(name)
        else:
            merged = data[present[0]]
            for code in present[1:]:
                merged = merged.fillna(data[code])
        columns[name] = merged
    if any_yes:
        log(f"{len(any_yes)} Yes/No variable(s) merged as 'yes in any of the codes': "
            f"{', '.join(any_yes[:6])}{' ...' if len(any_yes) > 6 else ''}")
    if missing:
        log(f"WARNING: no data for {len(missing)} variable(s): {', '.join(missing[:8])}"
            f"{' ...' if len(missing) > 8 else ''} - the data files may not be downloaded")
    return pd.DataFrame(columns, index=data.index)


# ---------------------------------------------------------------------------
# Derived variables
# ---------------------------------------------------------------------------

def _values(raw, codes):
    """
    First non-missing value of several variable codes, as a float array
    """
    present = [c for c in codes if c in raw.columns]
    if not present:
        return None
    merged = raw[present[0]]
    for code in present[1:]:
        merged = merged.fillna(raw[code])
    return merged.to_numpy(copy=True, dtype=float)


# Education of children (DMDEDUC3, the grade they are in) on the adult scale of
# DMDEDUC2: 1 less than 9th grade, 2 9-11th grade, 3 high school, 4 some
# college, 5 college graduate
CHILD_EDUCATION = {**{g: 1 for g in range(0, 9)}, 55: 1, 66: 1,
                   **{g: 2 for g in (9, 10, 11, 12)},
                   13: 3, 14: 3, 15: 4}


def _derive_education(values, raw):
    """
    Education level: the adult answer, filled in with the child answer

    The two use different scales - adults answer in five levels, children name
    the grade they are in - so the child answer is mapped onto the adult scale


    """
    child = _values(raw, ["DMDEDUC3"])
    if child is None:
        return values
    mapped = np.full_like(child, np.nan)
    for grade, level in CHILD_EDUCATION.items():
        mapped[child == grade] = level
    return np.where(np.isnan(values), mapped, values)


# The languages of the ACQ questions: a flag that is set when the language is
# spoken, and scales that run from 1 "only Spanish" to 5 "only English"
LANGUAGE_FLAGS = {"english": ["ACD010A", "ACD011A"], "spanish": ["ACD010B", "ACD011B"],
                  "other": ["ACD010C", "ACD011C"]}
LANGUAGE_SCALES = ["ACQ020", "ACQ030", "ACQ050", "ACQ060", "ACD040"]      # Spanish <-> English
LANGUAGE_SCALE_OTHER = ["ACD110"]                                          # other <-> English


def _derive_language(values, raw):
    """
    Language: 1 English, 2 Spanish, 3 another language

    English wins wherever it appears, because the question is whether the
    participant lives in the language of the survey; Spanish comes next, and
    any other language last.

    """
    def present(codes):
        found = np.zeros(len(raw), dtype=bool)
        for code in codes:
            if code in raw.columns:
                found |= raw[code].notna().to_numpy()
        return found

    def scale(codes, wanted):
        found = np.zeros(len(raw), dtype=bool)
        for code in codes:
            if code in raw.columns:
                values_ = raw[code].to_numpy(dtype=float)
                found |= wanted(values_)
        return found

    if not any(c in raw.columns for c in
               sum(LANGUAGE_FLAGS.values(), []) + LANGUAGE_SCALES + LANGUAGE_SCALE_OTHER):
        return None
    english = (present(LANGUAGE_FLAGS["english"])
               | scale(LANGUAGE_SCALES + LANGUAGE_SCALE_OTHER, lambda v: v >= 3))
    spanish = (present(LANGUAGE_FLAGS["spanish"])
               | scale(LANGUAGE_SCALES, lambda v: (v == 1) | (v == 2)))
    other = (present(LANGUAGE_FLAGS["other"])
             | scale(LANGUAGE_SCALE_OTHER, lambda v: (v == 1) | (v == 2)))
    out = np.full(len(raw), np.nan)
    out[other] = 3
    out[spanish] = 2
    out[english] = 1
    return out


def _derive_average(values, raw, codes):
    """
    Average of the repeated measurements of one variable, e.g. grip strength

    """
    present = [c for c in codes if c in raw.columns]
    if not present:
        return values
    return raw[present].mean(axis=1, skipna=True).to_numpy(copy=True, dtype=float)


def _derive_screen_time(values, raw):
    """
    Hours in front of a television or a computer

    Some surveys ask for the two together (PAQ480, PAD480), others ask
    separately (PAD590/PAQ710 television, PAD600/PAQ715 computer). Where they
    are separate, the hours are added: what is measured is the time spent
    sitting.

    """
    together = _values(raw, ["PAQ480", "PAD480"])
    television = _values(raw, ["PAD590", "PAQ710"])
    computer = _values(raw, ["PAD600", "PAQ715"])
    if television is None and computer is None:
        return values if together is None else _fold_tv(together)
    parts = [_fold_tv(p) for p in (television, computer) if p is not None]
    separate = np.where(np.all([np.isnan(p) for p in parts], axis=0), np.nan,
                        np.nansum(parts, axis=0))
    if together is None:
        return separate
    together = _fold_tv(together)
    return np.where(np.isnan(together), separate, together)


def _derive_smoking(values, raw):
    """
    Smoking status: 0 - never, 1 - quit, 2 - current
    """
    now = _values(raw, ["SMQ040", "SMQ140", "SMQ170"])
    regular = _values(raw, ["SMD030", "SMD130", "SMD160"])
    if now is None or regular is None:
        return None
    values = 2 * values
    values[(values > 0) & (now == 3)] = 1        # smoked, but not any more
    values[(values > 0) & (regular == 0)] = 0    # never smoked regularly
    return values


def _derive_blood_pressure(values, raw, codes):
    """
    Average of the readings, dropped if the participant had food, alcohol,
    coffee or cigarettes within 30 minutes
    """
    present = [c for c in codes if c in raw.columns]
    values = np.nanmean(raw[present].to_numpy(dtype=float), axis=1)
    had = [c for c in ["BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"] if c in raw.columns]
    if had:
        mask = np.nanmax(raw[had].to_numpy(dtype=float) == 1, axis=1).astype(bool)
        values[mask] = np.nan
    return values


def _derive_hospital_stays(values, raw):
    """
    Number of hospital stays: a participant who answered that they were not in
    hospital last year has 0 stays, not a missing value
    """
    been = [c for c in ["HUQ071", "HUQ070", "HUD070"] if c in raw.columns]
    if been:
        answered_no = np.nanmax(raw[been].to_numpy(dtype=float) == 0, axis=1).astype(bool)
        values[np.isnan(values) & answered_no] = 0
    return values


def _derive_unemployment(values):
    """
    Reason for not working: 2 - going to school, 3 - retired, 1 - any other
    """
    out = np.where(np.isfinite(values), 1.0, np.nan)
    out[values == 2] = 2
    out[values == 3] = 3
    return out


def _derive_healthcare(values, raw, codes):
    """
    Number of health care visits last year on one scale: later surveys split
    the answer into more categories (HUQ051), which are folded back into the
    scale of the first surveys (HUQ050) BEFORE the two are merged
    """
    if "HUQ051" in raw.columns:
        alt = raw["HUQ051"].to_numpy(copy=True, dtype=float)
        original = raw["HUQ051"].to_numpy(dtype=float)
        for code, target in {4: 3, 5: 3, 6: 4, 7: 5, 8: 5}.items():
            alt[original == code] = target
        base = (raw["HUQ050"].to_numpy(copy=True, dtype=float)
                if "HUQ050" in raw.columns else np.full_like(alt, np.nan))
        values = np.where(np.isnan(base), alt, base)
    return values


# Exam age in months first (RIDAGEEX 1999-2010, RIDEXAGM 2011+; they never
# co-occur), then screening months. Age is floor of this merge, nothing else.
AGE_MONTH_CODES = ("RIDAGEEX", "RIDEXAGM", "RIDAGEMN")


def _derive_age(values, raw, codes, book, table=None):
    """
    Completed years of age: floor of exam/screening months over 12

    Merges RIDAGEEX, RIDEXAGM, then RIDAGEMN - the three differ by a month at
    most, so which one is used does not matter. From 2011 NHANES publishes the
    age in months only for participants under 20, so everybody else falls back
    to RIDAGEYR, the age in years at screening.
    """
    months = _values(raw, AGE_MONTH_CODES)
    if months is None:
        return values
    return np.where(np.isnan(months), values, np.floor(months / 12.0))


# Variable codes a derived variable reads besides the ones the mapping names,
# keyed like DERIVED by the first code of the variable. Age always reads the
# months so that floor(months / 12) is defined; education reads the children's
# answer, which is on another scale and so must not be merged in
DERIVED_READS = {
    "RIDAGEYR": AGE_MONTH_CODES,
    "DMDEDUC2": ("DMDEDUC3",),
    "SMQ020": ("SMQ040", "SMQ140", "SMQ170", "SMD030", "SMD130", "SMD160"),
    "BPXSY1": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "BPXDI1": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "BPXPLS": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "BPXOSY1": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "BPXODI1": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "BPXOPLS1": ("BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"),
    "PAQ480": ("PAD480", "PAD590", "PAD600", "PAQ710", "PAQ715"),
    "PAD480": ("PAQ480", "PAD590", "PAD600", "PAQ710", "PAQ715"),
    "PAD590": ("PAQ480", "PAD480", "PAD600", "PAQ710", "PAQ715"),
}


def extra_codes_for_derived(mapping):
    """
    Variable codes a derived variable needs beyond those in the mapping

    Parameters
    ----------
    mapping : dict
        Variable name -> list of variable codes

    Returns
    -------
    list
        Codes to read as well, in the order they were first needed

    """
    extra, seen = [], {c for codes in mapping.values() for c in codes}
    for codes in mapping.values():
        for code in DERIVED_READS.get(codes[0] if codes else "", ()):
            if code not in seen:
                extra.append(code)
                seen.add(code)
    return extra


def _survey_labels(values, book):
    """
    Replace the survey number with the years it covers, e.g. 1 -> "1999-2000"
    and 12 -> "2021-2023" (the label of the latest survey reads
    "NHANES August 2021-August 2023 public release")
    """
    labels = {}
    for code, label in book.items():
        if not code.isdigit():
            continue
        years = re.findall(r"(?:19|20)\d{2}", str(label))
        labels[int(code)] = f"{years[0]}-{years[-1]}" if len(years) >= 2 else str(label)
    return np.array([labels.get(int(v), "") if np.isfinite(v) else "" for v in values],
                    dtype=object)


# Derived variables, keyed by the first variable code of the variable.
# Each entry: (description, function(values, raw, codes, book) -> new values)
DERIVED = {
    "RIDAGEYR": ("Age in completed years: floor of exam/screening months / 12 "
                 "(RIDAGEEX, RIDEXAGM, then RIDAGEMN), and the screening age in "
                 "years where NHANES published no months",
                 lambda v, raw, codes, book, table=None: _derive_age(v, raw, codes, book, table)),
    "INDFMPIR": ("Poverty status: 0 - poor, 1 - middle, 2 - rich (family income to poverty ratio "
                 "cut at 2 and 4)",
                 lambda v, raw, codes, book: np.where(np.isfinite(v), np.digitize(v, [2, 4]), np.nan)),
    "SMQ020": ("Smoking status: 0 - never, 1 - quit, 2 - current (needs SMQ040/SMQ140/SMQ170 "
               "and SMD030/SMD130/SMD160)",
               lambda v, raw, codes, book: _derive_smoking(v, raw)),
    "INDFMINC": ("Income brackets of later surveys folded into the first ones (12->4, 13->5, "
                 "14 and 15 -> 11)",
                 lambda v, raw, codes, book: _fold_income(v)),
    "INDHHINC": ("Income brackets of later surveys folded into the first ones",
                 lambda v, raw, codes, book: _fold_income(v)),
    "DMDEDUC2": ("Education level: the adult answer, filled in with the child answer "
                 "(DMDEDUC3) mapped onto the adult scale",
                 lambda v, raw, codes, book: _derive_education(v, raw)),
    "ACD010A": ("Language: 1 English, 2 Spanish, 3 another language; English wins wherever "
                "it appears",
                lambda v, raw, codes, book: _derive_language(v, raw)),
    "MGXH1T1": ("Grip strength of the first hand: the average of the three tests",
                lambda v, raw, codes, book: _derive_average(v, raw, codes)),
    "MGXH2T1": ("Grip strength of the second hand: the average of the three tests",
                lambda v, raw, codes, book: _derive_average(v, raw, codes)),
    "PAQ480": ("Hours in front of a television or computer, the two added where the survey "
               "asks them apart",
               lambda v, raw, codes, book: _derive_screen_time(v, raw)),
    "PAD480": ("Hours in front of a television or computer, the two added where the survey "
               "asks them apart",
               lambda v, raw, codes, book: _derive_screen_time(v, raw)),
    "PAD590": ("Hours in front of a television or computer, the two added where the survey "
               "asks them apart",
               lambda v, raw, codes, book: _derive_screen_time(v, raw)),
    "SDDSRVYR": ("Survey as the years it covers, e.g. '1999-2000' or '2021-2023'",
                 lambda v, raw, codes, book: _survey_labels(v, book)),
    "RIDEXMON": ("Season as text: Winter or Summer",
                 lambda v, raw, codes, book: np.where(v == 1, "Winter",
                                                      np.where(v == 2, "Summer", ""))),
    "DBD090": ("Meals not prepared at home: 'none' (6666) -> 0, more than 22 per week -> 22",
               lambda v, raw, codes, book: _fold_meals(v)),
    "HUQ050": ("Number of health care visits on one scale across surveys",
               lambda v, raw, codes, book: _derive_healthcare(v, raw, codes)),
    "HUD080": ("Number of hospital stays: 0 for participants who answered they were not in "
               "hospital last year",
               lambda v, raw, codes, book: _derive_hospital_stays(v, raw)),
    "OCQ380": ("Reason for not working: 1 - other, 2 - going to school, 3 - retired",
               lambda v, raw, codes, book: _derive_unemployment(v)),
    "BPXSY1": ("Systolic blood pressure averaged over the readings, dropped if the participant "
               "had food, alcohol, coffee or cigarettes within 30 minutes",
               lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "BPXDI1": ("Diastolic blood pressure, averaged and dropped the same way",
               lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "BPXPLS": ("Pulse, averaged and dropped the same way",
               lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "BPXOSY1": ("Systolic blood pressure of the oscillometric device, averaged over its "
                "readings, dropped the same way as the auscultatory one",
                lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "BPXODI1": ("Diastolic blood pressure of the oscillometric device, averaged and dropped "
                "the same way",
                lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "BPXOPLS1": ("Pulse of the oscillometric device, averaged and dropped the same way",
                 lambda v, raw, codes, book: _derive_blood_pressure(v, raw, codes)),
    "PERMTH_INT": ("Months of mortality follow-up converted to years (at least 0.1)",
                   lambda v, raw, codes, book: np.maximum(np.round(v / 12.0, 2), 0.1)),
    "PERMTH_EXM": ("Months of mortality follow-up from the examination converted to years "
                   "(at least 0.1)",
                   lambda v, raw, codes, book: np.maximum(np.round(v / 12.0, 2), 0.1)),
}


def _fold_income(values):
    for old, new in {12: 4, 13: 5, 14: 11, 15: 11}.items():
        values[values == old] = new
    return values


def _fold_meals(values):
    values[values == 6666] = 0
    values[(values > 22) & (values <= 5555)] = 22
    return values


def _fold_tv(values):
    values[(values == 6) | (values == 8)] = 0
    return values


def derive(table, raw, mapping, codebook, log=print):
    """
    Compute derived variables (see DERIVED)

    Parameters
    ----------
    table : DataFrame
        One column per variable name
    raw : DataFrame
        Values of every variable code that was read
    mapping : dict
        Variable name -> list of variable codes
    codebook : DataFrame
        Codebook from read_codebook()
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Table with derived variables replaced

    """
    books = codebook["Codebook"].to_dict()
    done = []
    for name, codes in mapping.items():
        if name not in table.columns or not codes or codes[0] not in DERIVED:
            continue
        description, function = DERIVED[codes[0]]
        if table[name].dtype.kind not in "fiub":
            continue                     # already text, e.g. computed in an earlier run
        values = table[name].to_numpy(copy=True, dtype=float)
        try:
            new = function(values, raw, codes, books.get(codes[0], {}), table)
        except TypeError:
            new = function(values, raw, codes, books.get(codes[0], {}))
        except Exception as e:
            log(f"WARNING: could not compute '{name}': {e.__class__.__name__}: {e}")
            continue
        if new is None:
            log(f"WARNING: '{name}' needs other variables that were not requested, "
                f"left as it is ({description})")
            continue
        table[name] = new
        done.append(name)
    if done:
        log(f"Computed {len(done)} derived variable(s): {', '.join(done[:8])}"
            f"{' ...' if len(done) > 8 else ''}")
    return table


# ---------------------------------------------------------------------------
# Extra columns: computed next to the variables you asked for, not instead
# ---------------------------------------------------------------------------

# 2021-2023 asks the same questions under new codes (HIQ032A-I). They are left
# out on purpose: a survey that is still being published may rename a variable
# and mean something slightly different by it
INSURANCE_CODES = ["HIQ011", "HID010",
                   "HIQ031A", "HIQ031B", "HIQ031C", "HIQ031D", "HIQ031E", "HIQ031F",
                   "HIQ031G", "HIQ031H", "HIQ031I", "HIQ031J",
                   "HID030A", "HID030B", "HID030C", "HID030D", "HID030E", "HIQ260"]
BLOOD_PRESSURE_CODES = {"Blood pressure systolic unadjusted (mm Hg)":
                        ["BPXSY1", "BPXSY2", "BPXSY3", "BPXSY4"],
                        "Blood pressure diastolic unadjusted (mm Hg)":
                        ["BPXDI1", "BPXDI2", "BPXDI3", "BPXDI4"]}
SLEEP_TIME_CODES = {"Sleep bedtime (weekday, min)": "SLQ300",
                    "Sleep wake time (weekday, min)": "SLQ310",
                    "Sleep bedtime (weekend, min)": "SLQ320",
                    "Sleep wake time (weekend, min)": "SLQ330"}


def _any_yes(raw, codes):
    """
    1 where any of these Yes/No codes says yes, 0 where they all say no,
    empty where the participant was asked none of them
    """
    present = [c for c in codes if c in raw.columns]
    if not present:
        return None
    values = raw[present].to_numpy(dtype=float)
    asked = np.isfinite(values).any(axis=1)
    yes = np.nanmax(np.where(np.isfinite(values), values, np.nan), axis=1)
    return np.where(asked, (yes == 1).astype(float), np.nan)


def _mean_of(raw, codes):
    """
    Average of the readings, without dropping anybody
    """
    present = [c for c in codes if c in raw.columns]
    if not present:
        return None
    return np.nanmean(raw[present].to_numpy(dtype=float), axis=1)


def _minutes_after_midnight(raw, code):
    """
    A time written as "23:30" as minutes after midnight
    """
    if code not in raw.columns:
        return None
    text = _as_text(raw[code])
    text = raw[code].astype(str) if text is None else text
    found = text.str.extract(r"^\s*(\d{1,2}):(\d{2})")
    hours = pd.to_numeric(found[0], errors="coerce")
    minutes = pd.to_numeric(found[1], errors="coerce")
    return (60 * hours + minutes).to_numpy(dtype=float)


# Columns added beside the variables that were requested, when the codes they
# need were read. Each entry: (codes it is built from, description, function)
EXTRA = {
    "Insurance medical (any)": (
        INSURANCE_CODES,
        "1 when the participant has any health insurance, private or public",
        lambda raw: _any_yes(raw, INSURANCE_CODES)),
}
for _name, _codes in BLOOD_PRESSURE_CODES.items():
    EXTRA[_name] = (_codes,
                    "Blood pressure averaged over the readings, keeping the participants who "
                    "had food, alcohol, coffee or a cigarette within 30 minutes",
                    (lambda codes: lambda raw: _mean_of(raw, codes))(_codes))
for _name, _code in SLEEP_TIME_CODES.items():
    EXTRA[_name] = ([_code],
                    "The time of day as minutes after midnight, next to the text",
                    (lambda code: lambda raw: _minutes_after_midnight(raw, code))(_code))
del _name, _codes, _code


def add_extra_columns(table, raw, mapping, log=print):
    """
    Add the columns of EXTRA that can be built from the data that was read

    Parameters
    ----------
    table : DataFrame
        One column per variable name
    raw : DataFrame
        Values of every variable code that was read
    mapping : dict
        Variable name -> list of variable codes; the new names are added to it,
        so that they are ordered and given a topic like the others
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Table with the extra columns

    """
    added = []
    for name, (codes, _description, function) in EXTRA.items():
        if name in table.columns or not any(c in raw.columns for c in codes):
            continue
        try:
            values = function(raw)
        except Exception as e:
            log(f"WARNING: could not compute '{name}': {e.__class__.__name__}: {e}")
            continue
        if values is None:
            continue
        table[name] = values
        mapping[name] = [c for c in codes if c in raw.columns]
        added.append(name)
    if added:
        log(f"Added {len(added)} column(s) beside the requested ones: {', '.join(added)}")
    return table


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def order_like(table, path, log=print):
    """
    Order columns like an existing table, so that updating it changes as
    little as possible. Columns the old table does not have are appended,
    columns it has but the new table does not are reported.

    Parameters
    ----------
    table : DataFrame
        Parsed table with a two-level column index
    path : str
        Path of the existing .csv to copy the column order from
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Table with reordered columns

    """
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ValueError(f"Table to copy the column order from not found: '{path}'")
    header = pd.read_csv(path, sep=None, engine="python", index_col=0, header=[0, 1], nrows=0)
    wanted = [c[1] for c in header.columns]
    present = {c[1]: c for c in table.columns}
    order = [present[name] for name in wanted if name in present]
    added = [c for c in table.columns if c[1] not in set(wanted)]
    dropped = [name for name in wanted if name not in present]
    if added:
        log(f"{len(added)} variable(s) not in {os.path.basename(path)} are appended at the end: "
            f"{', '.join(c[1] for c in added[:8])}{' ...' if len(added) > 8 else ''}")
    if dropped:
        log(f"WARNING: {len(dropped)} variable(s) of {os.path.basename(path)} are missing here: "
            f"{', '.join(dropped[:8])}{' ...' if len(dropped) > 8 else ''}")
    return table[order + added]


def order_columns_by_code(data, codes, codebook):
    """
    Order the variable codes as they appear in the codebook and give the
    columns two levels: data file and variable code

    Parameters
    ----------
    data : DataFrame
        One column per variable code
    codes : list
        Requested variable codes
    codebook : DataFrame
        Codebook from read_codebook()

    Returns
    -------
    DataFrame
        Table with a two-level column index

    """
    order = [c for c in codebook.index if c in data.columns and c in set(codes)]
    order += [c for c in data.columns if c not in order]
    data = data[order]
    files = [codebook.loc[c, "Category"] if c in codebook.index else "Other" for c in order]
    data.columns = pd.MultiIndex.from_arrays([files, order])
    return data


def order_columns(table, mapping, codebook):
    """
    Order columns by topic and give them two levels: topic and variable name

    Parameters
    ----------
    table : DataFrame
        One column per variable name
    mapping : dict
        Variable name -> list of variable codes
    codebook : DataFrame
        Codebook from read_codebook()

    Returns
    -------
    DataFrame
        Table with a two-level column index

    """
    topics = {}
    for name, codes in mapping.items():
        if name not in table.columns:
            continue
        code = next((c for c in codes if c in codebook.index), None)
        topic = codebook.loc[code, "Combined Category"] if code is not None else ""
        topics[name] = topic if isinstance(topic, str) and topic else "Other"
    order, seen = [], []
    for name in mapping:
        if name in topics and topics[name] not in seen:
            seen.append(topics[name])
    for topic in seen:
        order += [n for n in mapping if topics.get(n) == topic]
    table = table[order]
    table.columns = pd.MultiIndex.from_arrays([[topics[n] for n in order], order])
    return table


def parse(input_folder="XPT", output="CSV/nhanes_userdata.csv",
          codebook="CSV/nhanes_codebook.csv", variables="CSV/nhanes_variables.json",
          layout="names", derived=None, recode=True, merge_ambiguous=False,
          separator=None, like=None, workers=None, prepandemic=False, log=print):
    """
    Parse NHANES data files into one table and write it to .csv

    Parameters
    ----------
    input_folder : str, default "XPT"
        Folder with .xpt and mortality .dat files
    output : str or None, default "CSV/nhanes_userdata.csv"
        Path to the output .csv; None - do not write a file
    codebook : str or DataFrame, default "CSV/nhanes_codebook.csv"
        Codebook of pynhanes-scraper
    variables : str, list or dict, default "CSV/nhanes_variables.json"
        Requested variables, see read_variables()
    layout : str, default "names"
        "names" - one column per variable name, columns (topic, name), codes
        of one variable merged and derived variables computed: what
        nhanes_userdata.csv holds and what pynhanes.NhanesLoader expects.
        "codes" - one column per NHANES variable code, columns (data file,
        code), nothing merged and no derived variables: the layout of
        nhanes_userdata.csv as produced before 1.0.0
    derived : bool or None, default None
        Compute derived variables (see DERIVED); None - only in layout "names"
    recode : bool, default True
        Recode Yes/No and Male/Female from 1/2 to 1/0
    merge_ambiguous : bool, default False
        Merge names that belong to several variable codes instead of refusing
        (layout "names" only; in layout "codes" every code is its own column)
    separator : str or None, default None
        Column separator of the output .csv; None - ";", as in every other
        .csv pynhanes writes
    like : str or None, default None
        Path of an existing table whose column order is kept, so that
        updating it shows only the values that really changed
    workers : int or None, default None
        Threads reading data files in parallel; None - WORKERS, 1 - one
        file after another
    prepandemic : bool, default False
        Read the 2017-March 2020 pre-pandemic files (P_*) instead of the
        2017-2018 ones. They hold the same participants renumbered, plus the
        2019-March 2020 part, and carry their own sample weights
    log : callable, default print
        Function to print messages

    Returns
    -------
    DataFrame
        Parsed table, one row per participant

    """
    if layout not in LAYOUTS:
        raise ValueError(f"Unknown layout '{layout}'. Use one of: {', '.join(LAYOUTS)}.")
    derived = (layout == "names") if derived is None else derived
    separator = SEPARATORS[layout] if separator is None else separator
    book = read_codebook(codebook, log) if isinstance(codebook, str) else codebook
    mapping = resolve_variables(variables, book, merge_ambiguous or layout == "codes", log,
                                warn_ambiguous=layout != "codes")
    codes = [c for codes in mapping.values() for c in codes]
    if derived:
        codes = codes + extra_codes_for_derived(mapping)
    log(f"Parsing {len(mapping)} variables ({len(set(codes))} variable codes) from {input_folder}")

    needed = data_files_of(codes, book)
    raw = read_data(input_folder, codes, log, data_files=needed, workers=workers,
                    prepandemic=prepandemic)
    raw = decode_missing(raw, book, log)
    raw = apply_sentinels(raw, log)
    raw = clean_weights(raw, log)
    if recode:
        raw = recode_binary(raw, book, third_option=True, log=log)
    if layout == "codes":
        table = raw
        if derived:
            table = derive(table, raw, {c: [c] for c in raw.columns}, book, log)
        table = order_columns_by_code(table, codes, book)
    else:
        table = merge_codes(raw, mapping, log, codebook=book)
        if derived:
            table = derive(table, raw, mapping, book, log)
            table = add_extra_columns(table, raw, mapping, log)
        table = order_columns(table, mapping, book)
    if like:
        table = order_like(table, like, log)
    table = table.apply(lambda c: c.round(5) if c.dtype.kind == "f" else c)
    table.index.name = "SEQN"
    # the corner cell of the header says how the table was made
    table.columns = table.columns.set_names(
        [provenance(layout, recode, derived, prepandemic)] + [None] * (table.columns.nlevels - 1))
    if output:
        path = os.path.expanduser(output)
        folder = os.path.dirname(os.path.abspath(path))
        if folder:
            os.makedirs(folder, exist_ok=True)
        table.to_csv(path, sep=separator)
        log(f"Wrote {table.shape[0]} participants x {table.shape[1]} variables to {output}")
    return table


def provenance(layout="names", recode=True, derived=True, prepandemic=False):
    """
    One line saying how a parsed table was made

    It goes into the corner cell of the .csv - the first cell of the first
    header row, which pandas leaves empty - so that the table says what it
    holds even when it has been copied away from any folder it came with.
    Reading the file the documented way is unaffected, and the line comes
    back as `table.columns.names[0]`.

    Parameters
    ----------
    layout : str, default "names"
        Layout the table was written in
    recode : bool, default True
        Yes/No was recoded from 1/2 to 1/0
    derived : bool, default True
        Derived variables were computed
    prepandemic : bool, default False
        The 2017-March 2020 files were read instead of the 2017-2018 ones

    Returns
    -------
    str

    Example
    -------
    >>> pynhanes.parser.provenance()
    'pynhanes 1.0.0 | names | recoded | derived | 2026-09-24'

    """
    from datetime import date
    parts = ["pynhanes " + _version(), layout,
             "recoded" if recode else "not recoded",
             "derived" if derived else "not derived"]
    if prepandemic:
        parts.append("prepandemic")
    parts.append(date.today().isoformat())
    return " | ".join(parts)


def read_provenance(table):
    """
    The provenance line of a parsed table, or None if it carries none

    Parameters
    ----------
    table : DataFrame
        Table read with pd.read_csv(path, sep=";", index_col=0, header=[0, 1])

    Returns
    -------
    str or None

    """
    names = list(getattr(table.columns, "names", []) or [])
    stamp = names[0] if names else None
    return stamp if stamp and str(stamp).startswith("pynhanes") else None


def _version():
    try:
        from importlib.metadata import version
        return version("pynhanes")
    except Exception:                          # pragma: no cover - not installed
        return "unknown"


def parse_plan(input_folder="XPT", codebook="CSV/nhanes_codebook.csv",
               variables="CSV/nhanes_variables.json", merge_ambiguous=False,
               layout="names", prepandemic=False, log=print):
    """
    Report what would be parsed, without reading the data files

    Returns
    -------
    dict
        Variables, data files present and missing, and participants per file

    """
    book = read_codebook(codebook, log) if isinstance(codebook, str) else codebook
    mapping = resolve_variables(variables, book, merge_ambiguous or layout == "codes", log,
                                warn_ambiguous=layout != "codes")
    codes = {c for codes in mapping.values() for c in codes}
    if layout == "names":
        codes.update(extra_codes_for_derived(mapping))
    needed = {}
    for code in codes:
        if code in book.index:
            needed.setdefault(book.loc[code, "Category"], []).append(code)
    available = choose_prepandemic(local_data_files(input_folder), prepandemic, log)
    present = {k: len(available[k]) for k in sorted(needed) if k in available and available[k]}
    missing = sorted(k for k in needed if k not in available)
    return {"input": os.path.abspath(os.path.expanduser(input_folder)), "layout": layout,
            "columns": len(codes) if layout == "codes" else len(mapping),
            "variables": len(mapping), "variable_codes": len(codes),
            "data_files_needed": len(needed), "data_files_present": present,
            "data_files_missing": missing,
            "files_to_read": sum(present.values()),
            "derived": sorted(n for n, c in mapping.items() if c and c[0] in DERIVED)
            if layout == "names" else []}


def _format_plan(plan):
    lines = [f"NHANES parsing from {plan['input']}",
             f"  {plan['variables']} variables ({plan['variable_codes']} variable codes) "
             f"-> {plan['columns']} columns, layout '{plan['layout']}'",
             "",
             "DATA FILES",
             f"  {len(plan['data_files_present'])} of {plan['data_files_needed']} data files "
             f"present, {plan['files_to_read']} files to read"]
    if plan["data_files_missing"]:
        lines.append(f"  missing: {', '.join(plan['data_files_missing'][:12])}"
                     f"{' ...' if len(plan['data_files_missing']) > 12 else ''}")
        lines.append(f"  --> download them with: pynhanes-downloader -d "
                     f"{','.join(plan['data_files_missing'][:12])}")
    else:
        lines.append("  --> nothing missing")
    lines += ["", "DERIVED VARIABLES",
              f"  {len(plan['derived'])}: {', '.join(plan['derived'][:10])}"
              f"{' ...' if len(plan['derived']) > 10 else ''}" if plan["derived"]
              else f"  none in layout '{plan['layout']}'"
                   f"{' (add --derived)' if plan['layout'] == 'codes' else ''}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

EPILOG = """\
examples:
  pynhanes-parser -n                            what would be parsed, parse nothing
  pynhanes-parser                               XPT -> CSV/nhanes_userdata.csv
  pynhanes-parser -i ~/data/NHANES/XPT -o ~/data/NHANES/CSV/nhanes_userdata.csv
  pynhanes-parser -v "Age,Gender,BMI (kg/m2)"   a few variables by name
  pynhanes-parser --write-variables CSV/variables.json    template to curate

notes:
  Variables are named by the codebook column "Combined Name", and one name can
  belong to several NHANES variable codes (surveys rename variables). The parser
  merges them in the order they are listed, first non-missing value winning.
  A name that belongs to codes of different people or age groups - "Gender" is
  both the participant (RIAGENDR) and the household reference person (DMDHRGND) -
  is refused; write a template with --write-variables, keep the code you want,
  and pass that file with -v.

  Values that mean Refused / Don't know / Screened out become empty, Yes/No and
  Male/Female become 1/0, and derived variables (smoking status, blood pressure,
  poverty, mortality follow-up in years, ...) are computed. The documentation explains
  every step, and how one codebook covers all surveys.

for AI coding assistants:
  Run with -n first: it lists the data files that are missing and prints the
  pynhanes-downloader command that fetches them. Show that to the user before
  downloading anything. Use -j/--json for machine-readable output.
  Exit codes: 0 ok, 1 error.
"""


def main(argv=None):
    """
    Command line entry point of pynhanes-parser
    """
    parser = argparse.ArgumentParser(
        prog="pynhanes-parser",
        description="Parse NHANES data files into one table, decoded with the codebook.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", default="XPT",
                        help="folder with .xpt and mortality .dat files (default: %(default)s)")
    parser.add_argument("-o", "--output", default="CSV/nhanes_userdata.csv",
                        help="output .csv, folders are created (default: %(default)s)")
    parser.add_argument("-b", "--codebook", default="CSV/nhanes_codebook.csv",
                        help="codebook of pynhanes-scraper (default: %(default)s)")
    parser.add_argument("-v", "--variables", default="CSV/nhanes_variables.json",
                        help="path to a .json of variable names, or a comma-separated list "
                             "(default: %(default)s)")
    parser.add_argument("--write-variables", metavar="FILE", default=None,
                        help="write the resolved name -> codes mapping to FILE and exit")
    parser.add_argument("--weights-dict", dest="weights_dict", metavar="FILE", default=None,
                        help="where to write the table of which weight column each parsed "
                             "variable needs (default: nhanes_weights_dict.csv next to the "
                             "output). It is written together with the parsed data, because the "
                             "two belong together: the data carries the weights, this says which "
                             "one each variable needs")
    parser.add_argument("--no-weights-dict", dest="no_weights_dict", action="store_true",
                        help="do not write nhanes_weights_dict.csv beside the parsed data")
    parser.add_argument("-l", "--layout", choices=LAYOUTS, default="names",
                        help="'names' - one column per variable name, (topic, name), codes merged "
                             "and derived variables computed, what nhanes_userdata.csv holds and "
                             "what pynhanes.NhanesLoader expects; 'codes' - one column per NHANES "
                             "variable code, (data file, code), nothing merged or derived "
                             "(default: %(default)s)")
    parser.add_argument("--derived", action="store_true",
                        help="compute derived variables also in layout 'codes'")
    parser.add_argument("--like", metavar="FILE", default=None,
                        help="keep the column order of an existing table, so that updating it "
                             "changes as little as possible")
    parser.add_argument("--merge-ambiguous", action="store_true",
                        help="merge names that belong to several variable codes")
    parser.add_argument("--no-derived", action="store_true", help="do not compute derived variables")
    parser.add_argument("--no-recode", action="store_true", help="keep Yes/No as 1/2")
    parser.add_argument("--prepandemic", action="store_true",
                        help="read the 2017-March 2020 pre-pandemic files (P_*) instead of the "
                             "2017-2018 ones: the same participants renumbered, plus the "
                             "2019-March 2020 part, with their own sample weights")
    parser.add_argument("-w", "--workers", type=int, default=None,
                        help=f"data files read in parallel threads (default: {WORKERS} here, "
                             f"1 reads them one after another)")
    parser.add_argument("-n", "--noload", action="store_true",
                        help="show what would be parsed and exit")
    parser.add_argument("-j", "--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    quiet = args.json
    log = (lambda *a: print(*a, file=sys.stderr, flush=True)) if quiet else \
          (lambda *a: print(*a, flush=True))
    try:
        if args.write_variables:
            book = read_codebook(args.codebook)
            mapping = resolve_variables(args.variables, book, merge_ambiguous=True, log=lambda *a: None)
            write_variables(mapping, args.write_variables, book, log)
            return 0
        if args.noload:
            plan = parse_plan(args.input, args.codebook, args.variables, args.merge_ambiguous,
                              args.layout, args.prepandemic, log)
            log(_format_plan(plan))
            if quiet:
                print(json.dumps(plan, indent=1, default=str), flush=True)
            return 0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            derived = None if not args.derived else True
            if args.no_derived:
                derived = False
            table = parse(args.input, args.output, args.codebook, args.variables,
                          layout=args.layout, derived=derived, recode=not args.no_recode,
                          merge_ambiguous=args.merge_ambiguous, like=args.like,
                          workers=args.workers, prepandemic=args.prepandemic, log=log)
        extra = {}
        if not args.no_weights_dict:
            folder = os.path.dirname(os.path.abspath(os.path.expanduser(args.output)))
            path = os.path.expanduser(args.weights_dict) if args.weights_dict else \
                os.path.join(folder, "nhanes_weights_dict.csv")
            try:
                mapping = read_variables(args.variables, log=None)
                lookup = weights_dict(mapping)
                # stamp the corner cell, the way a parsed table is stamped, so the
                # file still says where it came from once it is copied away
                from datetime import date
                lookup.index.name = (f"pynhanes {_version()} | weights dict | "
                                     f"{date.today().isoformat()}")
                os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
                lookup.to_csv(path, sep=";")
                log(f"Wrote the weight of {len(lookup)} variables to {path}")
                extra["weights_dict"] = path
            except (ValueError, KeyError) as e:
                log(f"Could not write {path}: {e}")
        if quiet:
            print(json.dumps({"output": args.output, "participants": int(table.shape[0]),
                              "variables": int(table.shape[1]), **extra}, indent=1), flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as e:
        log(f"ERROR: {e}")
        return 1
    except KeyboardInterrupt:
        log("Interrupted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
