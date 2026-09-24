#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Offline tests for pynhanes.scraper (no requests are made)
"""

import os
import json

import pandas as pd
import pytest

from pynhanes import scraper as sc
from pynhanes import downloader as dl


def doc_page(variables):
    """
    Build a documentation page like the ones on the NHANES website
    """
    blocks = []
    for code, (label, rows) in variables.items():
        table = "".join(f"<tr><td>{k}</td><td>{v}</td><td>1</td><td>1</td><td></td></tr>"
                        for k, v in rows.items())
        blocks.append(f"""
        <div class="pagebreak">
          <dl><dt>Variable Name:</dt><dd>{code}</dd>
              <dt>SAS Label:</dt><dd>{label}</dd>
              <dt>Target:</dt><dd>Both males and females 0 YEARS - 150 YEARS</dd></dl>
          <table><thead><tr><th>Code or Value</th><th>Value Description</th>
          <th>Count</th><th>Cumulative</th><th>Skip to Item</th></tr></thead>
          <tbody>{table}</tbody></table>
        </div>""")
    # SEQN has no value table, like on the real pages
    blocks.append('<div class="pagebreak"><dl><dt>Variable Name:</dt><dd>SEQN</dd>'
                  '<dt>SAS Label:</dt><dd>Respondent sequence number</dd></dl></div>')
    return ("<html><body>" + "".join(blocks) + "</body></html>").encode()


# one variable in two surveys, with the differences described in the documentation
PAGES = {
    "DEMO.htm": doc_page({
        "DMDEDUC2": ("Education Level - Adults 20+",
                     {"1": "Less Than 9th Grade", "2": "High School", "3": "College Graduate",
                      "7": "Refused", ".": "Missing"}),
        "DMDYRSUS": ("Length of time in US", {"1": "Less than 1 year", "88": "Could not determine"}),
        "RIDAGEYR": ("Age at screening", {"0 to 84": "Range of Values", "85": ">= 85 years"}),
    }),
    "DEMO_L.htm": doc_page({
        "DMDEDUC2": ("Education level - Adults 20+",
                     {"1": "Less than 9th grade", "2": "High school", "3": "College graduate",
                      "7": "Refused", "9": "Don't know", ".": "Missing"}),
        "RIDAGEYR": ("Age in years at screening", {"0 to 79": "Range of Values", "80": ">= 80 years"}),
        "DMDMARTZ": ("Marital status", {"1": "Married", "2": "Widowed"}),
    }),
    "SMQ_L.htm": doc_page({"SMQ020": ("Smoked 100 cigarettes", {"1": "Yes", "2": "No"})}),
}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path_factory):
    """
    Serve documentation pages and listings from memory
    """
    monkeypatch.setenv("PYNHANES_CACHE", str(tmp_path_factory.mktemp("cache")))
    files = [
        dl.RemoteFile("DEMO", "DEMO", "Demographics", "1999-2000", "u/DEMO.xpt", "xpt", 1,
                      doc_url="https://x/DEMO.htm", description="Demographic Variables"),
        dl.RemoteFile("DEMO_L", "DEMO", "Demographics", "2021-2023", "u/DEMO_L.xpt", "xpt", 1,
                      doc_url="https://x/DEMO_L.htm", description="Demographic Variables"),
        dl.RemoteFile("SMQ_L", "SMQ", "Questionnaire", "2021-2023", "u/SMQ_L.xpt", "xpt", 1,
                      doc_url="https://x/SMQ_L.htm", description="Smoking"),
    ]

    def fake_collect(surveys, components=None, cache_dir=None, log=print, refresh=False):
        labels = {s.label for s in surveys}
        comps = set(components or dl.COMPONENTS)
        return [f for f in files if f.survey in labels and f.component in comps], []

    requested = []

    def fake_read(self, f):
        requested.append(f.name)
        return PAGES[os.path.basename(f.doc_url)]

    monkeypatch.setattr(dl, "collect_remote_files", fake_collect)
    monkeypatch.setattr(sc.downloader, "collect_remote_files", fake_collect)
    monkeypatch.setattr(sc.NhanesScraper, "_read_page", fake_read)
    return requested


def codebook(path):
    return pd.read_csv(path, sep=";", index_col=0)


def test_scrapes_variables_and_skips_seqn(tmp_path):
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    assert "SEQN" not in df.index
    assert {"DMDEDUC2", "DMDYRSUS", "RIDAGEYR", "DMDMARTZ", "SMQ020"} <= set(df.index)
    assert df.loc["SMQ020", "Component"] == "Questionnaire"
    assert df.loc["DMDEDUC2", "Category"] == "DEMO"
    assert df.loc["MORTSTAT", "Component"] == "Mortality"      # added without scraping


def test_merge_across_surveys_keeps_the_oldest_wording(tmp_path):
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    book = json.loads(df.loc["DMDEDUC2", "Codebook"])
    # label of the newest survey
    assert df.loc["DMDEDUC2", "Name"] == "Education level - Adults 20+"
    # value labels: union of surveys, oldest wording wins
    assert book["1"] == "Less Than 9th Grade"
    assert book["9"] == "Don't know"          # only in the newest survey
    # a code dropped later is kept
    assert json.loads(df.loc["DMDYRSUS", "Codebook"])["88"] == "Could not determine"
    # both top-codings of a continuous variable survive
    age = json.loads(df.loc["RIDAGEYR", "Codebook"])
    assert {"0 to 79", "0 to 84", "80", "85"} <= set(age)


def test_one_survey_is_not_merged(tmp_path):
    out = tmp_path / "codebook_2021.csv"
    sc.NhanesScraper(str(out), surveys=2021, log=lambda *a: None)
    df = codebook(out)
    assert df.loc["DMDEDUC2", "Name"] == "Education level - Adults 20+"
    assert json.loads(df.loc["DMDEDUC2", "Codebook"])["1"] == "Less than 9th grade"
    assert "DMDYRSUS" not in df.index          # 1999-2000 only


def test_selection_by_component_and_data_file(tmp_path, offline):
    sc.NhanesScraper(str(tmp_path / "a.csv"), components="Questionnaire", log=lambda *a: None)
    assert offline == ["SMQ_L"]
    offline.clear()
    sc.NhanesScraper(str(tmp_path / "b.csv"), data_files="DEMO", log=lambda *a: None)
    assert sorted(offline) == ["DEMO", "DEMO_L"]


def test_store_is_off_by_default(tmp_path, monkeypatch):
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    pages = os.path.join(dl.listing_cache_dir(), "pages")
    assert not os.path.exists(pages)
    monkeypatch.undo()


def test_failed_pages_are_reported(tmp_path, monkeypatch):
    def boom(self, f):
        if f.name == "SMQ_L":
            raise OSError("no route to host")
        return PAGES[os.path.basename(f.doc_url)]

    monkeypatch.setattr(sc.NhanesScraper, "_read_page", boom)
    messages = []
    scraper = sc.NhanesScraper(str(tmp_path / "codebook.csv"), log=messages.append)
    assert [f["file"] for f in scraper.failed] == ["SMQ_L"]
    assert any("could not be read" in m for m in messages)
    assert "DMDEDUC2" in codebook(tmp_path / "codebook.csv").index   # the rest still scraped


def test_cli_plan_and_run(tmp_path, capsys):
    assert sc.main(["-n", "-d", "DEMO", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "TO SCRAPE" in out and "2 documentation pages" in out
    target = tmp_path / "sub" / "codebook.csv"
    assert sc.main(["-o", str(target), "-d", "DEMO", "--refresh"]) == 0   # folder is created
    assert target.exists()
    assert "DMDEDUC2" in codebook(target).index


def test_cli_json_and_bad_argument(tmp_path, capsys):
    assert sc.main(["-n", "-j", "-s", "2021"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["surveys"] == ["2021-2023"] and data["pages"] >= 1
    assert sc.main(["-s", "2019"]) == 1
    assert "Invalid survey" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# value types, core flag, availability and disk sizes
# ---------------------------------------------------------------------------

def test_value_type():
    cases = [
        ({"1": "Yes", "2": "No", ".": "Missing"}, "Binary"),
        ({"0": "Female", "1": "Male", "2": "Female"}, "Binary"),          # recoded 0 next to 2
        ({"1": "Less than 9th", "2": "High school", "3": "College"}, "Categorical"),
        ({"0 to 79": "Range of Values", "80": ">= 80 years of age"}, "Continuous"),
        ({"1 to 6": "Range of Values", "7": "7 or more people"}, "Continuous"),
        # a range, but real categories: ethnicity, language, test results
        ({"1 to 5": "Range of Values", "1": "Mexican American", "2": "Other Hispanic",
          "3": "Non-Hispanic White", "4": "Non-Hispanic Black"}, "Categorical"),
        # a range with the values a laboratory result gets below the detection limit
        ({"0.1 to 9": "Range of Values", "1": "Below Limit of Detection",
          "2": "First Below Detection Limit Fill Value",
          "3": "Second Below Detection Limit Fill Value"}, "Continuous"),
        # a range whose low end is collapsed into one code: bottom coding, not a category
        ({"15 to 480": "Range of Values", "0": "No time spent outdoors",
          "14": "1-14 minutes", "3333": "Does not work or go to school"}, "Continuous"),
        # a range next to the very values it covers, each one described
        ({"0 to 7": "Range of Values", "0": "0 days", "1": "1 day", "2": "2 days",
          "3": "3 days", "4": "4 days"}, "Categorical"),
        # bins of a quantity, indexed rather than measured
        ({"1 to 216": "Range of Values", "1": "0-6 months", "2": "7-12 months",
          "3": "> 12 months"}, "Categorical"),
        # an exam status whose third code means the exam did not happen
        ({"1": "Complete", "2": "Partial", "3": "Not done", ".": "Missing"}, "Binary"),
        ({"1": "Value Imputed", ".": "Missing"}, "Flag"),                 # presence flag
        ({"2": "Spanish", "8": "Spanish"}, "Flag"),                       # one answer, two codes
        ({"Usual sleep time": "Value was recorded"}, "Text"),
        ({".": "Missing"}, "Unknown"),
        ({"7": "Refused", "9": "Don't know", ".": "Missing"}, "Unknown"),
    ]
    for book, expected in cases:
        assert sc.value_type(book) == expected, book


def test_value_truncation():
    cases = [
        # ages above 80 are all reported as 80
        ({"0 to 79": "Range of Values", "80": ">= 80 years of age"}, "top"),
        # minutes 1 to 14 are all reported as 14
        ({"15 to 480": "Range of Values", "14": "1-14 minutes"}, "bottom"),
        # a laboratory result below the detection limit gets a fill value
        ({"0.2 to 48": "Range of Values", "0.18": "First Below Detection Limit Fill Value"},
         "bottom"),
        ({"2 to 39": "Range of Values", "1": "1 year or less", "40": "40 or more"},
         "bottom,top"),
        # the many wordings NHANES uses for the same thing
        ({"0 to 4.99": "Range of Values", "5": "PIR value greater than or equal to 5.00"}, "top"),
        ({"3 to 13.5": "Range of Values", "2": "Less then 3 hours", "14": "14 hours or more"},
         "bottom,top"),
        ({"1 to 21": "Range of Values", "5555": "More than 21 meals per week"}, "top"),
        # both ends measured, and a categorical variable that happens to list a range
        ({"12.5 to 67.3": "Range of Values", ".": "Missing"}, ""),
        ({"1 to 5": "Range of Values", "1": "Mexican American", "2": "Other Hispanic",
          "3": "Non-Hispanic White", "4": "Non-Hispanic Black"}, ""),
    ]
    for book, expected in cases:
        assert sc.value_truncation(book) == expected, book


def test_codebook_has_type_and_core(tmp_path):
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    assert list(df.columns)[:12] == ["Name", "Combined Name", "Categories", "Category",
                                     "Category Name", "Combined Category", "Component", "Type",
                                     "Truncated", "Core", "Codebook", "Recoded Codebook"]
    # "Recode" is not a column of its own: "Recoded Codebook" already says
    # whether the parsed table holds 0 where NHANES publishes 2
    assert "Recode" not in df.columns
    assert df.loc["DMDEDUC2", "Type"] == "Categorical"
    assert df.loc["RIDAGEYR", "Type"] == "Continuous"
    # ages above 80 are reported as 80, which the codebook says out loud
    assert df.loc["RIDAGEYR", "Truncated"] == "top"
    assert pd.isna(df.loc["DMDEDUC2", "Truncated"])        # empty cell: ends not coded
    assert df.loc["SMQ020", "Type"] == "Binary"
    # "core" marks the data files whose variables the parser scripts use
    assert bool(df.loc["DMDEDUC2", "Core"]) is ("DEMO" in dl.PRESET_CORE)
    assert bool(df.loc["SMQ020", "Core"]) is ("SMQ" in dl.PRESET_CORE)


def test_codebook_says_how_many_answered(tmp_path):
    # the summary that used to be a separate "wide" file is part of the codebook
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    assert list(df.columns)[12:] == ["Surveys", "Availability", "1999-2000", "2021-2023"]
    # the missing counts live in nhanes_availability.csv, per survey
    assert not {"Valid", "Missing", "Valid %"} & set(df.columns)
    # DMDEDUC2 is on both pages, DMDYRSUS only on the older one
    assert df.loc["DMDEDUC2", "Surveys"] == 2
    assert df.loc["DMDYRSUS", "Surveys"] == 1
    assert df.loc["DMDYRSUS", "2021-2023"] == 0
    assert "SEQN" not in df.index


def test_recoded_codebook_is_the_dictionary_of_the_parsed_table(tmp_path):
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    # Yes/No: No becomes 0, "Refused" and the like are gone
    assert json.loads(df.loc["SMQ020", "Recoded Codebook"]) == {"1": "Yes", "0": "No"}
    # a variable that is not recoded keeps its values, without the missing codes
    education = json.loads(df.loc["DMDEDUC2", "Recoded Codebook"])
    assert education == {"1": "Less Than 9th Grade", "2": "High School",
                         "3": "College Graduate"}     # the oldest wording, see the documentation
    assert "7" not in education and "9" not in education
    # the original dictionary is still there, untouched
    assert json.loads(df.loc["SMQ020", "Codebook"])["2"] == "No"


def test_availability_long(tmp_path):
    scraper = sc.NhanesScraper(str(tmp_path / "codebook.csv"), log=lambda *a: None)
    long = scraper.availability_to_pandas(detailed=True)
    assert set(long.columns) >= {"Survey", "Valid", "Missing", "Valid %", "Survey %",
                                 "Target", "Values"}
    row = long[(long.index == "DMDEDUC2") & (long["Survey"] == "1999-2000")].iloc[0]
    # every row of the fixture page counts one participant; "Refused" and "Missing" are missing
    assert row["Valid"] == 3 and row["Missing"] == 2
    assert json.loads(row["Values"]) == {"1": 1, "2": 1, "3": 1}
    assert "150 YEARS" in row["Target"]
    assert long[long["Survey"] == "2021-2023"].shape[0] >= 1


def test_datafiles(tmp_path):
    scraper = sc.NhanesScraper(str(tmp_path / "codebook.csv"), log=lambda *a: None)
    sizes = scraper.datafiles_to_pandas()
    assert list(sizes.columns) == ["Data File Name", "Component", "Core", "Activity", "Surveys",
                                   "First", "Last", "Files", "Bytes", "Size", "GB"]
    assert sizes.loc["DEMO", "Files"] == 2                    # two surveys
    assert sizes.loc["DEMO", "Surveys"] == 2
    assert sizes.loc["DEMO", "First"] == "1999-2000" and sizes.loc["DEMO", "Last"] == "2021-2023"
    assert sizes.loc["DEMO", "Data File Name"] == "Demographic Variables"
    assert bool(sizes.loc["DEMO", "Core"]) is ("DEMO" in dl.PRESET_CORE)
    # rows are grouped by component, in the order NHANES lists them
    components = list(dict.fromkeys(sizes["Component"]))
    expected = [c for c in list(dl.COMPONENTS) + [dl.MORTALITY] if c in components]
    assert components == expected          # mortality last, it is not a survey component
    assert scraper.disksizes_to_pandas().equals(sizes)        # the name it had in 0.0.x


def test_cli_writes_the_extra_files(tmp_path, capsys):
    out = tmp_path / "CSV" / "codebook.csv"
    assert sc.main(["-o", str(out), "--availability", "--datafiles", "--refresh"]) == 0
    assert (tmp_path / "CSV" / "nhanes_datafiles.csv").exists()
    long = pd.read_csv(tmp_path / "CSV" / "nhanes_availability.csv", sep=";", index_col=0)
    # one row per variable and survey, with every answer counted
    assert "Survey" in long.columns and "Values" in long.columns and "Target" in long.columns
    assert int((long.index == "DMDEDUC2").sum()) == 2      # asked in two surveys
    assert json.loads(long[long["Survey"] == "1999-2000"].loc["DMDEDUC2", "Values"])


# ---------------------------------------------------------------------------
# the codebook that ships with pynhanes
# ---------------------------------------------------------------------------

def test_snapshot_is_shipped_and_readable():
    info = sc.snapshot_info()
    assert info, "pynhanes ships without a codebook snapshot"
    assert info["variables"] > 10000 and len(info["surveys"]) >= 11
    book = sc.read_snapshot("codebook")
    assert book.shape[0] == info["variables"]
    assert "Combined Name" in book.columns and "Truncated" in book.columns
    assert sc.read_snapshot("nonsense") is None


def test_cli_writes_the_shipped_codebook_without_asking_the_website(tmp_path, capsys, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("the shipped codebook must not need the website")
    monkeypatch.setattr(sc.downloader, "collect_remote_files", no_network)

    out = tmp_path / "CSV" / "nhanes_codebook.csv"
    assert sc.main(["-o", str(out), "--availability", "--datafiles"]) == 0
    printed = capsys.readouterr().out
    assert "ships with pynhanes" in printed and "--refresh" in printed
    assert out.exists() and (tmp_path / "CSV" / "nhanes_availability.csv").exists()
    assert (tmp_path / "CSV" / "nhanes_datafiles.csv").exists()
    book = codebook(out)
    assert "RIDAGEYR" in book.index and book.loc["RIDAGEYR", "Type"] == "Continuous"


def test_cli_shipped_codebook_can_be_filtered(tmp_path):
    out = tmp_path / "codebook.csv"
    assert sc.main(["-o", str(out), "-d", "DEMO"]) == 0
    book = codebook(out)
    assert "RIDAGEYR" in book.index and "LBXWBCSI" not in book.index


def test_parser_falls_back_to_the_shipped_codebook(tmp_path, capsys):
    from pynhanes import parser as pr
    messages = []
    book = pr.read_codebook(str(tmp_path / "does_not_exist.csv"), log=messages.append)
    assert len(book) > 10000
    assert isinstance(book.loc["RIDAGEYR", "Codebook"], dict)
    assert any("ships with pynhanes" in m for m in messages)


def test_variables_without_a_value_table_are_kept(tmp_path, monkeypatch):
    # ARX_F documents real measurements without a table of values, while every
    # questionnaire page documents the interviewer's routing the same way
    page = ("<html><body>"
            '<div class="pagebreak"><dl><dt>Variable Name:</dt><dd>ARXO2WD</dd>'
            "<dt>SAS Label:</dt><dd>Occiput-to-wall distance (cm)</dd>"
            "<dt>Target:</dt><dd>Both males and females 20 YEARS - 69 YEARS</dd></dl></div>"
            '<div class="pagebreak"><dl><dt>Variable Name:</dt><dd>ACQ015</dd>'
            "<dt>SAS Label:</dt><dd>BOX 2. CHECK ITEM ACQ.015: GO TO END OF SECTION</dd>"
            "<dt>Target:</dt><dd>Both males and females 0 YEARS - 150 YEARS</dd></dl></div>"
            '<div class="pagebreak"><dl><dt>Variable Name:</dt><dd>SEQN</dd>'
            "<dt>SAS Label:</dt><dd>Respondent sequence number</dd></dl></div>"
            "</body></html>").encode()
    monkeypatch.setitem(PAGES, "DEMO.htm", page)
    out = tmp_path / "codebook.csv"
    sc.NhanesScraper(str(out), log=lambda *a: None)
    df = codebook(out)
    assert "ARXO2WD" in df.index                 # a measurement, kept
    assert df.loc["ARXO2WD", "Type"] == "Unknown"   # its page lists no value
    assert json.loads(df.loc["ARXO2WD", "Codebook"]) == {".": "Missing"}
    assert "ACQ015" not in df.index              # an interviewer instruction
    assert "SEQN" not in df.index
