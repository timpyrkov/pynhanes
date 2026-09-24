#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Offline tests for pynhanes.loader (nothing is downloaded, no real NHANES files)
"""

import json

import numpy as np
import pandas as pd
import pytest

from pynhanes import loader as ld


CODEBOOK = [
    # code, NHANES name, pynhanes name, data file, topic, value labels, type, truncated
    ("SDDSRVYR", "Data release cycle", "Survey", "DEMO", "Demographic",
     {"1": "NHANES 1999-2000 Public Release", "12": "NHANES August 2021-August 2023"},
     "Categorical", ""),
    ("RIDEXMON", "Six month time period", "Season of year", "DEMO", "Demographic",
     {"1": "November 1 through April 30", "2": "May 1 through October 31"}, "Binary", ""),
    # everybody older than 80 is reported as 80
    ("RIDAGEYR", "Age in years at screening", "Age", "DEMO", "Demographic",
     {"0 to 79": "Range of Values", "80": ">= 80 years of age"}, "Continuous", "top"),
    ("RIDAGEEX", "Age in months at exam", "Age", "DEMO", "Demographic",
     {"0 to 1019": "Range of Values"}, "Continuous", ""),
    ("HSD010", "General health condition", "Health general", "HSQ", "Health",
     {"1": "Excellent", "5": "Poor"}, "Categorical", ""),
]


@pytest.fixture
def codebook_csv(tmp_path):
    rows = []
    for code, name, combined, data_file, topic, book, kind, truncated in CODEBOOK:
        rows.append({"Code": code, "Name": name, "Combined Name": combined,
                     "Categories": data_file, "Category": data_file,
                     "Category Name": data_file, "Combined Category": topic,
                     "Component": "Questionnaire", "Type": kind, "Truncated": truncated,
                     "Core": True, "Codebook": json.dumps(book), "Recode": False})
    path = tmp_path / "nhanes_codebook.csv"
    pd.DataFrame(rows).set_index("Code").to_csv(path, sep=";")
    return str(path)


def write_table(path, columns, values):
    """
    Write a table the way pynhanes-parser writes it: ";" and two header rows
    """
    frame = pd.DataFrame(values, index=pd.Index([1, 2, 3], name="SEQN"))
    frame.columns = pd.MultiIndex.from_tuples(columns)
    frame.to_csv(path, sep=";")
    return str(path)


@pytest.fixture
def codes_csv(tmp_path, codebook_csv):
    # layout "codes": (data file, variable code), Survey as the release number,
    # and Age in two codes, as nhanes_userdata.csv has it
    return write_table(tmp_path / "nhanes_userdata.csv",
                       [("DEMO", "SDDSRVYR"), ("DEMO", "RIDEXMON"), ("DEMO", "RIDAGEYR"),
                        ("DEMO", "RIDAGEEX"), ("HSQ", "HSD010")],
                       {"SDDSRVYR": [1.0, 10.0, 12.0], "RIDEXMON": [1.0, 2.0, 1.0],
                        "RIDAGEYR": [40.0, np.nan, 9.0], "RIDAGEEX": [np.nan, 600.0, np.nan],
                        "HSD010": [1.0, 4.0, 5.0]})


@pytest.fixture
def names_csv(tmp_path):
    # layout "names": (topic, name), Survey as text
    path = tmp_path / "sub"
    path.mkdir()
    return write_table(path / "nhanes_userdata_names.csv",
                       [("Demographic", "Survey"), ("Demographic", "Season of year"),
                        ("Demographic", "Age"), ("Health", "Health general")],
                       {"Survey": ["1999-2000", "2017-2018", "August 2021-August 2023"],
                        "Season of year": ["Winter", "Summer", "Winter"],
                        "Age": [40.0, 50.0, 9.0], "Health general": [1.0, 4.0, 5.0]})


@pytest.fixture
def npz_folder(tmp_path):
    folder = tmp_path / "NPZ"
    folder.mkdir()
    np.savez_compressed(folder / "nhanes_counts.npz", userid=np.array([1]),
                        counts=np.zeros((1, 10080), dtype=np.float16))
    np.savez_compressed(folder / "nhanes_triax.npz", userid=np.array([2]),
                        triax=np.ones((1, 10080), dtype=np.float16),
                        status=np.ones((1, 10080), dtype=np.uint8))
    return str(folder)


# ---------------------------------------------------------------------------
# the survey, as text and as a number
# ---------------------------------------------------------------------------

def test_survey_years_reads_text_and_numbers():
    assert ld.survey_years(["1999-2000", "2017-2018"]).tolist() == [1999, 2017]
    # the latest survey is named after months, not only years
    assert ld.survey_years(["August 2021-August 2023"]).tolist() == [2021]
    # layout "codes" keeps the NHANES release number
    assert ld.survey_years([1, 10, 12]).tolist() == [1999, 2017, 2021]
    assert np.isnan(ld.survey_years([np.nan, "no year here"])).all()


# ---------------------------------------------------------------------------
# columns: variable codes become names
# ---------------------------------------------------------------------------

def test_decode_columns_uses_the_codebook(codes_csv, codebook_csv):
    df = pd.read_csv(codes_csv, delimiter=";", index_col=0, header=[0, 1])
    out = ld.decode_columns(df, codebook_csv)
    assert list(out.columns) == [("Demographic", "Survey"), ("Demographic", "Season of year"),
                                 ("Demographic", "Age"), ("Health", "Health general")]
    # RIDAGEYR and RIDAGEEX share the name "Age" and are merged, first one wins
    assert out[("Demographic", "Age")].tolist() == [40.0, 600.0, 9.0]


def test_decode_columns_leaves_a_named_table_alone(names_csv):
    df = pd.read_csv(names_csv, delimiter=";", index_col=0, header=[0, 1])
    assert list(ld.decode_columns(df, None).columns) == list(df.columns)


def test_decode_columns_without_a_codebook(codes_csv):
    df = pd.read_csv(codes_csv, delimiter=";", index_col=0, header=[0, 1])
    out = ld.decode_columns(df, None)        # names built into pynhanes
    # the names are known, the topics are not, so the data file stays above them
    assert ("DEMO", "Age") in out.columns
    assert ("DEMO", "Survey") in out.columns
    assert ("HSQ", "Health general") in out.columns


# ---------------------------------------------------------------------------
# the loader itself
# ---------------------------------------------------------------------------

def test_loader_reads_both_layouts(codes_csv, names_csv, codebook_csv):
    by_code = ld.NhanesLoader(codes_csv, path_npz=str(codes_csv) + "-none")
    by_name = ld.NhanesLoader(names_csv, path_npz=str(codes_csv) + "-none")
    assert by_code.survey.tolist() == [1999, 2017, 2021]
    assert by_name.survey.tolist() == [1999, 2017, 2021]
    assert by_code.userdata("Health general").tolist() == [1, 4, 5]
    assert by_name.userdata("Health general").tolist() == [1, 4, 5]
    assert by_code.userdata("Age").tolist() == [40.0, 600.0, 9.0]    # months, not decoded
    assert by_name.userdata("Age").tolist() == [40.0, 50.0, 9.0]


def test_loader_without_accelerometry_says_so(codes_csv):
    nhanes = ld.NhanesLoader(codes_csv, path_npz=str(codes_csv) + "-none")
    assert nhanes.has_accelerometry is False
    assert len(nhanes.userid) == 3
    with pytest.warns(UserWarning):
        nhanes.x


def test_loader_reads_accelerometry(codes_csv, npz_folder):
    nhanes = ld.NhanesLoader(codes_csv, path_npz=npz_folder)
    assert nhanes.has_accelerometry is True
    assert nhanes.userid.tolist() == [1, 2]          # only the two with accelerometry
    assert nhanes.x.shape == (2, 10080)


def test_loader_reads_the_old_npz_key(codes_csv, tmp_path):
    # files written before 2025 call the minute status "categ"
    folder = tmp_path / "OLD"
    folder.mkdir()
    np.savez_compressed(folder / "nhanes_counts.npz", userid=np.array([1]),
                        counts=np.zeros((1, 10080), dtype=np.float16))
    np.savez_compressed(folder / "nhanes_triax.npz", userid=np.array([2]),
                        triax=np.ones((1, 10080), dtype=np.float16),
                        categ=np.ones((1, 10080), dtype=np.int8))
    nhanes = ld.NhanesLoader(codes_csv, path_npz=str(folder))
    assert nhanes.has_accelerometry is True
    assert nhanes.userid.tolist() == [1, 2]


def test_loader_is_quiet_unless_asked(codes_csv, capsys):
    ld.NhanesLoader(codes_csv, path_npz=str(codes_csv) + "-none")
    assert capsys.readouterr().out == ""
    ld.NhanesLoader(codes_csv, path_npz=str(codes_csv) + "-none", verbose=True)
    printed = capsys.readouterr().out
    assert "participants" in printed and "Decoding" in printed


def test_userdata_condition_and_column_lists(names_csv):
    nhanes = ld.NhanesLoader(names_csv, path_npz=str(names_csv) + "-none")
    assert nhanes.userdata("Health general", ">= 4").tolist() == [0.0, 1.0, 1.0]
    assert nhanes.categories() == ["Demographic", "Health"]
    assert "Age" in nhanes.columns("Demographic")
    assert nhanes.column_to_category_column("Age") == ("Demographic", "Age")
    assert nhanes.column_values("Survey").tolist() == ["1999-2000", "2017-2018",
                                                       "August 2021-August 2023"]


def test_random_survey_date_works_with_text_season(names_csv):
    nhanes = ld.NhanesLoader(names_csv, path_npz=str(names_csv) + "-none")
    dates = nhanes.generate_random_survey_date(seed=0)
    assert len(dates) == 3 and (dates > 0).all()


def test_codebook_reads_the_new_columns(codebook_csv):
    book = ld.CodeBook(codebook_csv, variables={})
    assert "Type" in book.data.columns and "Truncated" in book.data.columns
    assert "Core" in book.data.columns
    assert book.dict["HSD010"][1] == "Excellent"


# ---------------------------------------------------------------------------
# what the codebook says about a column: its type and its truncated ends
# ---------------------------------------------------------------------------

def test_variable_type_comes_from_the_codebook(names_csv, codebook_csv):
    nhanes = ld.NhanesLoader(names_csv, path_npz="-none", codebook=codebook_csv)
    assert nhanes.variable_type("Age") == "Continuous"
    assert nhanes.variable_type("Health general") == "Categorical"
    # a column the codebook does not know is read from its values
    assert nhanes.variable_type("Season of year") in ("Binary", "Categorical", "Text")


def test_truncated_ends_are_reported(names_csv, codebook_csv):
    nhanes = ld.NhanesLoader(names_csv, path_npz="-none", codebook=codebook_csv)
    # RIDAGEYR reports everybody over 80 as 80
    assert nhanes.truncated("Age") == "top"
    assert nhanes.truncated("Health general") == ""
    assert nhanes.truncated() == {"Age": "top"}


def test_encode_spreads_categories_and_keeps_numbers(names_csv, codebook_csv):
    nhanes = ld.NhanesLoader(names_csv, path_npz="-none", codebook=codebook_csv)
    x = nhanes.encode(["Age", "Health general"])
    assert x["Age"].tolist() == [40.0, 50.0, 9.0]
    # the answers become columns, named by what they mean
    assert "Health general = Excellent" in x.columns
    assert x["Health general = Excellent"].tolist() == [1.0, 0.0, 0.0]
    assert x.shape == (3, 4)
    plain = nhanes.encode(["Health general"], onehot=False)
    assert plain["Health general"].tolist() == [1.0, 4.0, 5.0]


def test_encode_leaves_text_out(codes_csv, codebook_csv, tmp_path):
    path = write_table(tmp_path / "with_text.csv",
                       [("Demographic", "Age"), ("Sleep", "Sleep time")],
                       {"Age": [40.0, 50.0, 9.0], "Sleep time": ["23:30", "22:00", None]})
    nhanes = ld.NhanesLoader(path, path_npz="-none", codebook=codebook_csv)
    with pytest.warns(UserWarning, match="Sleep time"):
        x = nhanes.encode()
    assert list(x.columns) == ["Age"]


def test_print_summary_names_the_type_and_the_truncation(names_csv, codebook_csv, capsys):
    nhanes = ld.NhanesLoader(names_csv, path_npz="-none", codebook=codebook_csv)
    nhanes.print_summary(("Demographic", "Age"))
    printed = capsys.readouterr().out
    assert "Continuous" in printed and "truncated" in printed


def test_any_one_accelerometry_file_is_enough(tmp_path, codes_csv):
    # only the 20 MB nhanes_steps.npz was downloaded: the loader used to give
    # up on all accelerometry because nhanes_counts.npz was not beside it
    folder = tmp_path / "steps-only"
    folder.mkdir()
    np.savez_compressed(folder / "nhanes_steps.npz", userid=np.array([2]),
                        steps=np.arange(10080).reshape(1, -1).astype(np.uint8),
                        status=np.full((1, 10080), 4, np.uint8))
    nhanes = ld.NhanesLoader(codes_csv, path_npz=str(folder))
    assert nhanes.has_accelerometry is True
    assert list(nhanes.userid) == [2]
    assert nhanes.x.shape == (1, 10080)


def test_the_measurement_can_be_chosen(codes_csv, npz_folder):
    both = ld.NhanesLoader(codes_csv, path_npz=npz_folder)
    assert sorted(both.userid) == [1, 2]              # counts and triax stack
    one = ld.NhanesLoader(codes_csv, path_npz=npz_folder, activity="triax")
    assert list(one.userid) == [2]
    with pytest.raises(ValueError, match="Unknown accelerometry"):
        ld.NhanesLoader(codes_csv, path_npz=npz_folder, activity="lumin")


def test_an_empty_npz_folder_is_reported_not_fatal(tmp_path, codes_csv):
    folder = tmp_path / "nothing"
    folder.mkdir()
    nhanes = ld.NhanesLoader(codes_csv, path_npz=str(folder), verbose=True)
    assert nhanes.has_accelerometry is False
    assert nhanes.userid.size > 0                      # the table still loaded
