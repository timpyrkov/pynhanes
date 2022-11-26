#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import numpy as np
import pandas as pd
import operator
import json
from jsoncomment import JsonComment



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
    parser = JsonComment(json)
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
    path_npz : str, default '~/work/NHANES/NPZ/'
        Path to folder containing 'nhanes_counts.npz' and 'nhanes_triax.npz'

    """
    
    def __init__(self, path_csv="~/work/NHANES/CSV/nhanes_userdata.csv", path_npz="~/work/NHANES/NPZ/"):
        self._df = pd.read_csv(os.path.expanduser(path_csv), delimiter=";", index_col=0, header=[0,1])
        xnpz1 = np.load(f"{os.path.expanduser(path_npz)}/nhanes_counts.npz")
        xnpz2 = np.load(f"{os.path.expanduser(path_npz)}/nhanes_triax.npz")
        userid1 = xnpz1["userid"]
        userid2 = xnpz2["userid"]
        self._userid = np.concatenate([userid1, userid2])
        self._survey = np.array([2003] * len(userid1) + [2011] * len(userid2))
        self._x = np.vstack([xnpz1["counts"], xnpz2["triax"]]).astype(float)
        self._categ = np.vstack([np.zeros_like(xnpz1["counts"], np.int8), xnpz2["categ"]]).astype(np.int8)
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
    

    def x(self, no_flat=True):
        """
        Get array of physical activity.

        Parameters
        ----------
        no_flat : bool, default True
            If True, cnahge flat regions and outliers to zero

        Returns
        -------
        ndarray
            2D array of size N samples x 10080 minutes
        
        """
        x_ = np.copy(self._x)
        if no_flat:
            d = np.diff(x_, axis=1, append=x_[:,:1])
            mask = (x_ > 32000) & (d==0)
            x_[mask] = 0
        return x_


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
        categ = np.array(self._df.columns.to_list()).T
        categ = dict(zip(categ[1], categ[0]))
        if field in categ:
            col = (categ[field], field)
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
    variables : str, dict, or None, default None
        Path to human-readable names .json file, e.g. '~/work/NHANES/CSV/nhanes_variables.json',
        or explicit dictiontionary of human-readable varable names

    """

    def __init__(self, path_csv="~/work/NHANES/CSV/nhanes_codebook.csv", variables=None):
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
        path : str
            Path to variables csv, e.g. '~/work/NHANES/CSV/nhanes_variables.csv'

        """
        dct = {}
        if variables is not None:
            if isinstance(variables, dict):
                dct.update(variables)
            else:
                dct = load_variables(variables)
                dct = dct["code"].to_dict()
            for key, val in dct.items():
                dct[key] = self._codebook[val[0]]
                # Fix dictionary for 'Smoking status' (combined field SMQ020/SMQ040)
                if key == "Smoking status":
                    dct[key] = {0: "Never", 1: "Quit", 2: "Current"}
                # Fix dictionary for 'Poverty status' (digitized field INDFMPIR)
                if key == "Poverty status":
                    dct[key] = {0: "Poor", 1: "Middle", 2: "Rich"}
                # Fix dictionary for 'Diabetes' special field
                if key == "Diabetes":
                    dct[key] = {0: "No", 1: "Yes"}
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
        return dct


