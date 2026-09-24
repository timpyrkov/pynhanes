#!/usr/bin/env python
# -*- coding: utf8 -*-

"""
Minute-level accelerometry of NHANES.

Two eras, and nothing else: PAXRAW in 2003-2006 (a hip monitor, one intensity
count per minute, plus steps in 2005-2006) and the PAXMIN family in 2011-2014
(a wrist monitor, MIMS triaxial units, ambient light and a wake/sleep/non-wear
label per minute). pynhanes reads only the minute-level files; the hourly
(PAXHR) and daily (PAXDAY) aggregates NHANES also publishes are not parsed,
except for the two small companion files the minute data needs:
PAXDAY for the time each first day started and PAXHD for the monitor status.

Every array is laid out as one row per participant and one column per minute,
the first column being midnight of the first day of wear.
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor


# Minute status, the NHANES codes of PAXPREDM plus 0 for a minute with no data
STATUS = {0: "Missing", 1: "Wake wear", 2: "Sleep wear", 3: "Non-wear", 4: "Unknown"}
# The same, as it is stored in the .npz files under the key "Status keys"
STATUS_KEYS = [STATUS[i] for i in sorted(STATUS)]
# 2003-2006 has no wake/sleep/non-wear prediction, so a minute that holds data
# is "Unknown" rather than "Wake wear"
STATUS_DATA = 4
STATUS_MISSING = 0

# Rows read from an .xpt at a time. 2 million rows of PAXMIN is ~0.25 GB
CHUNK = 2000000
# Files of one era read in parallel threads
WORKERS = 2

# A day counts as usable if it holds at least this many minutes above the
# threshold below - non-zero for a single-axis measurement, 30 MIMS units for
# the triaxial one
QUALIFY_MINUTES = 30
QUALIFY_TRIAX = 30.0

MINUTES_PER_DAY = 1440
PAXRAW_DAYS = 7
PAXMIN_DAYS = 9

PAXRAW_FILES = ["PAXRAW_C.XPT", "PAXRAW_D.XPT"]
PAXMIN_FILES = ["PAXMIN_G.XPT", "PAXMIN_H.XPT"]
PAXDAY_FILES = ["PAXDAY_G.XPT", "PAXDAY_H.XPT"]
PAXHD_FILES = ["PAXHD_G.XPT", "PAXHD_H.XPT"]
SURVEY_OF_FILE = {"PAXRAW_C": "2003-2004", "PAXRAW_D": "2005-2006",
                  "PAXMIN_G": "2011-2012", "PAXMIN_H": "2013-2014",
                  "PAXDAY_G": "2011-2012", "PAXDAY_H": "2013-2014",
                  "PAXHD_G": "2011-2012", "PAXHD_H": "2013-2014"}

# Columns of nhanes_activity.csv, in this order
ACTIVITY_COLUMNS = ["SEQN", "Survey", "Data File", "Days with counts", "Days with steps",
                    "Days with triax", "Steps clipped"]

EPILOG = """
Reads the minute-level accelerometry .xpt files and writes one .npz per
measurement, plus nhanes_activity.csv with one row per participant.

  2003-2006  PAXRAW_C.XPT, PAXRAW_D.XPT
             -> nhanes_counts.npz   activity counts, 7 days
             -> nhanes_steps.npz    step counts, 7 days (2005-2006 only)
  2011-2014  PAXMIN_G.XPT, PAXMIN_H.XPT (+ PAXDAY_*, PAXHD_*)
             -> nhanes_triax_full.npz   triaxial, light and status, 9 days
             -> nhanes_triax.npz        the best 7 days of those, rolled

Download the files first:

  pynhanes-downloader -a 2003,2005 -o XPT     # PAXRAW, 0.8 GB zipped
  pynhanes-downloader -a 2011,2013 -o XPT     # PAXMIN and companions, 16.3 GB

Every 7-day array is rolled so that column 0 is Monday 00:00. The 9-day array
is left in calendar order: rolling wraps the end back to the front, which is
harmless over exactly one week but would put the 8th and 9th day before the
1st. All arrays of one participant are always rolled by the same number of
minutes, so they stay aligned with each other.
"""


def decode_status(status=None):
    """
    Label of a minute status code

    Parameters
    ----------
    status : int, array-like or None
        Status code(s) of the "status" array. None returns the whole table

    Returns
    -------
    str, numpy.ndarray or dict
        The label, an array of labels, or the code -> label table

    Example
    -------
    >>> pynhanes.activity.decode_status(2)
    'Sleep wear'
    >>> pynhanes.activity.decode_status()
    {0: 'Missing', 1: 'Wake wear', 2: 'Sleep wear', 3: 'Non-wear', 4: 'Unknown'}

    """
    if status is None:
        return dict(STATUS)
    if np.isscalar(status):
        return STATUS.get(int(status), "Unknown")
    codes = np.asarray(status)
    labels = np.array(STATUS_KEYS, dtype=object)
    flat = np.clip(codes.astype(int), 0, len(STATUS_KEYS) - 1)
    return labels[flat]


def weekday_to_iso(weekdays):
    """
    Change weekdays to ISO format numbering

    Original: 0 - Missing, 1 - Sunday, 2 - Monday, ... 7 - Saturday
    New:      0 - Missing, 1 - Monday, 2 - Tuesday, ... 7 - Sunday

    """
    weekdays = np.asarray(weekdays)
    mask = weekdays == 0
    weekdays = (weekdays + 5) % 7 + 1
    weekdays[mask] = 0
    return weekdays


def weekday_refill(weekdays):
    """
    Fill missing weekday id

    The weekday of the first day is read from the first minute that has one,
    then every day of the record is numbered from it. A row without a single
    weekday is left as zeros, so the output always has as many rows as the
    input and never falls out of step with the other arrays.

    """
    weekdays = np.asarray(weekdays)
    nrow, ncol = weekdays.shape
    nday = ncol // MINUTES_PER_DAY
    known = weekdays > 0
    has = known.any(axis=1)
    first = np.argmax(known, axis=1)
    value = weekdays[np.arange(nrow), first].astype(int)
    # weekday of day 1, counting back over the days before the first known minute
    day_one = (value - 1 - first // MINUTES_PER_DAY) % 7 + 1
    full = (np.arange(nday)[None, :] + day_one[:, None] - 1) % 7 + 1
    full = np.repeat(full, MINUTES_PER_DAY, axis=1).astype(np.uint8)
    full[~has] = 0
    return full


def qualifying_days(values, threshold=0.0, minutes=QUALIFY_MINUTES):
    """
    Number of days of each participant that hold enough measured minutes

    A day qualifies if at least `minutes` of its 1440 minutes are above
    `threshold` (strictly above 0 when the threshold is 0).

    Parameters
    ----------
    values : ndarray (nuser, nday * 1440)
        Minute-level array
    threshold : float, default 0.0
        Minutes strictly above this value count for 0, at or above it otherwise
    minutes : int, default 30
        Minutes a day needs to qualify

    Returns
    -------
    ndarray (nuser,) of int
        Qualifying days per participant

    """
    values = np.asarray(values)
    nrow = values.shape[0]
    counted = np.zeros(nrow, dtype=np.int64)
    # one participant block at a time: the float16 arrays are large and
    # comparing them in one go would double the memory
    for i in range(0, nrow, 512):
        block = values[i:i + 512].astype(np.float32)
        block = block.reshape(block.shape[0], -1, MINUTES_PER_DAY)
        good = block > 0 if threshold == 0 else block >= threshold
        counted[i:i + 512] = (good.sum(axis=2) >= minutes).sum(axis=1)
    return counted


def _bytes_to_uint8(values):
    """Single-character byte column ('1' ... '9', blank) to uint8, blank -> 0"""
    raw = np.frombuffer(np.asarray(values).astype("S1").tobytes(), dtype=np.uint8)
    out = np.where((raw >= 48) & (raw <= 57), raw - 48, 0)
    return out.astype(np.uint8)


def _found(input_folder, wanted):
    """Names of `wanted` present in the folder, in the case they have on disk"""
    listing = {f.upper(): f for f in sorted(os.listdir(input_folder))}
    return [listing[w] for w in wanted if w in listing]


def _chunks(path, chunksize=CHUNK):
    """Read an .xpt in chunks of rows"""
    with pd.read_sas(path, format="xport", chunksize=chunksize, iterator=True) as reader:
        for chunk in reader:
            yield chunk


def _roll_rows(arrays, shift):
    """Roll every array by the same per-row number of minutes, in place"""
    for row, step in enumerate(shift):
        if step:
            for array in arrays:
                array[row] = np.roll(array[row], step)


def _save(path, arrays):
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    np.savez_compressed(path, **arrays)


def paxraw_parser(input_folder, output_folder, clip_steps=True, iso_weekday=True,
                  roll_to_monday=True, chunksize=CHUNK, workers=WORKERS, log=print):
    """
    Parse activity of 2003-2004 and 2005-2006.
    Counts to "nhanes_counts.npz", steps to "nhanes_steps.npz"

    The files are read in chunks and scattered straight into arrays that are
    allocated once, so the whole era needs about 0.8 GB of memory.

    Notes
    -----
    - "userid" is int64
    - "counts" is np.float16 (exact only up to 2048, see the docs)
    - "steps" is np.uint16, or np.uint8 if clipped to 0-255
    - "status" is np.uint8 (0 - Missing, 4 - Unknown; 2003-2006 has no
      wake/sleep/non-wear prediction, so a minute either holds data or not)
    - "weekday" is np.uint8 (if ISO: 0 - Missing, 1 - Mon ... 7 - Sun)
    - no NaN; no log-scale

    Parameters
    ----------
    input_folder : str
        Path to folder containing "PAXRAW_C.XPT" and "PAXRAW_D.XPT"
    output_folder : str
        Path to folder to output "nhanes_counts.npz" and "nhanes_steps.npz"
    clip_steps : bool, default True
        Flag to clip steps/min from range 0-32767 to 0-255
    iso_weekday : bool, default True
        Flag to renumber days from 1 - Sunday to 1 - Monday
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is always the first day
    chunksize : int, default 2000000
        Rows read at a time
    workers : int, default 2
        Files read in parallel threads

    Returns
    -------
    pandas.DataFrame or None
        One row per participant, the part of nhanes_activity.csv this era
        contributes. None if no input file was found

    Example
    -------
    >>> pynhanes.activity.paxraw_parser("./XPT", "./NPZ")

    """
    input_folder = os.path.expanduser(input_folder)
    output_folder = os.path.expanduser(output_folder)
    fnames = _found(input_folder, PAXRAW_FILES)
    if len(fnames) == 0:
        log(f"ERROR! No PAXRAW_C.XPT or PAXRAW_D.XPT found in '{input_folder}'")
        log("Download them with: pynhanes-downloader -a 2003,2005 -o <folder>")
        log("  2003-2004 PAXRAW_C.zip 0.4 GB, 2.6 GB unzipped")
        log("  2005-2006 PAXRAW_D.zip 0.4 GB, 2.9 GB unzipped")
        return None
    paths = [os.path.join(input_folder, f) for f in fnames]
    ncol = PAXRAW_DAYS * MINUTES_PER_DAY

    # Pass 1: who is in the files. The arrays are allocated once from this list
    def collect(path):
        found = set()
        for chunk in _chunks(path, chunksize):
            found.update(chunk["SEQN"].values.astype(np.int64).tolist())
        return found
    log(f"Reading {len(paths)} file(s) of PAXRAW, pass 1 of 2 (participants)...")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        found = list(pool.map(collect, paths))
    userlist = np.array(sorted(set().union(*found)), dtype=np.int64)
    nuser = len(userlist)
    log(f"  {nuser} participants, {ncol} minutes each")

    counts = np.zeros((nuser, ncol), dtype=np.float16)
    steps = np.zeros((nuser, ncol), dtype=np.uint16)
    status = np.zeros((nuser, ncol), dtype=np.uint8)
    weekday = np.zeros((nuser, ncol), dtype=np.uint8)
    clipped = np.zeros(nuser, dtype=np.int64)
    has_steps = np.zeros(nuser, dtype=bool)
    source = np.zeros(nuser, dtype=np.int8)

    # Pass 2: scatter every chunk into the rows it belongs to
    def fill(index_path):
        index, path = index_path
        for chunk in _chunks(path, chunksize):
            seqn = chunk["SEQN"].values.astype(np.int64)
            row = np.searchsorted(userlist, seqn)
            col = chunk["PAXN"].values.astype(np.int64) - 1
            keep = (col >= 0) & (col < ncol)
            row, col = row[keep], col[keep]
            counts[row, col] = chunk["PAXINTEN"].values[keep]
            status[row, col] = STATUS_DATA
            weekday[row, col] = np.round(chunk["PAXDAY"].values[keep]).astype(np.uint8)
            source[row] = index
            if "PAXSTEP" in chunk.columns:
                raw = chunk["PAXSTEP"].values[keep]
                np.add.at(clipped, row, raw > 255)
                has_steps[row] = True
                steps[row, col] = np.clip(raw, 0, 255) if clip_steps else raw
        return path
    log(f"Reading {len(paths)} file(s) of PAXRAW, pass 2 of 2 (minutes)...")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(fill, list(enumerate(paths))))

    # Weekdays: number every day from the first one that is known
    iso = weekday_refill(weekday_to_iso(weekday))
    weekday = iso if iso_weekday else weekday_refill(weekday)

    # Days that hold enough measured minutes, counted before any roll
    days_counts = qualifying_days(counts)
    days_steps = qualifying_days(steps)

    # Roll so that column 0 is Monday 00:00. Every array of a participant is
    # rolled by the same number of minutes, so they stay aligned
    roll = np.zeros(nuser, dtype=np.int64)
    if roll_to_monday:
        roll = MINUTES_PER_DAY * (iso[:, 0].astype(np.int64) - 1)
        roll[iso[:, 0] == 0] = 0
        _roll_rows([counts, steps, status, weekday], roll)

    if clip_steps:
        steps = steps.astype(np.uint8)

    _save(os.path.join(output_folder, "nhanes_counts.npz"), {
        "userid": userlist,
        "counts": counts,
        "weekday": weekday,
        "status": status,
        "Rolled by index": roll,
        "ISO weekday": iso_weekday,
        "Status keys": np.array(STATUS_KEYS),
    })
    # Steps exist only where the monitor reported them (2005-2006)
    mask = np.max(steps, axis=1) > 0
    _save(os.path.join(output_folder, "nhanes_steps.npz"), {
        "userid": userlist[mask],
        "steps": steps[mask],
        "weekday": weekday[mask],
        "status": status[mask],
        "Rolled by index": roll[mask],
        "ISO weekday": iso_weekday,
        "Steps clipped to 0-255": clip_steps,
        "Status keys": np.array(STATUS_KEYS),
    })
    log(f"Saved 'nhanes_counts.npz' ({nuser} participants) and "
        f"'nhanes_steps.npz' ({int(mask.sum())} participants) to '{output_folder}'")

    stems = [os.path.splitext(f)[0].upper() for f in fnames]
    table = pd.DataFrame({
        "SEQN": userlist,
        "Survey": [SURVEY_OF_FILE.get(stems[i], "") for i in source],
        "Data File": [stems[i].split("_")[0] for i in source],
        "Days with counts": days_counts.astype(float),
        "Days with steps": np.where(mask, days_steps, np.nan),
        "Days with triax": np.nan,
        "Steps clipped": np.where(has_steps, clipped, np.nan),
    })
    return table


def paxmin_parser(input_folder, output_folder, iso_weekday=True, roll_to_monday=True,
                  chunksize=CHUNK, workers=WORKERS, log=print):
    """
    Parse activity of 2011-2012 and 2013-2014.
    Full data to "nhanes_triax_full.npz", weekly data to "nhanes_triax.npz"

    The files are read in chunks and scattered straight into arrays that are
    allocated once, so the whole era needs about 1.5 GB of memory.

    Notes
    -----
    - "userid" is int64
    - "triax" is np.float16 (exact only up to 2048, see the docs)
    - "lumin" is np.float16
    - "status" is np.uint8 (0 - Missing, 1 - Wake wear, 2 - Sleep wear,
      3 - Non-wear, 4 - Unknown)
    - "weekday" is np.uint8 (if ISO: 0 - Missing, 1 - Mon ... 7 - Sun)
    - no NaN; no log-scale

    A triaxial value of -0.01 is the NHANES code for "value could not be
    computed". Those minutes are stored as 0 and marked Missing in "status".

    Parameters
    ----------
    input_folder : str
        Path to folder with "PAXMIN_G.XPT", "PAXMIN_H.XPT", "PAXDAY_G.XPT",
        "PAXDAY_H.XPT" and, if available, "PAXHD_G.XPT" and "PAXHD_H.XPT"
    output_folder : str
        Path to folder to output "nhanes_triax.npz" and "nhanes_triax_full.npz"
    iso_weekday : bool, default True
        Flag to renumber days from 1 - Sunday to 1 - Monday
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is the first day (only in the 7-day file)
    chunksize : int, default 2000000
        Rows read at a time
    workers : int, default 2
        Files read in parallel threads

    Returns
    -------
    pandas.DataFrame or None
        One row per participant, the part of nhanes_activity.csv this era
        contributes. None if no input file was found

    Example
    -------
    >>> pynhanes.activity.paxmin_parser("./XPT", "./NPZ")

    """
    input_folder = os.path.expanduser(input_folder)
    output_folder = os.path.expanduser(output_folder)
    minutes = _found(input_folder, PAXMIN_FILES)
    days = _found(input_folder, PAXDAY_FILES)
    if len(minutes) == 0:
        log(f"ERROR! No PAXMIN_G.XPT or PAXMIN_H.XPT found in '{input_folder}'")
        log("Download them with: pynhanes-downloader -a 2011,2013 -o <folder>")
        log("  2011-2012 PAXMIN_G.xpt 7.6 GB, 2013-2014 PAXMIN_H.xpt 8.7 GB")
        return None
    for name in minutes:
        need = name.upper().replace("PAXMIN", "PAXDAY")
        if need not in [d.upper() for d in days]:
            year = 2011 if need.endswith("_G.XPT") else 2013
            log(f"ERROR! '{name}' needs '{need}' in '{input_folder}'.")
            log(f"Download it with: pynhanes-downloader -d PAXDAY -s {year}")
            return None

    # PAXDAY: the participants, and the time of day the first day started.
    # Days 2 onwards start at midnight, the first one when the monitor was
    # handed out, so every minute is shifted by that start time and column 0
    # of the arrays is midnight of the first day
    frames = []
    for name in days:
        frame = pd.read_sas(os.path.join(input_folder, name), format="xport")
        frame["Data File"] = os.path.splitext(name)[0].upper()
        frames.append(frame)
    frames = pd.concat(frames, ignore_index=True)
    frames["SEQN"] = frames["SEQN"].values.astype(np.int64)
    first = frames[_bytes_to_uint8(frames["PAXDAYD"].values) == 1]
    start = first["PAXMSTD"].values.astype(str)
    start = np.array([_time_to_minutes(t) for t in start], dtype=np.int64)
    userlist = np.array(sorted(first["SEQN"].values.tolist()), dtype=np.int64)
    nuser = len(userlist)
    rows = np.searchsorted(userlist, first["SEQN"].values)
    offset = np.zeros(nuser, dtype=np.int64)
    offset[rows] = start
    survey = np.empty(nuser, dtype=object)
    survey[:] = ""
    survey[rows] = [SURVEY_OF_FILE.get(str(f), "") for f in first["Data File"].values]
    ncol = PAXMIN_DAYS * MINUTES_PER_DAY
    log(f"  {nuser} participants, {ncol} minutes each")

    triax = np.zeros((nuser, ncol), dtype=np.float16)
    lumin = np.zeros((nuser, ncol), dtype=np.float16)
    status = np.zeros((nuser, ncol), dtype=np.uint8)
    weekday = np.zeros((nuser, ncol), dtype=np.uint8)
    unknown = np.zeros(1, dtype=np.int64)

    def fill(name):
        path = os.path.join(input_folder, name)
        for chunk in _chunks(path, chunksize):
            seqn = chunk["SEQN"].values.astype(np.int64)
            row = np.searchsorted(userlist, seqn, side="left")
            row = np.clip(row, 0, nuser - 1)
            known = userlist[row] == seqn
            col = chunk["PAXSSNMP"].values.astype(np.int64) // 4800 + offset[row]
            keep = known & (col >= 0) & (col < ncol)
            unknown[0] += int((~known).sum())
            row, col = row[keep], col[keep]
            raw = chunk["PAXMTSM"].values[keep]
            # -0.01 is "value could not be computed": stored as 0, marked Missing
            missing = raw < -1e-3
            value = np.where(raw < 1e-3, 0.0, raw)
            predicted = _bytes_to_uint8(chunk["PAXPREDM"].values)[keep]
            predicted[missing] = STATUS_MISSING
            triax[row, col] = value
            lumin[row, col] = chunk["PAXLXMM"].values[keep]
            status[row, col] = predicted
            weekday[row, col] = _bytes_to_uint8(chunk["PAXDAYWM"].values)[keep]
        return name
    log(f"Reading {len(minutes)} file(s) of PAXMIN...")
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        list(pool.map(fill, minutes))
    if unknown[0]:
        log(f"  NOTE: {int(unknown[0])} minutes of participants that PAXDAY does not list "
            f"were skipped")

    iso = weekday_refill(weekday_to_iso(weekday))
    weekday = iso if iso_weekday else weekday_refill(weekday)

    days_triax = qualifying_days(triax, QUALIFY_TRIAX)

    _save(os.path.join(output_folder, "nhanes_triax_full.npz"), {
        "userid": userlist,
        "weekday": weekday,
        "triax": triax,
        "lumin": lumin,
        "status": status,
        # the 9-day record is not rolled: a roll wraps the end back to the
        # front, which would put the 8th and 9th day before the 1st
        "Rolled by index": np.zeros(nuser, dtype=np.int64),
        "ISO weekday": iso_weekday,
        "Status keys": np.array(STATUS_KEYS),
    })
    del triax, lumin, status, weekday, iso
    paxmin_weekly(output_folder, roll_to_monday, log=log)
    log(f"Saved 'nhanes_triax.npz' and 'nhanes_triax_full.npz' to '{output_folder}'")

    table = pd.DataFrame({
        "SEQN": userlist,
        "Survey": survey,
        "Data File": "PAXMIN",
        "Days with counts": np.nan,
        "Days with steps": np.nan,
        "Days with triax": days_triax.astype(float),
        "Steps clipped": np.nan,
    })
    return _add_header(table, input_folder)


def _time_to_minutes(text):
    """'12:30:00' -> 750"""
    parts = str(text).strip().split(":")
    return int(parts[0]) * 60 + int(parts[1])


def _add_header(table, input_folder):
    """
    Add a row for every participant who was given a monitor but whose minutes
    NHANES did not publish. They stay in the PAXMIN data file of their survey,
    with no qualifying days at all
    """
    names = _found(input_folder, PAXHD_FILES)
    if not names:
        return table
    frames = []
    for name in names:
        frame = pd.read_sas(os.path.join(input_folder, name), format="xport")
        frame["Survey"] = SURVEY_OF_FILE.get(os.path.splitext(name)[0].upper(), "")
        frames.append(frame)
    header = pd.concat(frames, ignore_index=True)
    header["SEQN"] = header["SEQN"].values.astype(np.int64)
    header = header[["SEQN", "Survey"]].drop_duplicates("SEQN")
    absent = header[~header["SEQN"].isin(table["SEQN"])].copy()
    if not len(absent):
        return table
    absent["Data File"] = "PAXMIN"
    table = pd.concat([table, absent.reindex(columns=table.columns)], ignore_index=True)
    return table.sort_values("SEQN").reset_index(drop=True)


def paxmin_weekly(output_folder, roll_to_monday=True, log=print):
    """
    Weekly activity of 2011-2012 and 2013-2014.

    Picks the 7 days of the 9-day record that hold the most measured minutes
    and rolls them so that column 0 is Monday 00:00. Every array of a
    participant is rolled by the same number of minutes.

    Parameters
    ----------
    output_folder : str
        Path to folder to read "nhanes_triax_full.npz" and output "nhanes_triax.npz"
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is always the first day

    Example
    -------
    >>> pynhanes.activity.paxmin_weekly("./NPZ")

    """
    output_folder = os.path.expanduser(output_folder)
    data = np.load(os.path.join(output_folder, "nhanes_triax_full.npz"))
    userlist = data["userid"]
    nuser = len(userlist)
    triax = data["triax"]
    lumin = data["lumin"]
    status = data["status"]
    weekday = data["weekday"]
    iso_weekday = data["ISO weekday"]
    ncol = PAXRAW_DAYS * MINUTES_PER_DAY
    nwindow = PAXMIN_DAYS - PAXRAW_DAYS + 1

    # minutes with movement per day, then the 7 consecutive days that hold most
    measured = (triax.reshape(-1, MINUTES_PER_DAY) > 1e-2).sum(axis=1).reshape(nuser, -1)
    window = np.stack([measured[:, i:i + PAXRAW_DAYS].sum(axis=1)
                       for i in range(nwindow)], axis=1)
    start = MINUTES_PER_DAY * np.argmax(window, axis=1)

    # cut the window block by block: one index array for all 14693 rows at
    # once would be larger than the data itself
    source = (triax, lumin, status, weekday)
    cut = [np.zeros((nuser, ncol), dtype=a.dtype) for a in source]
    for i in range(0, nuser, 512):
        block = slice(i, min(i + 512, nuser))
        take = start[block, None] + np.arange(ncol)[None, :]
        rows = np.arange(block.start, block.stop)[:, None]
        for out, array in zip(cut, source):
            out[block] = array[rows, take]
    triax, lumin, status, weekday = cut

    iso = weekday if iso_weekday else weekday_to_iso(weekday)
    roll = np.zeros(nuser, dtype=np.int64)
    if roll_to_monday:
        roll = MINUTES_PER_DAY * (iso[:, 0].astype(np.int64) - 1)
        roll[iso[:, 0] == 0] = 0
        _roll_rows([triax, lumin, status, weekday], roll)

    _save(os.path.join(output_folder, "nhanes_triax.npz"), {
        "userid": userlist,
        "weekday": weekday,
        "triax": triax,
        "lumin": lumin,
        "status": status,
        "Rolled by index": roll,
        "ISO weekday": iso_weekday,
        "Status keys": np.array(STATUS_KEYS),
    })
    return


def write_activity_table(tables, path, log=print):
    """
    Write nhanes_activity.csv - one row per participant, ";"-separated

    The three measurement columns say how many days of that participant hold
    enough measured minutes: empty when the measurement does not exist for
    them at all, 0 to 9 otherwise. `Steps clipped` counts the minutes the
    0-255 clip truncated.

    Parameters
    ----------
    tables : list of pandas.DataFrame
        What the parsers returned
    path : str
        Output .csv. Rows of participants this run did not parse are kept

    """
    tables = [t for t in tables if t is not None and len(t)]
    if not tables:
        return None
    table = pd.concat(tables, ignore_index=True)
    table = table.reindex(columns=ACTIVITY_COLUMNS)
    path = os.path.expanduser(path)
    if os.path.isfile(path):
        old = pd.read_csv(path, sep=";")
        old = old[~old["SEQN"].isin(table["SEQN"])]
        if len(old):
            log(f"  keeping {len(old)} row(s) of participants not parsed in this run")
            table = pd.concat([old.reindex(columns=ACTIVITY_COLUMNS), table], ignore_index=True)
    table = table.sort_values("SEQN").reset_index(drop=True)
    folder = os.path.dirname(path)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    table.to_csv(path, sep=";", index=False)
    log(f"Saved '{os.path.basename(path)}' ({len(table)} participants) to '{folder}'")
    return table


def parse_activity(input_folder="~/data/NHANES/XPT", output_folder="~/data/NHANES/NPZ",
                   csv_path="~/data/NHANES/CSV/nhanes_activity.csv", era="both",
                   clip_steps=True, iso_weekday=True, roll_to_monday=True,
                   chunksize=CHUNK, workers=WORKERS, log=print):
    """
    Parse every minute-level accelerometry file that is present

    Parameters
    ----------
    input_folder : str
        Folder with the .xpt files
    output_folder : str
        Folder for the .npz files
    csv_path : str or None
        Output nhanes_activity.csv, None to skip it
    era : str, default 'both'
        'paxraw' for 2003-2006, 'paxmin' for 2011-2014, 'both' for everything

    Returns
    -------
    pandas.DataFrame or None
        What was written to nhanes_activity.csv

    """
    tables = []
    if era in ("both", "paxraw"):
        tables.append(paxraw_parser(input_folder, output_folder, clip_steps=clip_steps,
                                    iso_weekday=iso_weekday, roll_to_monday=roll_to_monday,
                                    chunksize=chunksize, workers=workers, log=log))
    if era in ("both", "paxmin"):
        tables.append(paxmin_parser(input_folder, output_folder, iso_weekday=iso_weekday,
                                    roll_to_monday=roll_to_monday, chunksize=chunksize,
                                    workers=workers, log=log))
    if csv_path is None:
        return None
    return write_activity_table(tables, csv_path, log=log)


def main(argv=None):
    """
    Command line entry point of pynhanes-activity
    """
    parser = argparse.ArgumentParser(
        prog="pynhanes-activity",
        description="Parse minute-level NHANES accelerometry into .npz arrays.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-i", "--input", default="~/data/NHANES/XPT",
                        help="folder with the accelerometry .xpt files (default: %(default)s)")
    parser.add_argument("-o", "--output", default="~/data/NHANES/NPZ",
                        help="folder for the .npz files (default: %(default)s)")
    parser.add_argument("-c", "--csv", default="~/data/NHANES/CSV/nhanes_activity.csv",
                        help="one row per participant, ';'-separated (default: %(default)s)")
    parser.add_argument("-e", "--era", choices=["both", "paxraw", "paxmin"], default="both",
                        help="'paxraw' 2003-2006, 'paxmin' 2011-2014 (default: %(default)s)")
    parser.add_argument("-w", "--workers", type=int, default=WORKERS,
                        help="files read in parallel threads (default: %(default)s)")
    parser.add_argument("--chunksize", type=int, default=CHUNK,
                        help="rows read at a time (default: %(default)s)")
    parser.add_argument("--no-clip-steps", action="store_true",
                        help="keep steps in 0-32767 as uint16 instead of clipping to 0-255")
    parser.add_argument("--no-roll", action="store_true",
                        help="leave the 7-day arrays in calendar order instead of "
                             "rolling them so that column 0 is Monday")
    parser.add_argument("--no-iso-weekday", action="store_true",
                        help="keep the NHANES weekday numbering, 1 - Sunday")
    parser.add_argument("--no-csv", action="store_true", help="do not write nhanes_activity.csv")
    args = parser.parse_args(argv)

    log = lambda *a: print(*a, flush=True)
    try:
        parse_activity(args.input, args.output,
                       None if args.no_csv else args.csv, era=args.era,
                       clip_steps=not args.no_clip_steps,
                       iso_weekday=not args.no_iso_weekday,
                       roll_to_monday=not args.no_roll,
                       chunksize=args.chunksize, workers=args.workers, log=log)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        log(f"ERROR: {error}")
        return 1
    except KeyboardInterrupt:
        log("Interrupted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
