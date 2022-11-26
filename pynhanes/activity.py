#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import numpy as np
import pandas as pd
from tqdm import tqdm



def paxraw_parser(input_folder, output_folder):
    """
    Parse activity of 2003-2004 and 2005-2006 batches.
    Counts to "nhanes_counts.npz", Steps to "nhanes_steps.npz"
    
    Notes
    -----
    "userid" is int64
    "steps" is np.uint8
    "counts" is np.uint16
    no NaN; no log-scale
    
    Parameters
    ----------
    input_folder : str
        Path to folder containing "PAXRAW_C.XPT" and "PAXRAW_D.XPT"
    output_folder : str
        Path to folder to output "nhanes_counts.npz" and "nhanes_steps.npz"
    
    Example
    -------
    >>> paxraw_parser("./XPT", "./NPZ")

    """
    df = [pd.read_sas(input_folder + "/PAXRAW_C.XPT"),
          pd.read_sas(input_folder + "/PAXRAW_D.XPT")]
    df = pd.concat(df, ignore_index=True)
    df.fillna(0, inplace=True)
    df = df.astype(int)
    
    df["IDX"] = 1440 * ((df["PAXDAY"].values + 5) % 7) + \
                60 * df["PAXHOUR"].values + df["PAXMINUT"].values

    df["PAXSTEP"] = np.clip(df["PAXSTEP"].values, 0, 255)
    userlist = np.unique(df["SEQN"].values)

    acounts = []
    scounts = []
    for uid in tqdm(userlist):
        udf = df[df["SEQN"]==uid]
        idx = udf["IDX"].values
        # get activity counts
        x = np.zeros((10080))
        x[idx] = udf["PAXINTEN"].values
        acounts.append(x)
        # get step counts
        x = np.zeros((10080))
        x[idx] = udf["PAXSTEP"].values
        scounts.append(x)

    # save activity counts
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    acounts = np.stack(acounts).astype(np.float16)
    svdict = {"userid": userlist, "counts": acounts}
    np.savez_compressed(output_folder + "/nhanes_counts.npz", **svdict)
    scounts = np.stack(scounts).astype(np.uint8)
    mask = np.max(scounts, axis=1) > 0
    svdict = {"userid": userlist[mask], "steps": scounts[mask]}
    np.savez_compressed(output_folder + "/nhanes_steps.npz", **svdict)
    return


def paxmin_parser(input_folder, output_folder):
    """
    Parse activity of 2011-2012 and 2013-2014 batches.
    Full data to "nhanes_triax_full.npz", Weekly data to "nhanes_triax.npz"
    
    Notes
    -----
    "userid" is int64
    "weekday" is np.int8 (0 - Mon, 1 - Tue, 2 - Wed, 3 - Thu, 4 - Fri, 5 - Sat, 6 - Sun)
    "categ" is np.int8 (0 - Missing, 1 - Wake wear, 2 - Sleep wear, 3 - Non wear, 4  - Unknown)
    "lumin" is np.int16
    "triax" is np.float16
    no NaN; no log-scale

    Parameters
    ----------
    input_folder : str
        Path to folder containing "PAXMIN_G.XPT" and "PAXMIN_H.XPT"
    output_folder : str
        Path to folder to output "nhanes_triax_full.npz" and "nhanes_triax.npz"
    
    Example
    -------
    >>> paxmin_parser("./XPT", "./NPZ")

    """
    df = [pd.read_sas(input_folder + "/PAXMIN_G.XPT"),
          pd.read_sas(input_folder + "/PAXMIN_H.XPT")]
    df = pd.concat(df, ignore_index=True)
    
    userid = df["SEQN"].values.astype(int)
    userlist = np.unique(userid)
    nuser = len(userlist)

    iday = df["PAXDAYM"].values.astype(int) - 1 # 1-9 to 0-8
    weekday = df["PAXDAYWM"].values.astype(int) - 2
    weekday[weekday < 0] = 6

    istart = df["PAXSSNMP"].values.astype(int)
    imin = istart // 4800

    rawlux = df["PAXLXSM"].values.astype(np.int16)
    rawcat = df["PAXPREDM"].values.astype(np.int8)
    rawaxl = df["PAXMTSM"].values.astype(np.float16)
    rawaxl[rawaxl < 1e-3] = 1e-3
    
    n = nuser
    m = 9 * 1440
    axl = np.zeros((n,m)).astype(np.float16)
    lux = np.zeros((n,m)).astype(np.int16)
    cat = np.zeros((n,m)).astype(np.int8)
    week = np.zeros((n,8)).astype(np.int8)

    for q, uid in enumerate(tqdm(userlist)):
        usermask = userid == uid
        day = iday[usermask]
        idx = imin[usermask]

        user_week = np.arange(8) + weekday[usermask & (iday==0)][0]
        user_week = user_week % 7
        week[q] = user_week.astype(np.int8)

        n = np.sum(day == 0)
        idx = idx + 1440 - n

        user_axl = np.zeros((m)).astype(np.float16)
        user_lux = np.zeros((m)).astype(np.int16)
        user_cat = np.zeros((m)).astype(np.int8)

        user_axl[idx] = rawaxl[usermask]
        user_lux[idx] = rawlux[usermask]
        user_cat[idx] = rawcat[usermask]

        axl[q] = user_axl
        lux[q] = user_lux
        cat[q] = user_cat

    # save activity counts
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    svdict = {
        "userid": userlist,
        "weekday": week,
        "triax": axl[:,:11520],
        "lumin": lux[:,:11520],
        "categ": cat[:,:11520],
    }
    np.savez_compressed(output_folder + "/nhanes_triax_full.npz", **svdict)
    paxmin_weekly(output_folder)
    return


def paxmin_weekly(output_folder):
    """
    Weekly activity of 2011-2012 and 2013-2014 batches.
    
    Notes
    -----
    "userid" is int64
    "weekday" is np.int8 (0 - Mon, 1 - Tue, 2 - Wed, 3 - Thu, 4 - Fri, 5 - Sat, 6 - Sun)
    "categ" is np.int8 (0 - Missing, 1 - Wake wear, 2 - Sleep wear, 3 - Non wear, 4  - Unknown)
    "lumin" is np.int16
    "triax" is np.float16
    no NaN; no log-scale

    Parameters
    ----------
    output_folder : str
        Path to folder to read "nhanes_triax_full.npz" and output "nhanes_triax.npz"
    
    Example
    -------
    >>> paxmin_weekly("./NPZ")
    """
    xnpz = np.load(output_folder + "/nhanes_triax_full.npz")
    userlist = xnpz["userid"]
    nuser = len(userlist)
    triax = np.copy(xnpz["triax"])[:,1440:]
    lumin = np.copy(xnpz["lumin"])[:,1440:]
    categ = np.copy(xnpz["categ"])[:,1440:]
    week = xnpz["weekday"][:,1].astype(int)
    roll = 1440 * week
    for q, uid in enumerate(tqdm(userlist)):
        x = triax[q]
        y = lumin[q]
        z = categ[q]
        x = np.roll(x, roll[q])
        y = np.roll(y, roll[q])
        z = np.roll(z, roll[q])
        triax[q] = x
        lumin[q] = y
        categ[q] = z

    svdict = {
        "userid": userlist,
        "triax": triax,
        "lumin": lumin,
        "categ": categ,
    }
    np.savez_compressed(output_folder + "/nhanes_triax.npz", **svdict)
    return

