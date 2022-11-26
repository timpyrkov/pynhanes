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

For data analysis it is, however, more conveninet to have all data in Pandas DataFrame of NumPy arrays. This repo is here to help you make it.

NOTE: Please, keep in mind, that some NHANES data fields have been recoded since 1999. Make sure you have reviewed the NHANES website and understand how the code processed and changed the data. Especially pay attention to categorical data. This may have effect on data analysis results.

# Quick start

NHANES Parser lib offers tool to get data in Pandas and NumPy:

1) Create a working folder, e.g. `~/work/NHANES/`, copy notebooks from repository folder `sripts` to the working folder and create subfolders `XPT`, `CSV`, `NPZ`

2) `parse_codebook.ipynb` scrapes hierarchical structure of NHANES website to Pandas DataFrame (default: save data in `CSV` subfolder)

3) `pywgetxpt` can download .XPT category files for all survey years (default: save data in `XPT` subfolder)

4) `parse_userdata.ipynb` gets a list of selected data variable fields and converts .XPT and mortality .DAT files to Pandas DataFrame (default: save data in `CSV` subfolder)

5) `parse_activity.ipynb` converts 2003-2006 and 2011-2014 accelerometry data to NumPy arrays (default: save data in `NPZ` subfolder)

6) `load_and_plot.ipynb` shows an example how to load and hadle parsed data

# 

\* `parse_codebook.ipynb` produces a codebook DataFrame which a handy tool to convert numerically-encoded values to human-readable labels

# 
\** `parse_userdata.ipynb` can combine several variables into a sinle variable. Normally you would like to do that if:

**a) Same data field has alternative names in diffrenet survey years (but be careful since the range of values may have changed -see the codebook):**

`SMD090`, `SMD650` - Avg # cigarettes/day during past 30 days

 **b) It is more reasonable to treat data fields together:**

`SMQ020`, `SMQ120`, `SMQ150` - Smoked at least 100 cigarettes in life / a pipe / cigars at least 20 times in life


<!-- # Documentation

[https://pynhanes.readthedocs.io](https://pynhanes.readthedocs.io) -->