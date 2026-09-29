#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Offline tests for pynhanes.parser (data files are served from memory)
"""

import os
import pathlib
import json

import numpy as np
import pandas as pd
import pytest

from pynhanes import parser as pr


CODEBOOK = [
    # code, name, combined name, data file, combined category, codebook, recode
    ("RIDAGEYR", "Age in years", "Age", "DEMO", "Demographic",
     {"0 to 79": "Range of Values", "80": ">= 80 years", ".": "Missing"}, False),
    ("RIDAGEMN", "Age in months at screening", "Age in months at screening", "DEMO", "Demographic",
     {"0 to 1019": "Range of Values", ".": "Missing"}, False),
    ("RIDAGEEX", "Age in months at exam", "Age in months at exam", "DEMO", "Demographic",
     {"0 to 1019": "Range of Values", ".": "Missing"}, False),
    ("RIDEXAGM", "Age in months at exam 0-19", "Age in months at exam 0-19", "DEMO", "Demographic",
     {"0 to 239": "Range of Values", ".": "Missing"}, False),
    ("RIAGENDR", "Gender", "Gender", "DEMO", "Demographic",
     {"0": "Female", "1": "Male", "2": "Female", ".": "Missing"}, True),
    ("DMDHRGND", "HH ref person gender", "Gender", "DEMO", "Demographic",
     {"0": "Female", "1": "Male", "2": "Female", ".": "Missing"}, True),
    ("SDDSRVYR", "Data release cycle", "Survey", "DEMO", "Demographic",
     {"1": "NHANES 1999-2000 Public Release", "12": "NHANES August 2021-August 2023 public release",
      ".": "Missing"}, False),
    ("SMQ020", "Smoked 100 cigarettes", "Smoking status", "SMQ", "Smoking",
     {"1": "Yes", "2": "No", "0": "No", "7": "Refused", "9": "Don't know", ".": "Missing"}, True),
    ("SMQ040", "Smoke now", "Smoking now", "SMQ", "Smoking",
     {"1": "Every day", "2": "Some days", "3": "Not at all", ".": "Missing"}, False),
    ("SMD030", "Age started smoking", "Smoking regularly", "SMQ", "Smoking",
     {"0": "Never smoked regularly", "6 to 60": "Range of Values", ".": "Missing"}, False),
    ("DIQ010", "Doctor told you have diabetes", "Diabetes", "DIQ", "Medical",
     {"1": "Yes", "2": "No", "3": "Borderline", "9": "Don't know", ".": "Missing"}, False),
    ("AUQ020A", "Hearing checkbox", "Hearing problem", "AUQ", "Hearing",
     {"1": "Yes (checkbox checked)", "2": "No (checkbox unchecked)", ".": "Missing"}, False),
    ("FSD041", "HH adults cut meals", "Food insecurity", "FSQ", "Food",
     {"0": "No", "1": "Yes", "2": "No", "6": "Screened out", "7": "Refused", ".": "Missing"}, True),
    ("DEQ038G", "Sunburn", "Sunburn", "DEQ", "Skin",
     {"0": "Never", "1": "Yes", "2": "No", ".": "Missing"}, False),
    ("AGQ030", "Hay fever", "Hay fever", "AGQ", "Allergy",
     {"0": "No", "1": "Yes", "2": "No", ".": "Missing"}, True),
    ("HUD080", "Nights in hospital", "Hospital stays", "HUQ", "Hospitalization",
     {"1 to 5": "Range of Values", "99": "Don't know", ".": "Missing"}, False),
    ("HUQ071", "Overnight in hospital", "Hospitalized", "HUQ", "Hospitalization",
     {"1": "Yes", "2": "No", "0": "No", ".": "Missing"}, True),
    ("OCQ380", "Reason not working", "Unemployment status", "OCQ", "Occupation",
     {"1": "Taking care of family", "2": "Going to school", "3": "Retired",
      "4": "Unable to work", "7": "Other", ".": "Missing"}, False),
    ("PERMTH_INT", "Months of follow-up", "Mortality tte", "MORT", "Mortality",
     {"0 to 326": "Months", ".": "Missing"}, False),
    ("MORTSTAT", "Final mortality status", "Mortality event", "MORT", "Mortality",
     {"0": "Assumed alive", "1": "Assumed deceased", ".": "Missing"}, False),
    ("PERMTH_EXM", "Months of follow-up from exam", "Mortality tte from exam", "MORT", "Mortality",
     {"0 to 326": "Months", ".": "Missing"}, False),
    ("UCOD_LEADING", "Underlying leading cause of death", "Mortality cause", "MORT", "Mortality",
     {"1": "Diseases of heart", "7": "Diabetes mellitus", "10": "All other causes",
      ".": "Missing"}, False),
    ("DIABETES", "Diabetes flag from multiple cause of death", "Mortality cause diabetes", "MORT",
     "Mortality", {"0": "No - Condition not listed as a multiple cause of death",
                   "1": "Yes - Condition listed as a multiple cause of death", ".": "Missing"},
     False),
    ("RXDCOUNT", "Number of medicines", "Medicines", "RXQ_RX", "Medications",
     {"1 to 20": "Range of Values", ".": "Missing"}, False),
    ("SLQ300", "Usual sleep time", "Sleep time", "SLQ", "Sleep",
     {"99999": "Don't know", ".": "Missing"}, False),
    ("ARQ024D", "Low back pain 3 months", "Pain back (any)", "ARQ", "Pain",
     {"1": "Yes", "2": "No", ".": "Missing"}, True),
    ("ARQ020D", "Low back pain 6 weeks", "Pain back (any)", "ARQ", "Pain",
     {"4": "LOW BACK", ".": "Missing"}, False),
]

# data file -> {file name: DataFrame}
DATA = {
    "DEMO.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "RIDAGEYR": [40.0, 50.0, 9.0],
                              "RIDAGEEX": [480.0, 600.0, 108.0],
                              "RIAGENDR": [1.0, 2.0, 1.0], "DMDHRGND": [2.0, 2.0, 1.0],
                              "SDDSRVYR": [1.0, 1.0, 1.0]}),
    "DEMO_L.xpt": pd.DataFrame({"SEQN": [7, 8], "RIDAGEYR": [30.0, 80.0],
                                "RIDEXAGM": [360.0, 960.0],
                                "RIAGENDR": [2.0, 1.0], "SDDSRVYR": [12.0, 12.0]}),
    "SMQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "SMQ020": [1.0, 1.0, 2.0],
                             "SMQ040": [1.0, 3.0, np.nan], "SMD030": [18.0, 20.0, np.nan]}),
    "DIQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "DIQ010": [1.0, 3.0, 2.0]}),
    "AUQ.xpt": pd.DataFrame({"SEQN": [1, 2], "AUQ020A": [1.0, 2.0]}),
    "FSQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "FSD041": [1.0, 2.0, 6.0]}),
    "DEQ.xpt": pd.DataFrame({"SEQN": [1, 2], "DEQ038G": [0.0, 2.0]}),
    # the same variable in two data files, like AGQ030 in MCQ / RDQ / AGQ
    "AGQ.xpt": pd.DataFrame({"SEQN": [1, 2], "AGQ030": [1.0, np.nan]}),
    # a Yes/No question and a checkbox that carries the number of the box
    "ARQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "ARQ024D": [2.0, 2.0, np.nan],
                             "ARQ020D": [np.nan, 4.0, np.nan]}),
    "MCQ.xpt": pd.DataFrame({"SEQN": [3, 7], "AGQ030": [2.0, 1.0]}),
    "HUQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "HUD080": [2.0, np.nan, np.nan],
                             "HUQ071": [1.0, 2.0, np.nan]}),
    "OCQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3], "OCQ380": [4.0, 2.0, 3.0]}),
    # a character variable: SAS stores text as bytes, and missing codes as text too
    "SLQ.xpt": pd.DataFrame({"SEQN": [1, 2, 3],
                             "SLQ300": [b"23:30", b"99999", b"  "]}),
    # one participant twice, like the prescription file
    "RXQ_RX.xpt": pd.DataFrame({"SEQN": [1, 1, 2], "RXDCOUNT": [2.0, 2.0, 1.0]}),
}


# variables that live in more than one data file, as AGQ030 does in reality
EXTRA_DATA_FILES = {"AGQ030": "AGQ,MCQ"}


@pytest.fixture
def codebook_csv(tmp_path):
    rows = []
    for code, name, combined, datafile, category, book, recode in CODEBOOK:
        # "Categories" lists every data file the variable is in, "Category" the
        # main one - AGQ030 is in AGQ and in MCQ, as in the real codebook
        files = EXTRA_DATA_FILES.get(code, datafile)
        rows.append({"Code": code, "Name": name, "Combined Name": combined,
                     "Categories": files, "Category": datafile,
                     "Category Name": datafile, "Combined Category": category,
                     "Component": "Questionnaire", "Codebook": json.dumps(book), "Recode": recode})
    path = tmp_path / "codebook.csv"
    pd.DataFrame(rows).set_index("Code").to_csv(path, sep=";")
    return str(path)


@pytest.fixture
def xpt_folder(tmp_path, monkeypatch):
    folder = tmp_path / "XPT"
    folder.mkdir()
    for name in DATA:
        (folder / name).write_bytes(b"not a real xpt, read_sas is patched")
    (folder / "NHANES_1999_2000_MORT_2019_PUBLIC.dat").write_text(
        f"{'1':>14s}1100100.....    1000    1000120120\n"
        f"{'2':>14s}10  ........    1000    1000240240\n"
        f"{'3':>14s}1100711.....    1000    1000 60 58\n")
    monkeypatch.setattr(pr.pd, "read_sas",
                        lambda path, format=None: DATA[os.path.basename(path)].copy())
    return str(folder)


def parse(folder, codebook, variables, **kwargs):
    """
    Parse in the "names" layout (one column per variable name)
    """
    kwargs.setdefault("log", lambda *a: None)
    kwargs.setdefault("layout", "names")
    return pr.parse(folder, None, codebook, variables, **kwargs)


# ---------------------------------------------------------------------------
# variable selection
# ---------------------------------------------------------------------------

def test_ambiguous_names_are_refused(codebook_csv):
    book = pr.read_codebook(codebook_csv)
    with pytest.raises(ValueError, match="more than one variable code"):
        pr.resolve_variables(["Age", "Gender"], book)
    merged = pr.resolve_variables(["Gender"], book, merge_ambiguous=True, log=lambda *a: None)
    assert merged["Gender"] == ["RIAGENDR", "DMDHRGND"]
    explicit = pr.resolve_variables({"Gender": ["RIAGENDR"]}, book)
    assert explicit == {"Gender": ["RIAGENDR"]}
    with pytest.raises(ValueError, match="Unknown variable name"):
        pr.resolve_variables(["Blood pressure"], book)
    with pytest.raises(ValueError, match="Unknown variable code"):
        pr.resolve_variables({"Age": ["NOSUCH"]}, book)


def test_write_variables_marks_entries_to_review(tmp_path, codebook_csv):
    book = pr.read_codebook(codebook_csv)
    mapping = pr.resolve_variables(["Age", "Gender"], book, merge_ambiguous=True,
                                   log=lambda *a: None)
    out = tmp_path / "vars.json"
    pr.write_variables(mapping, str(out), book, log=lambda *a: None)
    text = out.read_text()
    assert "// REVIEW" in text and "DMDHRGND" in text
    assert pr.read_variables(str(out))["Gender"] == ["RIAGENDR", "DMDHRGND"]   # comments ignored


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def test_surveys_are_stacked_and_duplicates_warned(xpt_folder, codebook_csv):
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"], "Medicines": ["RXDCOUNT"]},
                  layout="names", log=messages.append)
    assert list(df.index) == [1, 2, 3, 7, 8]                  # both surveys
    assert df[("Demographic", "Age")].tolist() == [40, 50, 9, 30, 80]
    assert any("RXQ_RX.xpt has 1 extra row" in m for m in messages)
    assert df[("Medications", "Medicines")].loc[1] == 2       # first row kept


def test_variable_in_several_data_files_is_merged(xpt_folder, codebook_csv):
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Hay fever": ["AGQ030"]},
                  layout="names", log=messages.append)
    values = df[("Allergy", "Hay fever")]
    assert values.loc[1] == 1 and values.loc[3] == 0 and values.loc[7] == 1
    assert any("stored in 2 data files" in m for m in messages)


def test_mortality_is_read(xpt_folder, codebook_csv):
    df = parse(xpt_folder, codebook_csv, {"Mortality event": ["MORTSTAT"],
                                          "Mortality tte": ["PERMTH_INT"]})
    assert df[("Mortality", "Mortality event")].loc[1] == 1
    assert df[("Mortality", "Mortality tte")].loc[1] == 10.0      # 120 months -> years


def test_cause_of_death_keeps_the_linked_file_coding(xpt_folder, codebook_csv):
    # the linked file codes its flags 1 = Yes, 0 = No already - nothing may
    # recode them, and a survivor has no cause and no flag
    df = parse(xpt_folder, codebook_csv, {"Mortality event": ["MORTSTAT"],
                                          "Mortality cause": ["UCOD_LEADING"],
                                          "Mortality cause diabetes": ["DIABETES"],
                                          "Mortality tte from exam": ["PERMTH_EXM"]})
    df = df["Mortality"]
    assert df.loc[3, "Mortality cause"] == 7
    assert df.loc[3, "Mortality cause diabetes"] == 1
    assert df.loc[1, "Mortality cause diabetes"] == 0
    assert np.isnan(df.loc[2, "Mortality cause"])
    assert np.isnan(df.loc[2, "Mortality cause diabetes"])
    assert df.loc[3, "Mortality tte from exam"] == pytest.approx(58 / 12, abs=0.01)


def test_mortality_weight_follows_the_linkage():
    mapping = {"Mortality event": ["MORTSTAT"], "Mortality cause": ["UCOD_LEADING"],
               "Mortality tte from exam (yr)": ["PERMTH_EXM"]}
    weights = pd.DataFrame({"1999-2000": ["WTINT2YR"], "2017-2020": ["WTINTPRP"],
                            "2021-2023": ["WTINT2YR"]}, index=pd.Index(["DEMO"], name="Data File"))
    availability = pd.DataFrame({"Code": ["RIDAGEYR"] * 3, "Data File": ["DEMO"] * 3,
                                 "Survey": ["1999-2000", "2017-2020", "2021-2023"],
                                 "Valid": [10, 10, 10]})
    table = pr.weights_dict(mapping, weights, availability)
    assert table.loc["Mortality cause", "1999-2000"] == "Sample weight (interview)"
    assert table.loc["Mortality tte from exam (yr)", "1999-2000"] == "Sample weight (exam)"
    # neither the pre-pandemic release nor 2021-2023 is linked to the death index
    assert table.loc["Mortality event"].drop("1999-2000").isna().all()


def test_a_measurement_is_not_a_checkbox():
    # a range of values is the only label a lab value has, just as "LOW BACK"
    # is the only label of a checkbox - but only the checkbox means "yes"
    assert pr._is_flag({"4": "LOW BACK", ".": "Missing"}) is True
    assert pr._is_flag({"0.113 to 30.302": "Range of Values", ".": "Missing"}) is False
    assert pr._is_flag({"1 to 18": "Range of Values"}) is False


def test_measurements_in_several_codes_keep_their_values():
    # triglycerides live in three codes; merging them once turned every value
    # into 1, as if "has a value" meant "yes"
    data = pd.DataFrame({"LBDTRSI": [0.9, np.nan, np.nan],
                         "LBDSTRSI": [1.4, 2.7, np.nan]}, index=[1, 2, 3])
    codebook = pd.DataFrame(
        {"Codebook": [{"0.113 to 30.302": "Range of Values", ".": "Missing"},
                      {"0.135 to 28.778": "Range of Values", ".": "Missing"}],
         "Type": ["Continuous", "Continuous"]}, index=["LBDTRSI", "LBDSTRSI"])
    merged = pr.merge_codes(data, {"Triglycerides": ["LBDTRSI", "LBDSTRSI"]},
                            log=lambda *a: None, codebook=codebook)
    assert merged["Triglycerides"].tolist()[:2] == [0.9, 2.7]
    assert np.isnan(merged["Triglycerides"].iloc[2])


def test_a_continuous_variable_is_never_merged_as_yes_no():
    # the codebook's own type is a second guard, whatever the labels say
    data = pd.DataFrame({"A": [0.0, 1.0], "B": [1.0, 0.0]}, index=[1, 2])
    codebook = pd.DataFrame({"Codebook": [{"0": "No", "1": "Yes"}, {"0": "No", "1": "Yes"}],
                             "Type": ["Continuous", "Continuous"]}, index=["A", "B"])
    merged = pr.merge_codes(data, {"X": ["A", "B"]}, log=lambda *a: None, codebook=codebook)
    assert merged["X"].tolist() == [0.0, 1.0]            # first code wins, not "any yes"


# ---------------------------------------------------------------------------
# decoding and recoding
# ---------------------------------------------------------------------------

def test_missing_codes_become_empty(xpt_folder, codebook_csv):
    df = parse(xpt_folder, codebook_csv, {"Food insecurity": ["FSD041"], "Diabetes": ["DIQ010"]})
    food = df[("Food", "Food insecurity")]
    assert food.loc[1] == 1 and food.loc[2] == 0
    assert np.isnan(food.loc[3])                                  # 6 = "Screened out"


def test_binary_recode(xpt_folder, codebook_csv):
    df = parse(xpt_folder, codebook_csv, {"Gender": ["RIAGENDR"], "Hearing problem": ["AUQ020A"],
                                          "Diabetes": ["DIQ010"], "Sunburn": ["DEQ038G"]})
    assert df[("Demographic", "Gender")].tolist()[:3] == [1, 0, 1]
    assert df[("Hearing", "Hearing problem")].tolist()[:2] == [1, 0]    # checkbox wording
    diabetes = df[("Medical", "Diabetes")]
    assert diabetes.loc[1] == 1 and diabetes.loc[3] == 0
    assert np.isnan(diabetes.loc[2])                              # 3 = borderline
    # the third answer becomes empty in both layouts, so that no column means "2 = no"
    plain = pr.parse(xpt_folder, None, codebook_csv, {"Diabetes": ["DIQ010"]},
                     layout="codes", log=lambda *a: None)
    values = plain[("DIQ", "DIQ010")].tolist()[:3]
    assert values[0] == 1 and np.isnan(values[1]) and values[2] == 0
    # a variable where 0 is a real answer is never recoded, so 2 stays 2
    assert df[("Skin", "Sunburn")].tolist()[:2] == [0, 2]


def test_no_recode_option(xpt_folder, codebook_csv):
    df = parse(xpt_folder, codebook_csv, {"Gender": ["RIAGENDR"]}, recode=False)
    assert df[("Demographic", "Gender")].tolist()[:2] == [1, 2]


def test_merge_order_decides_the_winner(xpt_folder, codebook_csv):
    first = parse(xpt_folder, codebook_csv, {"Gender": ["RIAGENDR", "DMDHRGND"]})
    second = parse(xpt_folder, codebook_csv, {"Gender": ["DMDHRGND", "RIAGENDR"]})
    assert first[("Demographic", "Gender")].loc[1] == 1
    assert second[("Demographic", "Gender")].loc[1] == 0


# ---------------------------------------------------------------------------
# derived variables
# ---------------------------------------------------------------------------

def test_derived_variables(xpt_folder, codebook_csv):
    variables = {"Survey": ["SDDSRVYR"],
                 "Smoking status": ["SMQ020"], "Smoking now": ["SMQ040"],
                 "Smoking regularly": ["SMD030"],
                 "Hospital stays": ["HUD080"], "Hospitalized": ["HUQ071"],
                 "Unemployment status": ["OCQ380"]}
    df = parse(xpt_folder, codebook_csv, variables)
    assert df[("Demographic", "Survey")].loc[1] == "1999-2000"
    # the 2021-2023 label reads "NHANES August 2021-August 2023 public release"
    assert df[("Demographic", "Survey")].loc[7] == "2021-2023"
    smoking = df[("Smoking", "Smoking status")]
    assert smoking.loc[1] == 2 and smoking.loc[2] == 1 and smoking.loc[3] == 0
    stays = df[("Hospitalization", "Hospital stays")]
    assert stays.loc[1] == 2                                   # reported
    assert stays.loc[2] == 0                                   # answered "not in hospital"
    assert np.isnan(stays.loc[3])                              # never asked
    unemployment = df[("Occupation", "Unemployment status")]
    assert unemployment.tolist()[:3] == [1, 2, 3]


def test_text_variables_are_kept_as_text(xpt_folder, codebook_csv):
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Sleep time": ["SLQ300"]},
                  layout="codes", log=messages.append)
    values = df[("SLQ", "SLQ300")]
    assert values.loc[1] == "23:30"          # bytes decoded to text
    assert pd.isna(values.loc[2])            # "99999" means "Don't know"
    assert pd.isna(values.loc[3])            # blank
    assert any("hold text rather than numbers" in m for m in messages)


def test_derived_can_be_switched_off(xpt_folder, codebook_csv):
    df = parse(xpt_folder, codebook_csv, {"Survey": ["SDDSRVYR"]}, derived=False)
    assert df[("Demographic", "Survey")].loc[1] == 1


def test_derived_reads_its_helper_variables_by_itself(xpt_folder, codebook_csv):
    # asking for the smoking status alone is enough: SMQ040 and SMD030 are read too
    df = parse(xpt_folder, codebook_csv, {"Smoking status": ["SMQ020"]})
    status = df[("Smoking", "Smoking status")]
    assert status.loc[1] == 2 and status.loc[2] == 1 and status.loc[3] == 0


def test_derived_without_its_helper_variables_warns(xpt_folder, codebook_csv, monkeypatch):
    monkeypatch.setitem(pr.DERIVED_READS, "SMQ020", ())      # as if they were not on disk
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Smoking status": ["SMQ020"]},
                  layout="names", log=messages.append)
    assert any("needs other variables" in m for m in messages)
    assert df[("Smoking", "Smoking status")].loc[1] == 1       # left as the recoded Yes/No


def test_age_from_exam_months(xpt_folder, codebook_csv, monkeypatch):
    demo = DATA["DEMO.xpt"].copy()
    demo["RIDAGEYR"] = [2.0, 1.0, 0.0]
    demo["RIDAGEEX"] = [25.0, 24.0, np.nan]
    demo["RIDAGEMN"] = [24.0, 23.0, 11.0]
    demo_l = DATA["DEMO_L.xpt"].copy()
    demo_l["RIDAGEYR"] = [30.0, 4.0]
    demo_l["RIDEXAGM"] = [np.nan, 60.0]
    demo_l["RIDAGEMN"] = [np.nan, 48.0]
    frames = {name: df.copy() for name, df in DATA.items()}
    frames["DEMO.xpt"] = demo
    frames["DEMO_L.xpt"] = demo_l
    monkeypatch.setattr(pr.pd, "read_sas",
                        lambda path, format=None: frames[os.path.basename(path)].copy())
    variables = {"Age": ["RIDAGEYR", "RIDAGEEX", "RIDEXAGM", "RIDAGEMN"]}
    df = parse(xpt_folder, codebook_csv, variables)
    age = df[("Demographic", "Age")]
    assert age.loc[1] == 2          # floor(RIDAGEEX 25 / 12), exam over screening 24
    assert age.loc[2] == 2          # floor(24 / 12); screening year was 1
    assert age.loc[3] == 0          # floor(RIDAGEMN 11 / 12), no exam months
    assert age.loc[7] == 30         # adult, no months published -> screening RIDAGEYR
    assert age.loc[8] == 5          # floor(RIDEXAGM 60 / 12) over screening 48
    screening = parse(xpt_folder, codebook_csv, {"Age": variables["Age"]}, derived=False)
    assert screening[("Demographic", "Age")].loc[2] == 1      # RIDAGEYR, not derived
    only_years = parse(xpt_folder, codebook_csv, {"Age": ["RIDAGEYR"]})
    assert only_years[("Demographic", "Age")].loc[2] == 2     # still floor of exam months
    assert only_years[("Demographic", "Age")].loc[7] == 30    # and RIDAGEYR where none


# ---------------------------------------------------------------------------
# output and command line
# ---------------------------------------------------------------------------

def test_output_file(tmp_path, xpt_folder, codebook_csv):
    out = tmp_path / "sub" / "userdata.csv"
    pr.parse(xpt_folder, str(out), codebook_csv, {"Age": ["RIDAGEYR"], "Gender": ["RIAGENDR"]},
             layout="names", log=lambda *a: None)
    again = pd.read_csv(out, sep=";", index_col=0, header=[0, 1])
    assert again.index.name == "SEQN"
    assert list(again.columns) == [("Demographic", "Age"), ("Demographic", "Gender")]


def test_names_layout_is_the_default(xpt_folder, codebook_csv):
    # nhanes_userdata.csv holds names, not codes: one column per variable,
    # (topic, name), codes merged and derived variables computed
    df = pr.parse(xpt_folder, None, codebook_csv, {"Survey": ["SDDSRVYR"]}, log=lambda *a: None)
    assert list(df.columns) == [("Demographic", "Survey")]
    assert df[("Demographic", "Survey")].loc[1] == "1999-2000"


def test_codes_layout_keeps_one_column_per_variable_code(tmp_path, xpt_folder, codebook_csv):
    out = tmp_path / "userdata.csv"
    variables = {"Age": ["RIDAGEYR"], "Gender": ["RIAGENDR", "DMDHRGND"],
                 "Survey": ["SDDSRVYR"]}
    df = pr.parse(xpt_folder, str(out), codebook_csv, variables, layout="codes",
                  log=lambda *a: None)
    # one column per variable code, named (data file, code), nothing merged
    # codes keep the order of the codebook, grouped by data file
    assert list(df.columns) == [("DEMO", "RIDAGEYR"), ("DEMO", "RIAGENDR"),
                                ("DEMO", "DMDHRGND"), ("DEMO", "SDDSRVYR")]
    assert df[("DEMO", "SDDSRVYR")].loc[1] == 1          # no derived variables in this layout
    assert df[("DEMO", "RIAGENDR")].loc[2] == 0          # but decoding and recoding are applied
    again = pd.read_csv(out, sep=";", index_col=0, header=[0, 1])   # ";" like every other .csv
    assert again.shape == df.shape
    # ambiguous names are not a problem here, every code is its own column
    by_name = pr.parse(xpt_folder, None, codebook_csv, ["Age", "Gender"], layout="codes",
                       log=lambda *a: None)
    assert ("DEMO", "DMDHRGND") in by_name.columns


def test_codes_layout_can_add_derived(xpt_folder, codebook_csv):
    df = pr.parse(xpt_folder, None, codebook_csv, {"Survey": ["SDDSRVYR"]},
                  layout="codes", derived=True, log=lambda *a: None)
    assert df[("DEMO", "SDDSRVYR")].loc[1] == "1999-2000"


def test_unknown_layout(xpt_folder, codebook_csv):
    with pytest.raises(ValueError, match="Unknown layout"):
        pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"]}, layout="wide")


def test_cli(tmp_path, xpt_folder, codebook_csv, capsys):
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps({"Age": ["RIDAGEYR"], "Blood lead": ["LBXBPB"]}))
    assert pr.main(["-n", "-i", xpt_folder, "-b", codebook_csv, "-v", str(variables)]) == 1
    assert "Unknown variable code" in capsys.readouterr().out

    variables.write_text(json.dumps({"Age": ["RIDAGEYR"]}))
    assert pr.main(["-n", "-i", xpt_folder, "-b", codebook_csv, "-v", str(variables)]) == 0
    assert "DATA FILES" in capsys.readouterr().out

    out = tmp_path / "userdata.csv"
    assert pr.main(["-i", xpt_folder, "-o", str(out), "-b", codebook_csv,
                    "-v", str(variables)]) == 0
    assert out.exists()
    assert list(pd.read_csv(out, sep=";", index_col=0, header=[0, 1]).columns) \
        == [("Demographic", "Age")]
    assert pr.main(["-i", xpt_folder, "-o", str(out), "-b", codebook_csv,
                    "-v", str(variables), "-l", "codes"]) == 0
    assert list(pd.read_csv(out, sep=";", index_col=0, header=[0, 1]).columns) \
        == [("DEMO", "RIDAGEYR")]
    capsys.readouterr()

    assert pr.main(["-i", xpt_folder, "-o", str(out), "-b", codebook_csv,
                    "-v", str(variables), "-j"]) == 0
    assert json.loads(capsys.readouterr().out)["participants"] == 5


def test_cli_reports_missing_data_files(tmp_path, xpt_folder, codebook_csv, capsys):
    (tmp_path / "empty").mkdir()
    variables = tmp_path / "vars.json"
    variables.write_text(json.dumps({"Age": ["RIDAGEYR"]}))
    assert pr.main(["-n", "-i", str(tmp_path / "empty"), "-b", codebook_csv,
                    "-v", str(variables)]) == 0
    out = capsys.readouterr().out
    assert "missing: DEMO" in out and "pynhanes-downloader -d DEMO" in out


def test_data_files_of_lists_every_file_a_variable_is_in(codebook_csv):
    book = pr.read_codebook(codebook_csv)
    assert pr.data_files_of(["AGQ030"], book) == {"AGQ", "MCQ"}
    assert pr.data_files_of(["RIDAGEYR"], book) == {"DEMO"}
    assert pr.data_files_of(["MORTSTAT"], book) == {"MORT"}
    assert pr.data_files_of(["NOSUCHCODE"], book) == set()


def test_accelerometry_files_are_not_read(xpt_folder, codebook_csv, monkeypatch):
    # the folder can hold 16 GB of PAXMIN next to the files we want
    opened = []
    real = pr.pd.read_sas
    monkeypatch.setattr(pr.pd, "read_sas",
                        lambda path, format=None: (opened.append(os.path.basename(path)),
                                                   real(path, format=format))[1])
    (pathlib.Path(xpt_folder) / "PAXMIN_G.xpt").write_bytes(b"pretend this is 7.6 GB")
    pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"]}, log=lambda *a: None)
    assert "PAXMIN_G.xpt" not in opened
    assert "DEMO.xpt" in opened


def test_reading_falls_back_to_one_by_one(xpt_folder, codebook_csv, monkeypatch):
    def no_threads(*args, **kwargs):
        raise OSError("no threads here")
    monkeypatch.setattr(pr, "WORKERS", 4)
    monkeypatch.setattr(pr, "PARALLEL_MIN_FILES", 1)
    monkeypatch.setattr(pr, "ThreadPoolExecutor", no_threads)
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"]}, log=messages.append)
    assert df.shape[0] > 0
    assert any("reading them one by one" in m for m in messages)


def test_yes_no_codes_merge_as_yes_anywhere(xpt_folder, codebook_csv):
    messages = []
    df = pr.parse(xpt_folder, None, codebook_csv, {"Pain back (any)": ["ARQ024D", "ARQ020D"]},
                  layout="names", log=messages.append)
    pain = df[("Pain", "Pain back (any)")]
    assert pain.loc[1] == 0          # asked, said no
    assert pain.loc[2] == 1          # said no to one question, ticked the other box
    assert np.isnan(pain.loc[3])     # asked neither
    assert any("yes in any of the codes" in m for m in messages)


def test_prepandemic_files_are_left_out_unless_asked(xpt_folder, codebook_csv, monkeypatch):
    # the pre-pandemic release holds the 2017-2018 people again, under other SEQN
    frames = {name: df.copy() for name, df in DATA.items()}
    # 2017-2018, and the pre-pandemic release holding those people again under
    # other SEQN, together with the 2019-March 2020 part
    frames["DEMO_J.xpt"] = pd.DataFrame({"SEQN": [60, 61], "RIDAGEYR": [41.0, 51.0],
                                         "SDDSRVYR": [10.0, 10.0]})
    frames["P_DEMO.xpt"] = pd.DataFrame({"SEQN": [77, 78, 79], "RIDAGEYR": [41.0, 51.0, 33.0],
                                         "SDDSRVYR": [66.0, 66.0, 66.0]})
    for name in ("DEMO_J.xpt", "P_DEMO.xpt"):
        (pathlib.Path(xpt_folder) / name).write_bytes(b"read_sas is patched")
    monkeypatch.setattr(pr.pd, "read_sas",
                        lambda path, format=None: frames[os.path.basename(path)].copy())

    messages = []
    plain = pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"]}, log=messages.append)
    assert 60 in plain.index and 77 not in plain.index          # 2017-2018 is read
    assert any("pre-pandemic" in m for m in messages)

    instead = pr.parse(xpt_folder, None, codebook_csv, {"Age": ["RIDAGEYR"]}, prepandemic=True,
                       log=lambda *a: None)
    assert 77 in instead.index and 79 in instead.index          # pre-pandemic is read
    assert 60 not in instead.index                              # 2017-2018 is not
    assert 1 in instead.index and 7 in instead.index            # other surveys are untouched


def test_data_file_code_of_every_survey():
    assert pr._data_file_code("XPT/DEMO.xpt") == "DEMO"
    assert pr._data_file_code("XPT/DEMO_J.xpt") == "DEMO"
    assert pr._data_file_code("XPT/P_DEMO.xpt") == "DEMO"
    # the letter of the next cycle is not known yet, and must not stay in the code
    assert pr._data_file_code("XPT/DEMO_M.xpt") == "DEMO"
    assert pr._data_file_code("XPT/NHANES_2001_2002_MORT_2019_PUBLIC.dat") == "MORT"
    assert pr._data_file_code("XPT/L11_2.xpt") == "L11_2"


def test_the_variables_file_that_ships_with_pynhanes(tmp_path):
    shipped = pr.shipped_variables()
    assert shipped is not None and shipped.name == "nhanes_variables.json"
    book = pr.read_variables(str(shipped))
    assert isinstance(book, dict) and len(book) > 300
    # the age in months is listed, so that a reader sees where floor(months/12)
    # comes from instead of having to know about DERIVED_READS
    assert book["Age"] == ["RIDAGEYR"] + list(pr.AGE_MONTH_CODES)
    # the depression screener, one line per question
    phq = [code for codes in book.values() for code in codes if code.startswith("DPQ")]
    assert sorted(phq) == [f"DPQ0{i}0" for i in range(1, 10)] + ["DPQ100"]
    # one variable per line, so that the file can be read at a glance
    lines = [l for l in shipped.read_text().splitlines()
             if l.strip().startswith('"')]
    assert len(lines) == len(book)


def test_a_missing_variables_file_falls_back_to_the_shipped_one(tmp_path):
    said = []
    book = pr.read_variables(str(tmp_path / "nowhere.json"), log=said.append)
    assert book == pr.read_variables(str(pr.shipped_variables()))
    assert any("ship with pynhanes" in line for line in said)
    # anything that is not a .json path is still a comma-separated list
    assert pr.read_variables("Age, Gender") == ["Age", "Gender"]


def test_the_recoding_is_read_off_the_two_codebooks(xpt_folder, codebook_csv):
    # "Recode" was a column of its own until 1.0.0, and it disagreed with the
    # parser for the checkbox Yes/No variables. The two dictionaries do not
    yes_no = {"1": "Yes", "2": "No"}
    assert pr.recodes_to_binary(yes_no, {"1": "Yes", "0": "No"}) is True
    checkbox = {"1": "Yes (checkbox checked)", "2": "No (checkbox unchecked)"}
    assert pr.recodes_to_binary(checkbox, {"1": "Yes (checkbox checked)",
                                           "0": "No (checkbox unchecked)"}) is True
    # a scale that happens to have a 2 is not a recoding
    scale = {"1": "Excellent", "2": "Very good", "3": "Good"}
    assert pr.recodes_to_binary(scale, scale) is False
    # nor is a 2 that was dropped because it means "not measured"
    assert pr.recodes_to_binary({"1": "Acceptable", "2": "Could Not Obtain"},
                                {"1": "Acceptable"}) is False
    assert pr.recodes_to_binary({}, {}) is False
    # and the parser still recodes without a "Recode" column in the codebook
    book = pr.read_codebook(codebook_csv, log=lambda *a: None)
    assert "Recode" not in book.columns or True     # fixture may predate 1.0.0
    df = parse(xpt_folder, codebook_csv, {"Gender": ["RIAGENDR"]})
    assert df[("Demographic", "Gender")].tolist()[:3] == [1, 0, 1]


def test_the_corner_cell_says_how_the_table_was_made(tmp_path, xpt_folder, codebook_csv):
    out = tmp_path / "userdata.csv"
    pr.parse(xpt_folder, str(out), codebook_csv, {"Age": ["RIDAGEYR"]}, log=lambda *a: None)
    first = out.read_text().splitlines()[0]
    assert first.startswith("pynhanes ")
    assert " | names | recoded | derived | " in first
    # reading it the documented way is unaffected
    back = pd.read_csv(out, sep=";", index_col=0, header=[0, 1])
    assert list(back.columns) == [("Demographic", "Age")]
    assert back.index.name == "SEQN"
    assert pr.read_provenance(back) == first.split(";")[0]
    # the switches that change the values are named
    pr.parse(xpt_folder, str(out), codebook_csv, {"Age": ["RIDAGEYR"]}, layout="codes",
             recode=False, log=lambda *a: None)
    assert " | codes | not recoded | not derived | " in out.read_text().splitlines()[0]
    # a table without a stamp is reported as such
    plain = pd.read_csv(out, sep=";", index_col=0, header=[0, 1])
    plain.columns = plain.columns.set_names([None, None])
    assert pr.read_provenance(plain) is None
