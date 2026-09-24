#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import re
import numpy as np
import pandas as pd
from datetime import datetime
import operator
import json
try:
    import jsoncomment
except ImportError:                 # pragma: no cover - optional dependency
    jsoncomment = None
import warnings



def load_variables(path):
    """
    Load variable human-readable names and combinations from .json
    
    Parameters
    ----------
    path : str
        Path to manually created variable human-readable names and combinations .json

    Returns
    -------
    dict
        Dictionary {human-readable name -> list of NHNAES codes}

    """
    with open(os.path.expanduser(path)) as f:
        if jsoncomment is not None:
            return jsoncomment.JsonComment(json).load(f)
        # same thing without the dependency: drop whole-line "//" comments
        import re
        return json.loads(re.sub(r"^\s*//.*$", "", f.read(), flags=re.M))



def survey_years(values):
    """
    First year of the survey, from the text or the number that names it

    The parsed table holds the survey as text ("1999-2000", and for the latest
    survey "August 2021-August 2023") in layout "names", and as the NHANES
    release number (1 ... 12) in layout "codes".

    Parameters
    ----------
    values : array-like
        Survey as text or as the release number

    Returns
    -------
    ndarray
        First year of each survey, NaN where it could not be read

    """
    years = []
    for value in np.asarray(values, dtype=object):
        if isinstance(value, str):
            found = re.search(r"(?:19|20)\d{2}", value)
            years.append(int(found.group()) if found else np.nan)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            years.append(np.nan)
            continue
        # release 1 is 1999-2000, release 10 is 2017-2018, release 12 is 2021-2023
        years.append(1997 + 2 * int(number) if np.isfinite(number) else np.nan)
    return np.array(years, dtype=float)


def decode_columns(df, codebook=None, log=None):
    """
    Give the table (topic, name) columns, whichever layout it was written in

    pynhanes-parser writes two layouts: "names", whose columns are already
    (topic, name), and "codes", whose columns are (data file, variable code) -
    the layout of nhanes_userdata.csv. This turns the second into the first,
    using the codebook of pynhanes-scraper, and merges the codes that share a
    name (first code that has a value wins), as the parser does.

    Parameters
    ----------
    df : DataFrame
        Table with a two-level column index
    codebook : str, DataFrame or None, default None
        Codebook .csv of pynhanes-scraper, or a DataFrame of it; None - the
        names built into pynhanes (pynhanes.scraper.combined_names())
    log : callable or None, default None
        Function to print messages

    Returns
    -------
    DataFrame
        Table whose columns are (topic, name)

    """
    from pynhanes import scraper                  # lazy: keeps the import order simple
    codes = [c[1] for c in df.columns]
    names, topics = scraper.combined_names(), {}
    if codebook is not None:
        book = codebook
        if isinstance(book, str):
            book = pd.read_csv(os.path.expanduser(book), delimiter=";", index_col=0,
                               usecols=["Code", "Combined Name", "Combined Category"])
        names = book["Combined Name"].dropna().to_dict()
        topics = book["Combined Category"].dropna().to_dict()
    known = sum(1 for c in codes if c in names)
    if known < max(1, len(codes) // 2):
        return df                                  # already (topic, name)
    if log is not None:
        log(f"Decoding {known} of {len(codes)} columns from variable codes to names")
    merged, order, used = {}, [], {}
    for (data_file, code), column in zip(df.columns, df.columns):
        name = names.get(code, code)
        topic = topics.get(code) or data_file
        if name in used:
            merged[used[name]] = merged[used[name]].fillna(df[column])
            continue
        used[name] = (topic, name)
        order.append((topic, name))
        merged[(topic, name)] = df[column].copy()
    out = pd.DataFrame(merged, index=df.index)
    out = out[order]
    out.columns = pd.MultiIndex.from_tuples(order)
    return out


def column_metadata(names, codebook=None):
    """
    What the codebook says about each column: its type and its truncated ends

    Parameters
    ----------
    names : iterable
        Column names, as they appear in the parsed table
    codebook : str, DataFrame or None, default None
        Codebook .csv of pynhanes-scraper, or a DataFrame of it

    Returns
    -------
    dict
        Name -> {"type": "Continuous" | "Categorical" | "Binary" | "Flag" |
        "Text" | "Unknown", "truncated": "" | "top" | "bottom" | "bottom,top",
        "labels": {value: what it means}}

    """
    if codebook is None:
        return {}
    book = codebook
    if isinstance(book, str):
        try:
            book = pd.read_csv(os.path.expanduser(book), delimiter=";", index_col=0)
        except (OSError, ValueError):
            return {}
    if "Combined Name" not in book.columns or "Type" not in book.columns:
        return {}
    wanted = set(names)
    meta = {}
    for name, rows in book.groupby("Combined Name"):
        if name not in wanted:
            continue
        row = rows.iloc[0]
        truncated = row["Truncated"] if "Truncated" in rows.columns else ""
        labels = {}
        # "Recoded Codebook" is the dictionary of the parsed table: No is 0
        # there, and the missing codes are gone
        for column in ("Recoded Codebook", "Codebook"):
            if column not in rows.columns:
                continue
            try:
                book_of_name = json.loads(row[column])
            except (TypeError, ValueError):
                continue
            labels = {int(k): v for k, v in book_of_name.items() if str(k).isdigit()}
            if labels:
                break
        meta[name] = {"type": str(row["Type"]),
                      "truncated": "" if pd.isna(truncated) else str(truncated),
                      "labels": labels}
    return meta


# One accelerometry measurement per file, so any of them can be loaded alone
ACTIVITY_KEYS = {"counts": "nhanes_counts.npz", "steps": "nhanes_steps.npz",
                 "triax": "nhanes_triax.npz"}
# counts (2003-2006) and triax (2011-2014) are different participants, so they
# stack; steps are a subset of the counts participants and are not added twice
ACTIVITY_DEFAULT = ["counts", "triax", "steps"]


class NhanesLoader():
    """
    Class to load userdata and accelerometry for NHANES samples.

    Notes
    -----
    Only userdata is loaded for samples of 2003-2006 and 2011-2014
    batches, that is for those who have accelerometry data. 

    Parameters
    ----------
    path_csv : str, default '~/data/NHANES/CSV/nhanes_userdata.csv'
        Path to user data csv file, in either layout of pynhanes-parser
    path_npz : str or None, default '~/data/NHANES/NPZ/'
        Path to the folder with the accelerometry .npz files. Whichever of
        them are there are used; none of them is required
    activity : str, list or None, default None
        Which accelerometry measurement(s) to read: 'counts' (2003-2006),
        'steps' (2005-2006) or 'triax' (2011-2014). None reads every file
        that is there
        If accelerometry is loaded correctly, userid is shrinked to accelerometry subset
    codebook : str or None, default None
        Codebook .csv of pynhanes-scraper, used to turn variable codes into
        names; None - 'nhanes_codebook.csv' next to the user data, or the
        names built into pynhanes
    verbose : bool, default False
        Print what was loaded

    """
    
    def __init__(self, path_csv="~/data/NHANES/CSV/nhanes_userdata.csv",
                 path_npz="~/data/NHANES/NPZ/", codebook=None, activity=None,
                 verbose=False):
        self.verbose = verbose
        log = (lambda *a: print(*a)) if verbose else (lambda *a: None)
        path = os.path.expanduser(path_csv)
        self._df = pd.read_csv(path, delimiter=";", index_col=0, header=[0, 1],
                               low_memory=False)
        if codebook is None:
            beside = os.path.join(os.path.dirname(path), "nhanes_codebook.csv")
            codebook = beside if os.path.isfile(beside) else None
        self._df = decode_columns(self._df, codebook, log)
        self._userid = self._df.index.values
        self._load_activity(path_npz, activity, log)
        self._meta = column_metadata([c[1] for c in self._df.columns], codebook)
        truncated = [n for n, m in self._meta.items() if m["truncated"]]
        if truncated:
            log(f"{len(truncated)} column(s) have a truncated end, e.g. "
                f"{', '.join(sorted(truncated)[:4])} - see .truncated()")
        self._survey = survey_years(self.column_values("Survey"))
        log(f"{len(self.userid)} participants, surveys "
            f"{', '.join(str(int(y)) for y in np.unique(self._survey[np.isfinite(self._survey)]))}")


    @property
    def userid(self):
        """
        Get user ids.

        Returns
        -------
        ndarray
            1D array of size N samples
        
        """
        return np.copy(self._userid)


    @property
    def survey(self):
        """
        Get user survey years.

        Returns
        -------
        ndarray
            1D array of size N samples
        
        """
        return np.copy(self._survey)


    @property
    def x(self):
        """
        Get array of physical activity.

        Returns
        -------
        ndarray
            2D array of size N samples x 10080 minutes
        
        """
        x_ = np.copy(self._x)
        if self.has_accelerometry:
            d = np.diff(x_, axis=1, append=x_[:,:1])
            mask = (x_ > 32000) & (d==0)
            x_[mask] = 0
        else:
            warnings.warn("Accelerometry file not found. Check path to file.")
        return x_


    def xbinned(self, cutoff=(3.0, 3.5)):
        """
        Get binarized array of physical activity.

        Parameters
        ----------
        cutoff : int or tuple, default (3.0, 3.5)
            Cutoff or separate cutoffs for 2003-2006 and 2011-2014 cohorts

        Returns
        -------
        ndarray
            2D array of size N samples x 10080 minutes
        
        """
        assert isinstance(cutoff, int) or len(cutoff) == 2
        cutoff = cutoff if len(cutoff) == 2 else (cutoff,) * 2
        x_ = self.x
        x_ = np.log2(x_+1)
        x_ = np.vstack([
            (x_[self.survey < 2010] > cutoff[0]).astype(float),
            (x_[self.survey > 2010] > cutoff[1]).astype(float),
        ])
        return x_


    def categories(self):
        """
        Get list of loaded userdata categories.

        Returns
        -------
        list
            List of userdata categories
        
        """
        cols = np.array(self._df.columns.to_list()).T
        categs = list(dict.fromkeys(cols[0]))
        return categs


    def columns(self, category=None):
        """
        Get list of loaded userdata columns.

        Parameters
        ----------
        category : str or None, default None
            If given, list only columns for that category
        
        Returns
        -------
        list
            List of userdata columns
        
        """
        cols = np.array(self._df.columns.to_list()).T
        mask = np.ones((cols.shape[1])).astype(bool)
        if category is not None:
            mask = cols[0] == category
        cols = cols[1][mask]
        return cols
    

    def _load_activity(self, path_npz, activity, log):
        """
        Read the accelerometry .npz files that are there

        Each measurement is a file of its own, so any one of them can be used
        alone - handy when only the 20 MB nhanes_steps.npz was downloaded. A
        file that is not there is skipped rather than giving up on all of them,
        and participants already taken from an earlier file are not taken twice.
        """
        wanted = ACTIVITY_DEFAULT if activity is None else activity
        if isinstance(wanted, str):
            wanted = [wanted]
        unknown = [w for w in wanted if w not in ACTIVITY_KEYS]
        if unknown:
            raise ValueError(f"Unknown accelerometry measurement(s): {', '.join(unknown)}. "
                             f"Use one or more of: {', '.join(ACTIVITY_KEYS)}.")
        folder = os.path.expanduser(path_npz) if path_npz else None
        userid, values, status, loaded = [], [], [], []
        seen = set()
        for name in wanted:
            path = os.path.join(folder, ACTIVITY_KEYS[name]) if folder else ""
            if not folder or not os.path.isfile(path):
                continue
            try:
                npz = np.load(path)
                ids = npz["userid"]
                keep = np.array([i not in seen for i in ids], dtype=bool)
                if not keep.any():
                    continue
                seen.update(ids[keep].tolist())
                userid.append(ids[keep])
                values.append(npz[name][keep].astype(float))
                # the minute status was called "categ" before 2025, and the
                # 2003-2006 files had none at all
                if "status" in npz or "categ" in npz:
                    code = npz["status"] if "status" in npz else npz["categ"]
                    status.append(code[keep].astype(np.int8))
                else:
                    status.append(np.zeros_like(values[-1], np.int8))
                loaded.append(f"{name} ({len(userid[-1])})")
            except (KeyError, OSError) as error:
                log(f"Could not read {path}: {error.__class__.__name__}: {error}")
        if not userid:
            self.has_accelerometry = False
            self._x = np.zeros((len(self._userid), 1)) * np.nan
            self._categ = np.zeros((len(self._userid))) * np.nan
            if path_npz:
                log(f"No accelerometry loaded from {path_npz}: none of "
                    f"{', '.join(ACTIVITY_KEYS[w] for w in wanted)} is there")
            return
        width = max(v.shape[1] for v in values)
        values = [v if v.shape[1] == width else
                  np.pad(v, ((0, 0), (0, width - v.shape[1])), constant_values=np.nan)
                  for v in values]
        status = [c if c.shape[1] == width else
                  np.pad(c, ((0, 0), (0, width - c.shape[1]))) for c in status]
        self.has_accelerometry = True
        self._userid = np.concatenate(userid)
        self._x = np.vstack(values)
        self._categ = np.vstack(status).astype(np.int8)
        self._df = self._df.loc[self._userid]
        log(f"Accelerometry: {', '.join(loaded)}")


    def column_values(self, name, default=np.nan):
        """
        Values of a column by its name, whatever topic it sits under

        Parameters
        ----------
        name : str
            Column name, e.g. "Survey" or "Season of year"
        default : object, default numpy.nan
            What to return when the table has no such column

        Returns
        -------
        ndarray
            Column values, or an array of `default`

        """
        for topic, column in self._df.columns:
            if column == name:
                return self._df[(topic, column)].to_numpy(copy=True)
        return np.full(len(self._df), default)


    def variable_type(self, name):
        """
        What kind of values a column holds, from the codebook

        Parameters
        ----------
        name : str
            Column name, e.g. "Age"

        Returns
        -------
        str
            "Continuous", "Categorical", "Binary", "Flag", "Text" or "Unknown".
            A column the codebook does not know (a computed one, such as
            "Insurance medical (any)") is read from its values instead

        """
        if name in self._meta:
            return self._meta[name]["type"]
        values = self._df[self.column_to_category_column(name)] \
            if name in self.columns() else None
        if values is None:
            return "Unknown"
        if values.dtype.kind not in "fiub":
            return "Text"
        unique = values.dropna().unique()
        if set(unique) <= {0.0, 1.0}:
            return "Binary"
        return "Continuous" if len(unique) > 12 else "Categorical"


    def truncated(self, name=None):
        """
        Which columns have a truncated end, and which end

        NHANES reports everybody older than 80 as 80, a laboratory result below
        the detection limit as a fill value, and so on. The values are usable
        numbers, but the tail of the distribution is not real: a mean is right,
        a 95th percentile or a maximum is not.

        Parameters
        ----------
        name : str or None, default None
            Column name; None - every truncated column

        Returns
        -------
        str or dict
            "top", "bottom", "bottom,top" or "" for one column, or
            {name: which end} for all of them

        Example
        -------
        >>> nhanes = pynhanes.NhanesLoader()
        >>> nhanes.truncated("Age")
        'top'

        """
        if name is not None:
            return self._meta.get(name, {}).get("truncated", "")
        return {n: m["truncated"] for n, m in self._meta.items() if m["truncated"]}


    def encode(self, names=None, onehot=True):
        """
        Table of numbers ready for scikit-learn, one column per feature

        Continuous and Yes/No columns are taken as they are, categorical ones
        are spread into one column per answer ("Ethnicity = Mexican American"),
        and text columns are left out. Which is which comes from the codebook
        column "Type".

        Parameters
        ----------
        names : list or None, default None
            Columns to encode; None - all of them
        onehot : bool, default True
            Spread categorical columns into one column per answer; False -
            keep the answer codes as numbers

        Returns
        -------
        DataFrame
            One row per participant, one column per feature

        Example
        -------
        >>> nhanes = pynhanes.NhanesLoader()
        >>> x = nhanes.encode(["Age", "Gender", "Ethnicity"])

        """
        names = list(self.columns()) if names is None else list(names)
        parts, skipped = {}, []
        for name in names:
            if name not in self.columns():
                skipped.append(name)
                continue
            values = self._df[self.column_to_category_column(name)]
            kind = self.variable_type(name)
            if kind == "Text" and not (onehot and name in ("Survey", "Season of year")):
                skipped.append(name)
                continue
            if kind in ("Categorical", "Flag") or values.dtype.kind not in "fiub":
                if not onehot:
                    parts[name] = pd.to_numeric(values, errors="coerce")
                    continue
                labels = self._meta.get(name, {}).get("labels", {})
                readable = values.map(lambda v: labels.get(int(v), v)
                                      if isinstance(v, float) and np.isfinite(v)
                                      and int(v) in labels else v)
                dummies = pd.get_dummies(readable.astype("object"), prefix=name,
                                         prefix_sep=" = ", dtype=float)
                dummies[values.isna()] = np.nan
                for column in dummies.columns:
                    parts[column] = dummies[column]
                continue
            parts[name] = values.astype(float)
        if skipped:
            warnings.warn(f"{len(skipped)} column(s) hold text and are left out of encode(): "
                          f"{', '.join(skipped[:6])}")
        return pd.DataFrame(parts, index=self._df.index)


    def column_to_category_column(self, column):
        """
        Get (category, column) by column name

        Parameters
        ----------
        column : str
            Column name
        
        Returns
        -------
        tuple
            (category, column) tuple
        
        """
        cols = np.array(self._df.columns.to_list()).T
        dct = dict(zip(cols[1], cols[0]))
        return (dct[column], column)


    def userdata(self, field, cond=None, userid=None):
        """
        Get values of userdata (for selected user ids)

        Parameters
        ----------
        field : str
            Human-readable name of NHANES data field
            Use .columns() to list available field names
        cond : str or None, default None
            If given, apply condtion to modify values (e.g. ">= 4")
        userid : ndarray of None, dfault None
            If given, output values only for selected user ids
        
        Returns
        -------
        ndarray
            NHANES field values for selected user ids

        Example
        -------
        >>> nhanes = pynhanes.NhanesLoader()
        >>> frailty = nhanes.userdata("Health general", ">= 4")

        """
        if userid is None:
            userid = self.userid
        val = np.zeros((len(userid))) * np.nan
        if field.lower() == "const":
            val = np.ones((len(userid)))
        categ = np.array(self._df.columns.to_list()).T
        categ = dict(zip(categ[1], categ[0]))
        key = [k for k in categ.keys() if k.lower() == field.lower()]
        key = key[0] if len(key) else field
        if key in categ:
            col = (categ[key], key)
            dct = self._df[col].to_dict()
            val = np.vectorize(dct.get)(userid)
        if cond is not None:
            ops = {">": operator.gt, "<": operator.lt, "==": operator.eq,
                   ">=": operator.ge, "<=": operator.le}
            op, v = cond.split()
            mask = np.isfinite(val)
            val = (ops[op](val, float(v))).astype(float)
            val[~mask] = np.nan

        return val


    def print_summary(self, col=None, codebook=None):
        """
        Print available userdata fileds summary

        Parameters
        ----------
        codebook : pynhanes.CodeBook object or None, default None
            Print fileds dictionary (optional)
        
        """
        if col is not None:
            column = col if isinstance(col, tuple) else self.column_to_category_column(col)
            val = self._df[column].values
            nan = np.sum(np.isnan(val))
            nan = np.clip(int(100 * nan / len(val)), 1, 100) if nan else 0
            nan = f"-- {nan}% NaN" if nan else ""
            unique = np.unique(val)
            name = column[-1]
            kind = self.variable_type(name)
            end = self.truncated(name)
            end = f" -- {end} of the range is truncated" if end else ""
            print(col)
            print(f"{kind}{end}")
            print(unique, nan)
            if len(unique) <= 10 and codebook is not None:
                dct = codebook.dict[column[-1]]
                if all([u in dct for u in unique[np.isfinite(unique)]]):
                    print(dct)
            print()
        else:
            for col in self._df.columns:
                self.print_summary(col, codebook)
        return


    def generate_random_survey_date(self, seed=None):
        """
        Generate random survey date based on year and season (Winter/Summer)

        Parameters
        ----------
        seed : int or None, default None
            Random seed

        Returns
        -------
        ndarray
            Array of ordinal dates (1 = Jan 1st, 1 AD)

        """
        np.random.seed(seed)
        user_year = self.survey
        user_season = self.column_values("Season of year")
        if user_season.dtype.kind not in "fiub":
            user_season = np.where(pd.isna(user_season), 1.0,
                                   np.where(np.char.lower(user_season.astype(str)) == "summer",
                                            2.0, 1.0))
        user_season = user_season.astype(float)
        user_season[~np.isfinite(user_season)] = 1
        idate_min = datetime.strptime(f"{int(np.nanmin(user_year))}", "%Y").toordinal()
        idate_max = datetime.strptime(f"{int(np.nanmax(user_year)) + 2}", "%Y").toordinal()
        idate = np.arange(idate_min, idate_max)
        weekday = (idate + 6) % 7 + 1
        idate = idate[weekday == 1] # Keep only Mondays
        date = [datetime.fromordinal(i) for i in idate]
        year = np.array([d.year for d in date])
        month = np.array([d.month for d in date])
        season = 1 + (((month + 1) % 12) >= 6).astype(int)
        nuser = len(user_year)
        user_idate = np.zeros((nuser)).astype(int)
        for i in range(nuser):
            mask = (year == user_year[i]) | (year == user_year[i] + 1)
            mask = mask & (season == user_season[i])
            user_idate[i] = np.random.choice(idate[mask])
        return user_idate


    @staticmethod
    def week2day(x):
        """
        Sample-wise average activity data over all days of the week

        Parameters
        ----------
        x : ndarray
            2D array of size N samples x 10080 minutes (7 x 24 x 60)
        
        Returns
        -------
        ndarray
            2D array of size N samples x 1440 minutes (1 x 24 x 60)

        """
        n, m = x.shape
        m = m // 1440
        x_ = np.copy(x).reshape(n,m,1440)
        mask = (np.sum(x_ > 0, axis=-1) >= 30) & (np.sum(x_ <= 0, axis=-1) >= 30)
        x_[~mask] = np.nan
        mask = np.sum(mask, axis=-1) >= m // 2
        x_[~mask] = np.nan
        x_ = np.nanmean(x_, axis=1)
        return x_


    @staticmethod
    def week2hour(x):
        """
        Sample-wise average activity data over hours of the day

        Parameters
        ----------
        x : ndarray
            1D array of length 10080 (7 days x 24 hours x 60 min = 10080)

        Returns
        -------
        ndarray
            1D array of length 1440 (24 hours x 60 min = 1440)

        """
        nbins = 24
        x_ = x.reshape(-1,1440)
        mask = np.sum(x_ > 1, axis = 1) >= 30
        if np.sum(mask) >= 3:
            x_ = x_[mask]
            nday = x_.shape[0]
            x_ = x_.reshape(nbins * nday, -1)
            x_ = np.nanmean(x_, axis=1).reshape(nday, -1)
            x_ = np.nanmean(x_, axis=0)
        else:
            x_ = np.zeros((nbins)) * np.nan
        return x_




class CodeBook():
    """
    Class to load NHANES codebook CSV

    Parameters
    ----------
    path_csv : str, default '~/data/NHANES/CSV/nhanes_codebook.csv'
        Path to the codebook csv file
    variables : list or str, default '~/data/NHANES/CSV/nhanes_variables.json'
        List or path to .json containing requested variable or category codes / combined
        names. A path that is not there falls back to the file that ships with pynhanes


    Example
    -------
    >>> import pynhanes
    >>> codebook = pynhanes.CodeBook("./CSV/nhanes_codebook.csv")

    """
    def __init__(self, path_csv="~/data/NHANES/CSV/nhanes_codebook.csv",
                       variables="~/data/NHANES/CSV/nhanes_variables.json"):
        self._load_codebook(path_csv)
        self._load_variables(variables)


    @staticmethod
    def _text_to_dict(text, digitize=False):
        """
        Convert json-style str of dict to python dict with int keys


        Parameters
        ----------
        text : str
            Dictionary in json-style str format
        digitize : bool, default False
            If True - output only keys converted to int

        Returns
        -------
        dct : dict
            Python dictionary with int keys

        """
        dct = json.loads(text)
        if digitize:
            dct = {int(key): val for key, val in dct.items() if key.isdigit()}
            dct = {key: dct[key] for key in sorted(list(dct.keys()))}
        return dct


    def _load_codebook(self, path):
        """
        Load NHANES codebook


        Parameters
        ----------
        path : str
            Path to user data csv, e.g. '~/data/NHANES/CSV/nhanes_userdata.csv'

        """
        fname = os.path.expanduser(path)
        df = pd.read_csv(fname, delimiter=";", index_col=0)
        # the labels of the parsed table, not of the file NHANES publishes:
        # "Refused" and "Don't know" are empty by then, and No is 0 rather
        # than 2, so offering them would name values that cannot occur
        source = "Recoded Codebook" if "Recoded Codebook" in df.columns else "Codebook"
        df["Int Codebook"] = [self._text_to_dict(c, True) for c in df[source].values]
        df["Codebook"] = [self._text_to_dict(c, False) for c in df["Codebook"].values]
        self._codebook = df["Int Codebook"].to_dict()
        columns = ["Name", "Combined Name", "Categories", "Category", "Category Name",
                   "Combined Category", "Component", "Type", "Truncated", "Core",
                   "Codebook", "Int Codebook", "Recoded Codebook",
                   "Surveys", "Availability"]
        columns += [c for c in df.columns if c[:2] in ("19", "20")]      # one per survey
        # "Type", "Truncated" and "Core" were added in 1.0.0: an older codebook has none
        self._data = df[[c for c in columns if c in df.columns]]
        return


    def _load_variables(self, variables):
        """
        Load variable human-readable names to codes dictionary


        Parameters
        ----------
        variables : str or dict
            Path to human-readable names .json file, or explicit 
            dictiontionary of human-readable varable names

        """
        dct = {}
        if isinstance(variables, dict):
            dct.update(variables)
        elif os.path.exists(os.path.expanduser(variables)):
            dct = load_variables(variables)
            for key, val in dct.items():
                decoder = {}
                for v in val[::-1]:
                    decoder.update(self._codebook[v])
                decoder = {key: decoder[key] for key in sorted(list(decoder.keys()))}
                dct[key] = decoder
                # Fix dictionary for 'Diabetes' special field
                if key == "Diabetes":
                    dct[key] = {0: "No", 1: "Yes"}
                # Fix dictionary for 'Smoking status' (combined field SMQ020/SMQ040)
                if key == "Smoking status":
                    dct[key] = {0: "Never", 1: "Quit", 2: "Current"}
        self._codevar = dct
        return


    @property
    def data(self):
        """
        Get codebook dataframe

        Returns
        -------
        Dataframe
            Codebook dataframe
        
        """
        return self._data


    @property
    def dict(self):
        """
        Get variable codes-to-descriptions dictionary


        Parameters
        ----------
        key : str or None, default None
            Variable name


        Returns
        -------
        dict
            Variable codes-to-descriptions dictionary

        """
        dct = self._codebook
        dct.update(self._codevar)
        dct["Poverty status"] = {0: "Poor", 1: "Middle", 2: "Rich"}
        dct["Unemployment status"] = {1: "Other", 2: "School", 3: "Retired"}
        dct["Sleep hours (status)"] = {5: "5 hours or less", 6: "6 hours", 7: "7 hours", 8: "8 hours", 9: "9 hours or more"}
        dct["Occupation hours worked (status)"] = {0: "0 hours/week", 1: "0 - 25 hours/week", 2: "25 - 35 hours/week", 3: "35 - 45 hours/week", 4: "45 or more hours/week"}
        dct["Health physical poor (status)"] = {0: "0 days/month", 1: "1 - 2 days/month", 2: "3 - 5 days/month", 3: "6 - 14 days/month", 4: "15 or more days/month"}
        dct["Health mental poor (status)"] = {0: "0 days/month", 1: "1 - 2 days/month", 2: "3 - 5 days/month", 3: "6 - 14 days/month", 4: "15 or more days/month"}
        return dct


import types
__all__ = [name for name, thing in globals().items()
          if not (name.startswith('_') or isinstance(thing, types.ModuleType))]
del types

