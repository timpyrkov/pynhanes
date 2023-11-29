#!/usr/bin/env python
# -*- coding: utf8 -*-

import glob
import string
import numpy as np
import pandas as pd
from tqdm import tqdm
import warnings
warnings.simplefilter(action='ignore', category=pd.errors.PerformanceWarning)


def get_drop_list():
    """
    Numerical encodings for nonsense value descriptions are outliers
    and should be dropped (replaced with np.NaN)
    """
    drop_list = [
        "refuse",
        "refused",
        "sp refused",
        "no response",
        "blank",
        "error",
        "unknown",
        "don't know",
        "dont know",
        "don't  know",
        "don't know/not sure",
        "no / don't know",
        "not determined, picture missing",
        "cannot assess",
        "could not assess",
        "cannot be accessed",
        "can not be assessed",
        "can not assess",
        "cannot be assessed", 
        "could not assess",
        "could not obtain", 
        "could not interpret", 
        "could not determine",
        "calculation cannot be determined", 
        "data acquisition problems", 
        "text present but uncodable", 
        "blank but applicable",
    ]
    return drop_list


def fname_to_categ(fname):
    """
    Get category for an .xpt filename
    E.g. '~/Downloads/DEMO_B.XPT' -> 'DEMO'
    """
    alphabet = list(string.ascii_uppercase)
    categ = fname.split("/")[-1].split(".")[0]
    if categ[-2] == "_" and categ[-1] in alphabet[:15]:
        categ = categ[:-2]
    return categ


def list_xpts_missing(variables, codebook, folder=None):
    """
    List xpt categories that need to be downloded for a given 
    variable codes list (variables). If 'folder' given - 
    list what xpt categories are missing.

    Notes
    -----
    Each XPT can be doanloaded from NHANES website 
    using wgetxpt.py script (e.g. ./wgetxpt.py DEMO -out XPT)

    """
    variables_list = variables_to_list(variables)
    dct = codebook["category"].to_dict()
    req = [dct[v] if v in dct else "" for v in variables_list]
    fnames = sorted(glob.glob(f"{folder}/*.XPT"))
    xpts = [fname_to_categ(f) for f in fnames]
    xpts = set(req) - set(xpts + ["MORT", ""])
    xpts = list(xpts)
    return xpts


def list_xpts_loaded(variables, codebook, folder=None):
    """
    List files that should be loaded from download folder
    for a given variable codes list (variables).
    """
    variables_list = variables_to_list(variables)
    dct = codebook["category"].to_dict()
    req = [dct[v] if v in dct else "" for v in variables_list]
    fnames = sorted(glob.glob(f"{folder}/*.XPT"))
    xpts = [fname_to_categ(f) for f in fnames]
    mask = np.array([True if x in req else False for x in xpts])
    fnames = np.array(fnames)[mask].tolist()
    return fnames


def sort_xpts_loaded(fnames):
    """
    List files that should be loaded from download folder
    for a given variable codes list (variables) grouped
    by categories.
    """
    xpts = []
    fname_list = np.array(fnames, dtype=str)
    categ_list = np.array([fname_to_categ(f) for f in fnames], dtype=str)
    for categ in np.unique(categ_list):
        mask = categ_list == categ
        xpts.append(fname_list[mask].tolist())
    return xpts


def single_digit_outlier(dct, drop_list):
    """
    Search for outlier value descriptions in single-digit dictionary.
    E.g. 7 - "Refused", 9 - "Don't know"
    """
    keymin = []
    outliers = {}
    for key, val in dct.items():
        if len(key) > 1:
            return outliers
        if key.isdigit():
            keymin.append(key)
    keymin = np.sort(np.array(keymin).astype(int))
    mask = np.diff(keymin) > 2
    if len(keymin) > 2 and np.sum(mask) > 0:
        keymin = max(6, keymin[1:][mask][0])
        for key, val in dct.items():
            if key.isdigit() and int(key) >= keymin and val.lower() not in drop_list:
                outliers[key] = val
    return outliers


def repeated_digit_outlier(dct, drop_list):
    """
    Search for outlier value descriptions in multi-digit dictionary.
    E.g. 777 - "Refused", 999 - "Don't know"
    """
    def repeated(s):
        return len(s) == s.count(s[0])
    outliers = {}
    if len(dct) < 3:
        return outliers
    keymax = 0
    for key, val in dct.items():
        if key.isdigit() and not repeated(key) and int(key) > keymax:
            keymax = int(key)
    for key, val in dct.items():
        if len(key) > 1 and repeated(key) and key != val and val.lower() not in drop_list:
            if not key.isdigit() or (key.isdigit() and int(key) > max(11,keymax)):
                outliers[key] = val
    return outliers


def suspected_outleirs(dct):
    """
    Use single_digit_outlier() and repeated_digit_outlier() to
    search for outlier value descriptions in single- and multidigit dictionary.
    """
    drop_list = get_drop_list()
    outliers = single_digit_outlier(dct, drop_list)
    if len(outliers) == 0:
        outliers = repeated_digit_outlier(dct, drop_list)
    return outliers


def decode_missing_values(variables, codebook, data):
    """
    Decode 'missing' values (replace by np.NaN) and
    search for suspected outlier value descriptions.

    Notes
    -----
    To switch off warnings on missing columns and suspected outliers:
    >>> import warnings
    >>> warnings.simplefilter(action='ignore', category=UserWarning)

    """
    variables_list = variables_to_list(variables)
    drop_list = get_drop_list()
    name = codebook["name"].to_dict()
    dct = codebook["codebook"].to_dict()
    for v in variables_list:
        if v not in name:
            warnings.warn(f"{v}: codebook not found", UserWarning, stacklevel=2)
        elif v not in data.columns:
            warnings.warn(f"{v}: xpt not found", UserWarning, stacklevel=2)
        else:
            x = data[v].values
            flag_replace = False
            for key, val in dct[v].items():
                if  key.isdigit() and int(key) > 5 and val.lower() in drop_list:
                    x[x==int(key)] = np.nan
                    flag_replace = True
            if flag_replace:
                data[v] = x
            outliers = suspected_outleirs(dct[v])
            for out in outliers:
                warnings.warn(
                    f"{v}: suspected outlier value '{out}': '{dct[v][out]}'", 
                    UserWarning, stacklevel=2)
    return data


def merge_columns(df):
    """
    Merge all columns of a Dataframe
    - impute NaN of 1st column with other columns values
    """
    dm = df.iloc[:,0]
    if df.shape[1] > 1 :
        for i in range(1,df.shape[1]):
            dm = dm.fillna(df.iloc[:,i])
    return dm


def merge_duplicated_columns(df):
    """
    Merge all duplicated columns in a Dataframe
    - impute NaN of 1st column with other columns values
    """
    columns = sorted(list(set(df.columns)))
    df = [merge_columns(df[[col]]) for col in columns]
    df = pd.concat(df, join="outer", axis=1)
    return df


def load_xpt(fnames):
    """
    File(s) .xpt of one category should be loaded 
    and concatenated vertically.

    """
    if not isinstance(fnames, list):
        fnames = [fnames]
    def load_file(fname):
        df = pd.read_sas(fname, index="SEQN")
        df = df[~df.index.duplicated(keep="first")]
        return df
    df = [load_file(f) for f in fnames]
    df = pd.concat(df, join="outer", axis=0)
    df = df[~df.index.duplicated(keep="first")]
    return df


def load_xpts(fnames):
    """
    Files of all categories should be loaded category-by-category,
    each catogory concatenated vertically, then all - horizantally.
    """
    fnames_sorted = sort_xpts_loaded(fnames)
    df = [load_xpt(f) for f in fnames_sorted]
    df = pd.concat(df, join="outer", axis=1)
    return df


def load_dat(fname):
    """
    Load linked mortality .dat fixed-withdth-file
    """
    col_widths = [14, 1, 1, 3, 1, 1, 1, 4, 8, 8, 3, 3]
    col_names = ["SEQN", "ELIGSTAT", "MORTSTAT", "UCOD_LEADING",
                    "DIABETES", "HYPERTEN", "DODQTR", "DODYEAR", 
                    "WGT_NEW", "SA_WGT_NEW", "PERMTH_INT", "PERMTH_EXM"]
    df = pd.read_fwf(fname, widths=col_widths, names=col_names, na_values=".")
    df = df.set_index("SEQN")
    return df


def load_dats(folder):
    """
    Load all linked mortality .dat fixed-withdth-files
    """
    fnames = sorted(glob.glob(f"{folder}/*_MORT_*_PUBLIC.dat"))
    df = pd.concat([load_dat(f) for f in fnames])
    df = df[~df.index.duplicated(keep="last")]
    return df


def variables_to_list(variables):
    """
    Convert variable code dictionary to list

    Parameters
    ----------
    variables : dict
        Variable code dictionary

    Returns
    -------
    list
        Variable code list

    """
    if isinstance(variables, dict):
        variables_list = list([val for key, val in variables.items()])
        variables_list = [code for sublist in variables_list for code in sublist]
        variables_list = list(set(variables_list))
    else:
        variables_list = variables
    return variables_list


def load_data(variables, codebook, folder):
    """
    Load all .xpt and .dat to pandas Dataframe

    Notes
    -----
    To switch off warnings on missing columns and suspected outliers:
    >>> import warnings
    >>> warnings.simplefilter(action='ignore', category=UserWarning)

    Parameters
    ----------
    variables : dict
        Variable codes dictionary
    codebook : Dataframe
        Codebook scrapped from NHANES website
    folder : str
        Path to folder containing downloaded .xpt and .dat

    Returns
    -------
    Dataframe
        Userdata dataframe

    """
    variables_list = variables_to_list(variables)
    fnames = list_xpts_loaded(variables_list, codebook, folder)
    data = [load_xpts(fnames), load_dats(folder)]
    data = pd.concat(data, join="outer", axis=1)
    data = data[variables_list]
    data = merge_duplicated_columns(data)
    data = decode_missing_values(variables_list, codebook, data)
    return data
    



def processing(variables, codebook, data):
    """
    Convert selecetd fields to text and set multiindex columns

    Parameters
    ----------
    variables : dict
        Variable codes dictionary
    codebook : Dataframe
        Codebook scrapped from NHANES website
    data : Dataframe
        Userdata lodaded from .xpt files

    Returns
    -------
    Dataframe
        Userdata dataframe

    """
    idx = data.index.values.astype(int)
    def dict_to_text(dct, p0=0, p2=2):
        idct = {int(k): " ".join(v.split()[p0:p2]) for k, v in dct.items() if k.isdigit()}
        idct = {k: v.title() for k, v in idct.items()}
        return idct
    def float_to_text(x, dct):
        x = np.vectorize(dct.get)(x).astype(str)
        x = np.array(["Unknown" if x_ == "None" else x_ for x_ in x], dtype=str)
        return x
    def float_to_round(x):
        if np.issubdtype(x.dtype, float):
            x = np.round(x, 5)
        return x
    def float_to_int(x, epsilon=1e-5):
        if np.issubdtype(x.dtype, float):
            mask = np.isnan(x)
            diff = x[~mask] - np.round(x[~mask])
            if np.abs(diff).max() < epsilon:
                x = np.nan_to_num(x)
                x = x.astype(int).astype(str)
                x[mask] = ""
        return x
    def recode(df, cols, dct):
        if not isinstance(cols, list):
            cols = [cols]
        for col in cols:
            x = df[col].values
            for i in np.unique(x[np.isfinite(x)]):
                if i not in dct:
                    dct[i] = i
            x = np.vectorize(dct.get)(x)
            df[col] = x
        return df
    df = []
    categ = codebook["category_name"].to_dict()
    dct = codebook["codebook"].to_dict()
    for name, code in variables.items():
        cat = categ[code[0]]
        cat = "Pressure" if cat[:14] == "Blood Pressure" else cat.split()[0]
        d = merge_columns(data[code])
        x = d.values
        xs = np.unique(x[~np.isnan(x)])
        idct = dct[code[0]]
        # Recode Yes/No and Male/Female 1/2 -> 1/0
        if len(xs) == 2 and "1" in idct and "2" not in idct:
            if idct["1"].lower() in ["yes", "male"]:
                if idct["0"].lower() in ["no", "female"]:
                    x[(x == 2)] = 0
        # Fix Poverty status
        if code[0] in ["INDFMPIR"]:
            mask = np.isfinite(x)
            x = np.digitize(x, [2,4]).astype(float)
            x[~mask] = np.nan
        # Fix Smoking status to 0 - Never, 1 - Quit, 2 - Current
        if code[0] in ["SMQ020", "SMQ120", "SMQ150"]:
            x = 2 * x
            # Mask to set previous smokers to 1
            mask1 = merge_columns(data[["SMQ040", "SMQ140", "SMQ170"]]).values == 3
            x[(x > 0) & mask1] = 1
            # Mask to set non-regular smokers to 0
            mask0 = merge_columns(data[["SMD030", "SMD130", "SMD160"]]).values == 0
            x[(x > 0) & mask0] = 0
        # Fix Family/Household Income to top-coded 11
        if code[0] in ["INDFMINC", "INDFMIN2", "INDHHINC", "INDHHIN2"]:
            idct = {12: 4, 13: 5, 14: 11, 15: 11}
            d = recode(data[code], code, idct)
            x = merge_columns(d).values
        # Fix Survey num -> text
        if code[0] in ["SDDSRVYR"]:
            idct = dict_to_text(dct[code[0]], p0=1)
            x = float_to_text(x, idct)
        # Fix Season num -> text
        if code[0] in ["RIDEXMON"]:
            idct = {1: "Winter", 2: "Summer"}
            x = float_to_text(x, idct)
        # Fix Num of times/wk eat meals not from a home
        if code[0] in ["DBD090", "DBD091", "DBD895"]:
            x[(x == 6666)] = 0
            x[(x > 22) & (x <= 5555)] = 22
        # Fix Num hours watching TV
        if code[0] in ["PAD590", "PAQ480", "PAQ710"]:
            x[(x == 6) | (x == 8)] = 0
        # Fix Hospitalization/Healthcare frequency
        if code[0] in ["HUQ050", "HUQ051", "HUD080", "HUQ080"]:
            if "HUQ050" in code and "HUQ051" in code:
                idct = {4: 3, 5: 3, 6: 4, 7: 5, 8: 5}
                d = recode(data[code], "HUQ051", idct)
                x = merge_columns(d).values
            mask = data[["HUD070", "HUQ070", "HUQ071"]].values == 0
            mask = np.isnan(x) & np.max(mask, axis=1).astype(bool)
            x[mask] = np.nan
        # Fix Diabetes/Vigorous or Moderate Activity
        # (1 - Yes, 2 - No, 3 - Borderline/Unable) -> (0 - No, 1 - Yes)
        if code[0] in ["DIQ010", "PAD200", "PAQ650", "PAD320", "PAQ665"]:
            x[(x == 2)] = 0
            x[(x == 3)] = np.nan
        # Fix Physical activity hours watching TV
        if code[0] in ["PAD590", "PAQ480", "PAQ710"]:
            x[(x == 6) | (x==8)] = 0
        # Fix Blood pressure/pulse to np.NaN if had food, alcohol, coffee, cigarettes
        if code[0] in ["BPXSY1", "BPXSY2", "BPXSY3", "BPXSY4", 
            "BPXDI1", "BPXDI2", "BPXDI3", "BPXDI4", "BPXPLS"]:
            x = np.nanmean(data[code].values, axis=1)
            mask = data[["BPQ150A", "BPQ150B", "BPQ150C", "BPQ150D"]].values == 1
            mask = np.max(mask, axis=1).astype(bool)
            x[mask] = np.nan
        # Fix Mortality tte months -> years
        if code[0] == "PERMTH_INT":
            x = np.round(x / 12.0, 2)
            x[(x < 0.1)] = 0.1
        x = float_to_round(x)
        x = float_to_int(x)
        d = pd.DataFrame(data=x.reshape(-1,1), index=idx, columns=[[cat],[name]])
        df.append(d)
    df = pd.concat(df, join="outer", axis=1)
    return df


