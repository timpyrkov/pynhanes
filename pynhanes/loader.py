#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import numpy as np
import pandas as pd
from datetime import datetime
import operator
import json
import jsoncomment
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
    parser = jsoncomment.JsonComment(json)
    f = open(os.path.expanduser(path))
    dct = parser.load(f)
    f.close()
    return dct



class NhanesLoader():
    """
    Class to load userdata and accelerometry for NHANES samples.

    Notes
    -----
    Only userdata is loaded for samples of 2003-2006 and 2011-2014
    batches, that is for those who have accelerometry data. 

    Parameters
    ----------
    path_csv : str, default '~/work/NHANES/CSV/nhanes_userdata.csv'
        Path to user data csv file
    path_npz : str or None, default '~/work/NHANES/NPZ/'
        Path to folder containing 'nhanes_counts.npz' and 'nhanes_triax.npz'
        If accelerometry is loaded correctly, userid is shrinked to accelerometry subset

    """
    
    def __init__(self, path_csv="~/work/NHANES/CSV/nhanes_userdata.csv", path_npz="~/work/NHANES/NPZ/"):
        self._df = pd.read_csv(os.path.expanduser(path_csv), delimiter=";", index_col=0, header=[0,1])
        self._userid = self._df.index.values
        try:
            self.has_accelerometry = True
            xnpz1 = np.load(f"{os.path.expanduser(path_npz)}/nhanes_counts.npz")
            xnpz2 = np.load(f"{os.path.expanduser(path_npz)}/nhanes_triax.npz")
            userid1 = xnpz1["userid"]
            userid2 = xnpz2["userid"]
            self._userid = np.concatenate([userid1, userid2])
            self._x = np.vstack([xnpz1["counts"], xnpz2["triax"]]).astype(float)
            self._categ = np.vstack([np.zeros_like(xnpz1["counts"], np.int8), xnpz2["categ"]]).astype(np.int8)
            self._df = self._df.loc[self._userid]
        except FileNotFoundError as e:
            self.has_accelerometry = False
            self._x = np.zeros((len(self._userid),1)) * np.nan
            self._categ = np.zeros((len(self._userid))) * np.nan
        dct = self._df[("Demographic", "Survey")].to_dict()
        self._survey = 1997 + 2 * np.vectorize(dct.get)(self._userid)
        print('NUSERS', len(self.userid))


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
            print(col)
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
        user_season = self._df[("Demographic", "Season of year")].values
        user_season[~np.isfinite(user_season)] = 1
        idate_min = datetime.strptime(f"{user_year.min()}", "%Y").toordinal()
        idate_max = datetime.strptime(f"{user_year.max()+2}", "%Y").toordinal()
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
    path_csv : str, default '~/work/NHANES/CSV/nhanes_userdata.csv'
        Path to user data csv file
    variables : str or dict, default '~/work/NHANES/CSV/nhanes_variables.json'
        Path to human-readable names .json file, or explicit 
        dictiontionary of human-readable varable names

    """

    def __init__(self, path_csv="~/work/NHANES/CSV/nhanes_codebook.csv", 
                       variables="~/work/NHANES/CSV/nhanes_variables.json"):
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
            Path to user data csv, e.g. '~/work/NHANES/CSV/nhanes_userdata.csv'

        """
        fname = os.path.expanduser(path)
        df = pd.read_csv(fname, index_col=0)
        df["icodebook"] = [self._text_to_dict(c, True) for c in df["codebook"].values]
        df["codebook"] = [self._text_to_dict(c, False) for c in df["codebook"].values]
        self._codebook = df["icodebook"].to_dict()
        self._data = df
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

