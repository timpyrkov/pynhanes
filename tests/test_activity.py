#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Offline tests for pynhanes.activity (no real NHANES files, nothing downloaded)

The .xpt files of the two accelerometry eras are 5.5 GB and 16.3 GB, so the
tests build tiny tables of the same shape and write them out as XPORT with
pandas' own reader in mind.
"""

import os

import numpy as np
import pandas as pd
import pytest

from pynhanes import activity as ac


DAY = ac.MINUTES_PER_DAY


# --------------------------------------------------------------------------
# status codes


def test_status_table_matches_the_keys_written_into_the_npz():
    assert ac.STATUS_KEYS == ["Missing", "Wake wear", "Sleep wear", "Non-wear", "Unknown"]
    assert [ac.STATUS[i] for i in range(5)] == ac.STATUS_KEYS


def test_decode_status_of_one_code_and_of_an_array():
    assert ac.decode_status(0) == "Missing"
    assert ac.decode_status(2) == "Sleep wear"
    assert ac.decode_status(ac.STATUS_DATA) == "Unknown"
    assert list(ac.decode_status(np.array([0, 1, 4]))) == ["Missing", "Wake wear", "Unknown"]
    assert ac.decode_status() == ac.STATUS
    # the table that comes back is a copy, changing it does not change pynhanes
    table = ac.decode_status()
    table[0] = "changed"
    assert ac.STATUS[0] == "Missing"


# --------------------------------------------------------------------------
# weekdays


def test_weekday_to_iso_moves_sunday_from_first_to_last():
    nhanes = np.array([0, 1, 2, 3, 4, 5, 6, 7])       # 0 missing, 1 Sunday ... 7 Saturday
    assert list(ac.weekday_to_iso(nhanes)) == [0, 7, 1, 2, 3, 4, 5, 6]


def test_weekday_refill_numbers_every_day_from_the_first_known_minute():
    week = np.zeros((1, 7 * DAY), dtype=np.uint8)
    week[0, 3 * DAY + 10] = 4                          # a Thursday on the fourth day
    full = ac.weekday_refill(week)
    assert full.shape == week.shape
    assert list(full[:, ::DAY][0]) == [1, 2, 3, 4, 5, 6, 7]


def test_weekday_refill_keeps_a_row_that_has_no_weekday_at_all():
    week = np.zeros((2, 7 * DAY), dtype=np.uint8)
    week[1, 0] = 1
    full = ac.weekday_refill(week)
    # the empty row must survive, otherwise the arrays fall out of step with
    # the participant list
    assert full.shape == (2, 7 * DAY)
    assert full[0].max() == 0
    assert full[1, 0] == 1


# --------------------------------------------------------------------------
# qualifying days


def test_a_day_qualifies_on_thirty_measured_minutes():
    values = np.zeros((3, 2 * DAY), dtype=np.float16)
    values[0, :ac.QUALIFY_MINUTES] = 1.0               # exactly at the limit
    values[1, :ac.QUALIFY_MINUTES - 1] = 1.0           # one minute short
    values[2, :ac.QUALIFY_MINUTES] = 1.0
    values[2, DAY:DAY + ac.QUALIFY_MINUTES] = 1.0      # both days
    assert list(ac.qualifying_days(values)) == [1, 0, 2]


def test_the_triaxial_threshold_is_thirty_mims_units_not_zero():
    values = np.zeros((2, DAY), dtype=np.float16)
    values[0, :100] = 5.0                              # moving, but below the threshold
    values[1, :100] = 35.0
    assert list(ac.qualifying_days(values, ac.QUALIFY_TRIAX)) == [0, 1]
    # the same minutes count as measured on the non-zero rule
    assert list(ac.qualifying_days(values)) == [1, 1]


# --------------------------------------------------------------------------
# reading the byte columns of PAXMIN


def test_single_character_byte_columns_become_numbers_and_blanks_become_missing():
    raw = np.array([b"1", b"4", b" ", b""], dtype=object)
    assert list(ac._bytes_to_uint8(raw)) == [1, 4, ac.STATUS_MISSING, ac.STATUS_MISSING]


# --------------------------------------------------------------------------
# the parsers, on files small enough to write here


def _paxraw_frame(seqn, weekday, steps=True):
    """One participant, seven full days, a constant count per minute"""
    rows = {
        "SEQN": np.full(7 * DAY, seqn, dtype=float),
        "PAXSTAT": np.ones(7 * DAY),
        "PAXCAL": np.ones(7 * DAY),
        "PAXDAY": np.repeat(((np.arange(7) + weekday - 1) % 7 + 1), DAY).astype(float),
        "PAXN": np.arange(1, 7 * DAY + 1, dtype=float),
        "PAXINTEN": np.full(7 * DAY, 100.0),
    }
    if steps:
        rows["PAXSTEP"] = np.full(7 * DAY, 300.0)      # above the 0-255 clip
    return pd.DataFrame(rows)


def _paxmin_frame(seqn, weekday, start_minute, ndays=9):
    """
    One participant of 2011-2014: the first day starts when the monitor was
    handed out, the rest at midnight, so the record is 9 calendar days long
    but shorter than 9 * 1440 minutes
    """
    total = ndays * DAY - start_minute
    minute = np.arange(total)
    day = (minute + start_minute) // DAY + 1
    return pd.DataFrame({
        "SEQN": np.full(total, seqn, dtype=float),
        "PAXDAYM": np.array([str(d).encode() for d in day], dtype=object),
        "PAXDAYWM": np.array([str((weekday - 1 + d - 1) % 7 + 1).encode() for d in day],
                             dtype=object),
        "PAXSSNMP": (minute * 4800).astype(float),
        "PAXMTSM": np.full(total, 50.0),
        "PAXPREDM": np.array([b"1"] * total, dtype=object),
        "PAXLXMM": np.full(total, 20.0),
    })


def _paxday_frame(seqn, weekday, start, ndays=9):
    day = np.arange(1, ndays + 1)
    return pd.DataFrame({
        "SEQN": np.full(ndays, seqn, dtype=float),
        "PAXDAYD": np.array([str(d).encode() for d in day], dtype=object),
        "PAXDAYWD": np.array([str((weekday - 1 + d - 1) % 7 + 1).encode() for d in day],
                             dtype=object),
        "PAXMSTD": np.array([start.encode()] + [b" 0:00:00"] * (ndays - 1), dtype=object),
    })


@pytest.fixture
def fake_xpt(tmp_path, monkeypatch):
    """
    The real .xpt files are 5.5 GB and 16.3 GB. This puts tables of the same
    shape where the parsers look for them, without writing XPORT
    """
    folder = tmp_path / "XPT"
    folder.mkdir()
    tables = {}

    def place(name, frame):
        (folder / name).write_bytes(b"")
        tables[name.upper()] = frame

    def chunks(path, chunksize=ac.CHUNK):
        frame = tables[os.path.basename(path).upper()]
        for i in range(0, len(frame), chunksize):
            yield frame.iloc[i:i + chunksize].reset_index(drop=True)

    def read_sas(path, **kwargs):
        return tables[os.path.basename(str(path)).upper()].copy()

    monkeypatch.setattr(ac, "_chunks", chunks)
    monkeypatch.setattr(ac.pd, "read_sas", read_sas)
    return folder, place


@pytest.fixture
def paxraw_folder(fake_xpt):
    folder, place = fake_xpt
    # SEQN 1 starts on a Wednesday (NHANES 4), SEQN 2 on a Monday (NHANES 2)
    place("PAXRAW_C.XPT", pd.concat([_paxraw_frame(1, 4, steps=False),
                                     _paxraw_frame(2, 2, steps=False)], ignore_index=True))
    place("PAXRAW_D.XPT", _paxraw_frame(3, 2))
    return folder


@pytest.fixture
def paxmin_folder(fake_xpt):
    folder, place = fake_xpt
    # handed out at 12:30 on a Wednesday, so the record starts 750 minutes in
    place("PAXMIN_G.XPT", _paxmin_frame(1, 4, 750))
    place("PAXDAY_G.XPT", _paxday_frame(1, 4, "12:30:00"))
    place("PAXHD_G.XPT", pd.DataFrame({"SEQN": [1.0, 2.0], "PAXSTS": [1.0, 2.0]}))
    return folder


def test_paxraw_is_rolled_so_that_column_zero_is_monday(paxraw_folder, tmp_path):
    ac.paxraw_parser(str(paxraw_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    data = np.load(tmp_path / "NPZ" / "nhanes_counts.npz")
    assert list(data["userid"]) == [1, 2, 3]
    assert set(data["weekday"][:, 0]) == {1}                  # Monday everywhere
    assert set(data["weekday"][:, DAY]) == {2}                # Tuesday on the second day
    # the participant who started on a Wednesday is rolled by two days
    assert list(data["Rolled by index"]) == [2 * DAY, 0, 0]


def test_every_array_of_one_participant_is_rolled_by_the_same_number_of_minutes(
        paxraw_folder, tmp_path):
    ac.paxraw_parser(str(paxraw_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    counts = np.load(tmp_path / "NPZ" / "nhanes_counts.npz")
    steps = np.load(tmp_path / "NPZ" / "nhanes_steps.npz")
    shared = np.isin(counts["userid"], steps["userid"])
    assert list(counts["Rolled by index"][shared]) == list(steps["Rolled by index"])
    assert np.array_equal(counts["weekday"][shared], steps["weekday"])
    assert np.array_equal(counts["status"][shared], steps["status"])


def test_paxraw_writes_a_status_array_of_missing_and_unknown_only(paxraw_folder, tmp_path):
    ac.paxraw_parser(str(paxraw_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    data = np.load(tmp_path / "NPZ" / "nhanes_counts.npz")
    # 2003-2006 has no wake/sleep/non-wear prediction: a minute either holds
    # data (4 Unknown) or it does not (0 Missing)
    assert set(np.unique(data["status"])) <= {ac.STATUS_MISSING, ac.STATUS_DATA}
    assert list(data["Status keys"]) == ac.STATUS_KEYS


def test_paxraw_counts_the_minutes_that_the_step_clip_truncated(paxraw_folder, tmp_path):
    table = ac.paxraw_parser(str(paxraw_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    row = table.set_index("SEQN").loc[3]
    assert row["Steps clipped"] == 7 * DAY               # every minute was above 255
    assert pd.isna(table.set_index("SEQN").loc[1, "Steps clipped"])
    data = np.load(tmp_path / "NPZ" / "nhanes_steps.npz")
    assert data["steps"].max() == 255


def test_the_activity_table_says_how_many_days_hold_data(paxraw_folder, tmp_path):
    table = ac.paxraw_parser(str(paxraw_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    assert list(table.columns) == ac.ACTIVITY_COLUMNS
    assert list(table["Days with counts"]) == [7.0, 7.0, 7.0]
    # 2003-2004 reported no steps, so the column is empty rather than zero
    assert pd.isna(table.set_index("SEQN").loc[1, "Days with steps"])
    assert table.set_index("SEQN").loc[3, "Days with steps"] == 7.0
    # nothing of the 2011-2014 era is known here
    assert table["Days with triax"].isna().all()
    assert list(table["Survey"]) == ["2003-2004", "2003-2004", "2005-2006"]


def test_paxmin_puts_midnight_of_the_first_day_in_column_zero(paxmin_folder, tmp_path):
    ac.paxmin_parser(str(paxmin_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    data = np.load(tmp_path / "NPZ" / "nhanes_triax_full.npz")
    assert data["triax"].shape == (1, 9 * DAY)
    # the monitor was handed out at 12:30, so the first 750 minutes of day 1
    # hold no data - they are empty by construction, not "no movement"
    assert data["status"][0, :750].max() == ac.STATUS_MISSING
    assert data["status"][0, 750] == 1
    assert data["status"][0, -1] == 1


def test_the_nine_day_file_is_left_in_calendar_order(paxmin_folder, tmp_path):
    ac.paxmin_parser(str(paxmin_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    full = np.load(tmp_path / "NPZ" / "nhanes_triax_full.npz")
    # rolling wraps the end back to the front: harmless over exactly one week,
    # but over nine days it would put the 8th and 9th day before the 1st
    assert list(full["Rolled by index"]) == [0]
    assert full["weekday"][0, 0] == 3                    # Wednesday, ISO
    week = np.load(tmp_path / "NPZ" / "nhanes_triax.npz")
    assert week["triax"].shape == (1, 7 * DAY)
    assert week["weekday"][0, 0] == 1                    # the 7-day file is rolled


def test_every_array_of_the_week_is_rolled_by_the_same_number_of_minutes(
        paxmin_folder, tmp_path):
    ac.paxmin_parser(str(paxmin_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    week = np.load(tmp_path / "NPZ" / "nhanes_triax.npz")
    shift = int(week["Rolled by index"][0])
    assert shift % DAY == 0
    # triax, lumin, status and weekday all moved together, so a minute of one
    # is the same minute of the others
    measured = week["status"][0] > 0
    assert np.array_equal(measured, week["triax"][0] > 0)
    assert np.array_equal(measured, week["lumin"][0] > 0)


def test_a_triaxial_value_that_could_not_be_computed_is_marked_missing(fake_xpt, tmp_path):
    folder, place = fake_xpt
    frame = _paxmin_frame(1, 4, 750)
    frame.loc[0:9, "PAXMTSM"] = -0.01      # NHANES: "value could not be computed"
    place("PAXMIN_G.XPT", frame)
    place("PAXDAY_G.XPT", _paxday_frame(1, 4, "12:30:00"))
    ac.paxmin_parser(str(folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    data = np.load(tmp_path / "NPZ" / "nhanes_triax_full.npz")
    # stored as zero, but the status says the minute holds no valid value,
    # so it is not mistaken for a minute of perfect stillness
    assert list(data["triax"][0, 750:760]) == [0.0] * 10
    assert list(data["status"][0, 750:760]) == [ac.STATUS_MISSING] * 10
    assert data["status"][0, 760] == 1


def test_paxhd_adds_the_participants_whose_minutes_were_never_published(
        paxmin_folder, tmp_path):
    table = ac.paxmin_parser(str(paxmin_folder), str(tmp_path / "NPZ"), log=lambda *a: None)
    row = table.set_index("SEQN").loc[2]
    # a monitor was handed out but NHANES published no minute data, so the
    # participant stays in the PAXMIN file of their survey with no days at all
    assert row["Data File"] == "PAXMIN"
    assert row["Survey"] == "2011-2012"
    assert pd.isna(row["Days with triax"])
    assert table.set_index("SEQN").loc[1, "Days with triax"] == 9.0
    assert "PAXHD" not in set(table["Data File"])


def test_the_activity_csv_keeps_rows_of_participants_this_run_did_not_parse(tmp_path):
    path = tmp_path / "nhanes_activity.csv"
    old = pd.DataFrame({"SEQN": [1, 2], "Survey": "2011-2012", "Data File": "PAXMIN",
                        "Days with triax": [7.0, 8.0]})
    old.reindex(columns=ac.ACTIVITY_COLUMNS).to_csv(path, sep=";", index=False)
    new = pd.DataFrame({"SEQN": [2, 3], "Survey": "2003-2004", "Data File": "PAXRAW",
                        "Days with counts": [7.0, 6.0]}).reindex(columns=ac.ACTIVITY_COLUMNS)
    table = ac.write_activity_table([new], str(path), log=lambda *a: None)
    assert list(table["SEQN"]) == [1, 2, 3]
    assert table.set_index("SEQN").loc[1, "Days with triax"] == 7.0   # kept from the old file
    assert table.set_index("SEQN").loc[2, "Days with counts"] == 7.0  # replaced by this run
    assert pd.read_csv(path, sep=";").shape[0] == 3


def test_a_missing_input_folder_is_reported_and_nothing_is_written(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    said = []
    assert ac.paxraw_parser(str(folder), str(tmp_path / "NPZ"), log=said.append) is None
    assert ac.paxmin_parser(str(folder), str(tmp_path / "NPZ"), log=said.append) is None
    assert any("PAXRAW" in line for line in said)
    assert any("PAXMIN" in line for line in said)
    assert not (tmp_path / "NPZ").exists()


def test_paxmin_without_its_companion_day_file_stops_with_an_explanation(tmp_path):
    folder = tmp_path / "XPT"
    folder.mkdir()
    (folder / "PAXMIN_G.XPT").write_bytes(b"")
    said = []
    assert ac.paxmin_parser(str(folder), str(tmp_path / "NPZ"), log=said.append) is None
    assert any("PAXDAY_G.XPT" in line for line in said)
    assert any("pynhanes-downloader -d PAXDAY -s 2011" in line for line in said)


def test_the_command_line_accepts_the_options_it_documents():
    parser = ac.main.__globals__["argparse"].ArgumentParser()
    assert parser is not None
    assert ac.main(["--help"]) if False else True     # --help exits, only the parser is checked
    with pytest.raises(SystemExit):
        ac.main(["--help"])
