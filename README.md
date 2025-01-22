[![Python Versions](https://img.shields.io/pypi/pyversions/pynhanes?style=plastic)](https://pypi.org/project/pynhanes/)
[![PyPI](https://img.shields.io/pypi/v/pynhanes?style=plastic)](https://pypi.org/project/pynhanes/)
[![License](https://img.shields.io/pypi/l/pynhanes?style=plastic)](https://opensource.org/licenses/MIT)
<!-- [![Documentation Status](https://readthedocs.org/projects/pynhanes/badge/?version=latest)](https://pynhanes.readthedocs.io/en/latest/?badge=latest) -->

# NHANES parser
## Python parser and scraper for NHANES accelerometry and questionnaire
https://wwwn.cdc.gov/nchs/nhanes/default.aspx
#
# Features
- Scrape .DOC files to Pandas DataFrame
- Parse .XPT and mortality .DAT files and convert to Pandas DataFrame
- Parse accelerometry .XPT files for 2003-2006 and 2011-2014 surveys to NumPy arrays

# Installation
```
pip install pynhanes
```

# Introduction

NHANES website has hierarchical organization of data: 

- Surveys (e.g. "2011-2012") -> 

  - Components (e.g. "Questionnaire") ->

    - Categories (e.g. "Occupation") ->

      - Data variables (.DOC and .XPT files)

It is conveninet to have all data in Pandas DataFrame of NumPy arrays for data analysis. This repo is here to help you make it.

NOTE: Please, keep in mind, that some NHANES data fields have been recoded since 1999. Make sure you have reviewed the NHANES website and understand how the code processed and changed the data. Especially pay attention to categorical data. This may have effect on data analysis results.

# Quick start

NHANES Parser converts data to Pandas and NumPy format.

1) Make sure you have `wget` and `unzip` utilities installed.\
For Mac OS use `brew install wget` and `brew install unzip`.\
For Ubuntu use `apt install wget` and `apt install unzip`.

2) Make sure you have `1Gb` free space on disk for downloading data from NHANES website.\
Optionally, make sure you have additional `30Gb` free space on disk if you plan to download and parse NHANES accelerometry data.

3) Download template scripts and subfolders from this github repository (35Kb). Unzip to make a working folder for downloading and parsing raw data from NHANES website (You can use another name instead of `workfolder` if you wish).
```
wget https://github.com/timpyrkov/pynhanes/archive/master/scripts.zip
```
```
unzip -j scripts.zip 'pynhanes-master/scripts/*' 'pynhanes-master/pynhanes/wgetxpt.py' -d workfolder
```

4) Go to your working folder, create subfolders, and move `nhanes_variables.json` to the `CSV` subfolder.
```
cd workfolder
```
```
mkdir XPT; mkdir NPZ; mkdir CSV; mv nhanes_variables.json CSV
```

5) `wgetxpt.py` downloads .XPT category files you need.\
For example, to download `DEMO` category files to `XPT/` subfolder, run:
```
python wgetxpt.py DEMO -o XPT
```

6) `parse_codebook.ipynb` scrapes hierarchy of NHANES data fields and saves to Pandas-readable `CSV/nhanes_codebook.csv`

7) `parse_userdata.ipynb` parses .XPT and mortality .DAT files to Pandas-readable `CSV/nhanes_userdata.csv`.\
You need to manually download mortality .DAT files from the NHANES website, otherwise parsing mortality is skipped.\
You need to manually edit `CSV/nhanes_variables.json` to add or remove NHANES data fileds which should be parsed.

8) `parse_activity.ipynb` converts accelerometry .XPT from  and 2011-2014 surveys (`PAX` category) and saves to NumPy-readable:\
`NPZ/nhanes_steps.npz` - step counts for 2005-2006 survey;\
`NPZ/nhanes_counts.npz` - activity counts for 2003-2004/2005-2006 surveys;\
`NPZ/nhanes_triax.npz` - activity counts for 2011-2012/2013-2014 surveys;\
You need approximately `30Gb` free space to store raw accelerometry .XPT files.\
Note that 2011-2014 surveys have status prediction for each minute: 0 - Missing, 1 - Wake wear, 2 - Sleep wear, 3 - Non wear, 4  - Unknown

9) `load_and_plot.ipynb` provides example of loading and handling parsed data stored now in the `CSV/` subfolder

# 

\* `parse_codebook.ipynb` produces a codebook DataFrame which is a handy tool to convert numerically-encoded values to human-readable labels

# 
\** `parse_userdata.ipynb` may combine several variables into a sinle variable. Normally you would like to do that if:

**a) Same data field has alternative names in diffrenet survey years (but be careful since the range of values may have changed -see the codebook):**

`SMD090`, `SMD650` - Avg # cigarettes/day during past 30 days

 **b) It is more reasonable to treat data fields together:**

`SMQ020`, `SMQ120`, `SMQ150` - Smoked at least 100 cigarettes in life / a pipe / cigars at least 20 times in life


<!-- # Documentation

[https://pynhanes.readthedocs.io](https://pynhanes.readthedocs.io) -->