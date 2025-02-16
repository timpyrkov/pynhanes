#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import numpy as np
import pandas as pd
from tqdm import tqdm


def weekday_to_iso(weakdays):
    """
    Change weekdays to ISO fromat numbering
    Original: 0 - Missing, 1 - Sunday, 2 - Monday, 3 - Tuesday, 4 - Wednesday, 5 - Thursday, 6 - Friday, 7 - Saturday
    New:      0 - Missing, 1 - Monday, 2 - Tuesday, 3 - Wednesday, 4 - Thursday, 5 - Friday, 6 - Saturday, 7 - Sunday
    
    """
    mask = weakdays == 0
    weakdays = (weakdays + 5) % 7 + 1
    weakdays[mask] = 0
    return weakdays


def weekday_refill(weekdays):
    """
    Fill missing weekday id
    
    """
    weekfull = []
    idx = np.arange(len(weekdays[0]))
    nday = len(idx) // 1440
    for j, w in enumerate(weekdays):
        mask = w > 0
        if np.sum(mask) > 0:
            n = idx[mask][0] // 1440
            i0 = w[w > 0][0] - n
            i = (np.arange(nday) + i0 - 1) % 7 + 1
            weekfull.append(np.repeat(i, 1440))
    weekfull = np.stack(weekfull).astype(np.uint8)
    return weekfull


def paxraw_parser(input_folder, output_folder, clip_steps=True, iso_weekday=True, roll_to_monday=True):
    """
    Parse activity of 2003-2004 and 2005-2006 batches.
    Counts to "nhanes_counts.npz", Steps to "nhanes_steps.npz"
    
    Notes
    -----
    "userid" is int64
    "counts" is np.float16
    "steps" is np.uint16 or np.uint8 (if clipped to range 0-255)
    "weekday" is np.uint8 (if ISO: 0 - Missing, 1 - Mon, 2 - Tue, 3 - Wed, 4 - Thu, 5 - Fri, 6 - Sat, 7 - Sun)
    no NaN; no log-scale
    
    Parameters
    ----------
    input_folder : str
        Path to folder containing "PAXRAW_C.XPT" and "PAXRAW_D.XPT"
    output_folder : str
        Path to folder to output "nhanes_counts.npz" and "nhanes_steps.npz"
    clip_steps : bool, default True
        Flag to clip steps/min from range 0-32768 to 0-255
    iso_weekday : bool, default True
        Flag to renumber days from 1 - Sunday to 1 - Monday
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is always the first day
    
    Example
    -------
    >>> paxraw_parser("./XPT", "./NPZ")

    """
    match = ['PAXRAW_C.XPT', 'PAXRAW_D.XPT']
    fnames = sorted(os.listdir(os.path.expanduser(input_folder)))
    fnames = [f for f in fnames if f.upper() in match]
    if len(fnames) == 0:
        print(f"ERROR! No PAXRAW_C.XPT or PAXRAW_D.XPT found in '{input_folder}'")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2003/DataFiles/PAXRAW_C.zip [0.4GB] [2.4GB unzipped]")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2005/DataFiles/PAXRAW_D.zip [0.5GB] [2.8GB unzipped]")
        return
    df = [pd.read_sas(f"{input_folder}/{f}") for f in fnames]
    df = pd.concat(df, ignore_index=True)
    df.fillna(0, inplace=True)
    df = df.astype(int)
    
    # User list
    userid = df["SEQN"].values.astype(int)
    userlist = np.unique(userid)
    nuser = len(userlist)

    # Get index of each measurement
    idxs = df["PAXN"].values.astype(int) - 1

    # Get activity counts
    all_counts = df["PAXINTEN"].values.astype(np.float16)

    # Get and clip steps
    all_steps = df["PAXSTEP"].values.astype(np.uint16)
    if clip_steps:
        all_steps = np.clip(all_steps, 0, 255).astype(np.uint8)

    # Get weekdays
    all_week = df["PAXDAY"].values.astype(int)
    iso_week = weekday_to_iso(all_week).astype(np.uint8)
    all_week = all_week.astype(np.uint8)

    count = []
    step = []
    week = []
    roll = []
    for i, uid in enumerate(tqdm(userlist)):
        usermask = userid == uid
        idx = idxs[usermask]
        # Roll to Monday if roll_to_monday == True
        useriso = iso_week[usermask]
        k = 0 # No roll
        if roll_to_monday:
            k = 1440 * (useriso[0] - 1)
        roll.append(k)
        # Get user weekdays
        x = np.zeros((10080))
        x[idx] = all_week[usermask]
        if iso_weekday:
            x = weekday_to_iso(x)
        x = x if k == 0 else np.roll(x, k)
        week.append(x)
        # Get user activity counts
        x = np.zeros((10080))
        x[idx] = all_counts[usermask]
        x = x if k == 0 else np.roll(x, k)
        count.append(x)
        # get user step counts
        x = np.zeros((10080))
        x[idx] = all_steps[usermask]
        x = x if k == 0 else np.roll(x, k)
        step.append(x)
    count = np.stack(count).astype(np.float16)
    step = np.stack(step).astype(np.uint16)
    week = np.stack(week).astype(np.uint8)
    week = weekday_refill(week)
    roll = np.array(roll)
    if clip_steps:
        step = step.astype(np.uint8)

    # Make save folder
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    # Save activity counts
    svdict = {
        "userid": userlist, 
        "counts": count,
        "weekday": week,
        "Rolled by index": roll,
        "ISO weekday": iso_weekday,
    }
    np.savez_compressed(output_folder + "/nhanes_counts.npz", **svdict)

    # Save step counts
    mask = np.max(step, axis=1) > 0
    svdict = {
        "userid": userlist[mask], 
        "steps": step[mask],
        "weekday": week[mask],
        "Rolled by index": roll[mask],
        "ISO weekday": iso_weekday,
        "Steps clipped to 0-255": clip_steps,
    }
    np.savez_compressed(output_folder + "/nhanes_steps.npz", **svdict)

    return


def paxmin_parser(input_folder, output_folder, iso_weekday=True, roll_to_monday=True):
    """
    Parse activity of 2011-2012 and 2013-2014 batches.
    Full data to "nhanes_triax_full.npz", Weekly data to "nhanes_triax.npz"
    
    Notes
    -----
    "userid" is int64
    "triax" is np.float16
    "lumin" is np.float16
    "status" is np.uint8 (0 - Missing, 1 - Wake wear, 2 - Sleep wear, 3 - Non wear, 4  - Unknown)
    "weekday" is np.uint8 (if ISO: 0 - Missing, 1 - Mon, 2 - Tue, 3 - Wed, 4 - Thu, 5 - Fri, 6 - Sat, 7 - Sun)
    no NaN; no log-scale

    Parameters
    ----------
    input_folder : str
        Path to folder containing "PAXMIN_G.XPT", "PAXMIN_H.XPT", "PAXDAY_G.XPT", and "PAXDAY_H.XPT"
    output_folder : str
        Path to folder to output "nhanes_triax.npz" and "nhanes_triax_full.npz"
    iso_weekday : bool, default True
        Flag to renumber days from 1 - Sunday to 1 - Monday
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is always the first day (applied only to "nhanes_triax.npz")
    
    Example
    -------
    >>> paxmin_parser("./XPT", "./NPZ")

    """
    # Collect file names
    dct = {}
    match = ['PAXMIN_G.XPT', 'PAXMIN_H.XPT', 'PAXDAY_G.XPT', 'PAXDAY_H.XPT']
    fnamelist = sorted(os.listdir(os.path.expanduser(input_folder)))
    for fname in fnamelist:
        if fname.upper() in match:
            dct[fname.upper()] = fname
    fnamelist = dct.keys()
    if 'PAXMIN_G.XPT' in fnamelist and 'PAXDAY_G.XPT' not in fnamelist:
        print(f"ERROR! 'PAXMIN_G.XPT' needs 'PAXDAY_G.XPT' in '{input_folder}'.")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2011/DataFiles/PAXDAY_G.xpt [6.2MB]")
    #     return
    if 'PAXMIN_H.XPT' in fnamelist and 'PAXDAY_H.XPT' not in fnamelist:
        print(f"ERROR! 'PAXMIN_H.XPT' needs 'PAXDAY_H.XPT' in '{input_folder}'.")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2013/DataFiles/PAXDAY_H.xpt [7.0MB]")
        return

    # Read t0 from PAXDAY
    fnames = [v for k, v in dct.items() if k.upper() in ['PAXDAY_G.XPT', 'PAXDAY_H.XPT']]
    if len(fnames) == 0:
        print(f"ERROR! No PAXDAY_G.XPT or PAXDAY_H.XPT found in '{input_folder}'")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2011/DataFiles/PAXDAY_G.xpt [6.2MB]")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2013/DataFiles/PAXDAY_H.xpt [7.0MB]")
        return
    df = [pd.read_sas(f"{input_folder}/{f}") for f in fnames]
    df = pd.concat(df, ignore_index=True)
    def time_to_min(t):
        h, m, s = t.split(':')
        return int(m) + 60 * int(h)
    iday = df['PAXDAYD'].values.astype(int)
    imin = np.array([time_to_min(t) for t in df['PAXMSTD'].values.astype(str)])
    userid = df['SEQN'].values.astype(int)
    userlist = np.unique(userid)
    t0 = {}
    for uid in userlist:
        # Accelerometry always starts at first day, so we only need to know start time at first day
        mask = (userid == uid) & (iday == 1) 
        t0[uid] = imin[mask][0]

    # Read minutely activity, lux, status from PAXMIN
    fnames = [v for k, v in dct.items() if k.upper() in ['PAXMIN_G.XPT', 'PAXMIN_H.XPT']]
    if len(fnames) == 0:
        print(f"ERROR! No PAXMIN_G.XPT or PAXMIN_H.XPT found in '{input_folder}'")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2011/DataFiles/PAXMIN_G.xpt [7.6GB]")
        print("Please download https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2013/DataFiles/PAXMIN_H.xpt [8.7GB]")
        return
    df = [pd.read_sas(f"{input_folder}/{f}") for f in fnames]
    df = pd.concat(df, ignore_index=True)
        
    # User list
    userid = df["SEQN"].values.astype(int)
    userlist = np.unique(userid)
    nuser = len(userlist)

    # Get index of each measurement (Add t0 corresponding to userid, see below)
    idxs = df["PAXSSNMP"].values.astype(int) // 4800

    # Get light intensity, status, and triaxial accelerometry
    all_lumin = df["PAXLXMM"].values.astype(np.float16)
    all_status = df["PAXPREDM"].values.astype(np.uint8)
    all_triaxial = df["PAXMTSM"].values.astype(np.float16)
    # Near-zero values are read as 1e-79; replace with zero
    all_triaxial[all_triaxial < 1e-3] = 0

    # Get weekdays
    all_week = df["PAXDAYWM"].values.astype(int)
    iso_week = weekday_to_iso(all_week).astype(np.uint8)
    all_week = all_week.astype(np.uint8)

    week = []
    triax = []
    lumin = []
    status = []
    for q, uid in enumerate(tqdm(userlist)):
        usermask = userid == uid
        idx = idxs[usermask] + t0[uid]
        # Get weekdays
        x = np.zeros((12960))
        if iso_weekday:
            x[idx] = iso_week[usermask]
        else:
            x[idx] = all_week[usermask]
        week.append(x)
        # Get accelerometry
        x = np.zeros((12960))
        x[idx] = all_triaxial[usermask]
        triax.append(x)
        # Get luminosity
        x = np.zeros((12960))
        x[idx] = all_lumin[usermask]
        lumin.append(x)
        # Get predicted status
        x = np.zeros((12960))
        x[idx] = all_status[usermask]
        status.append(x)
    status = np.stack(status).astype(np.uint8)
    triax = np.stack(triax).astype(np.float16)
    lumin = np.stack(lumin).astype(np.float16)
    week = np.stack(week).astype(np.uint8)
    week = weekday_refill(week)

    # Save activity, luminocity, status
    if not os.path.isdir(output_folder):
        os.makedirs(output_folder)

    svdict = {
        "userid": userlist,
        "weekday": week,
        "triax": triax,
        "lumin": lumin,
        "status": status,
        "ISO weekday": iso_weekday,
    }
    np.savez_compressed(output_folder + "/nhanes_triax_full.npz", **svdict)
    paxmin_weekly(output_folder, roll_to_monday)
    return


def paxmin_weekly(output_folder, roll_to_monday=True):
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
    roll_to_monday : bool, default True
        Flag to roll arrays so that Monday is always the first day (applied only to "nhanes_triax.npz")

    Example
    -------
    >>> paxmin_weekly("./NPZ")
    """
    data = np.load(output_folder + "/nhanes_triax_full.npz")
    userlist = data["userid"]
    nuser = len(userlist)
    triax = data["triax"]
    lumin = data["lumin"]
    status = data["status"]
    weekday = data["weekday"]
    iso_weekday = data["ISO weekday"]

    nmins = np.sum(triax.reshape(-1,1440) > 1e-2, axis=1).reshape(nuser,-1)
    count = np.zeros((nuser,3))
    for j, uid in enumerate(userlist):
        for i in range(3):
            count[j,i] = np.sum(nmins[j][i:i+7])
    idx = np.argmax(count, axis=1)
    
    triax_ = []
    lumin_ = []
    status_ = []
    weekday_ = []
    roll = []
    for j, i in enumerate(idx):
        # index i0 and i1 to select 7 days (1 week) from 9 day-lomh record
        i0 = 1440 * i
        i1 = 1440 * (i + 7)
        # Get iso weekday and roll to Monday if roll_to_monday == True
        useriso = weekday[j][i0:i1]
        if not iso_weekday:
            useriso = weekday_to_iso(useriso)
        k = 0
        if roll_to_monday:
            k = 1440 * (useriso[0] - 1)
        roll.append(k)
        # Get user weekdays
        x = weekday[j][i0:i1]
        x = x if k == 0 else np.roll(x, k)
        weekday_.append(x)
        # Get user triaxial accelerometry
        x = triax[j][i0:i1]
        x = x if k == 0 else np.roll(x, k)
        triax_.append(x)
        # Get user luminocity
        x = lumin[j][i0:i1]
        x = x if k == 0 else np.roll(x, k)
        lumin_.append(x)
        # Get user status per minute
        x = status[j][i0:i1]
        x = x if k == 0 else np.roll(x, k)
        status_.append(x)
    triax = np.stack(triax_).astype(np.float16)
    lumin = np.stack(lumin_).astype(np.float16)
    status = np.stack(status_).astype(np.uint8)
    weekday = np.stack(weekday_).astype(np.uint8)
    roll = np.array(roll, dtype=int)
    
    # Save activity, luminocity, status
    svdict = {
        "userid": userlist,
        "weekday": weekday,
        "triax": triax,
        "lumin": lumin,
        "status": status,
        "Rolled by index": roll,
        "ISO weekday": iso_weekday,
    }
    np.savez_compressed(output_folder + "/nhanes_triax.npz", **svdict)
    return

