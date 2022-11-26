#!/usr/bin/env python
# -*- coding: utf8 -*-

import numpy as np
import pylab as plt


def age_cohorts(age, dt=10):
    """
    Assign age cohort indices

    Example
    -------
    >>> plt.figure(figsize=(12,6), facecolor="w")
    >>> age_cohorts = pynhanes.utils.age_cohorts(age)
    >>> for i, (age_min, age_max) in age_cohorts.items():
    >>>     label = f"Age {age_min}-{age_max}"
    >>>     c = plt.get_cmap("viridis")(0.1*(i+1))
    >>>     mask = (age >= age_min) & (age < age_max)
    >>>     plt.plot(np.nanmean(x[mask], axis=0), color=c, label=label)
    >>> plt.legend()
    >>> plt.show()

    """
    idx = age.astype(int) // dt
    dct = {i: (dt * i, dt * (i + 1)) for i in np.unique(idx)}
    return dct


def age_avg_std(x, age, window=1, nmin=1):
    """
    Calculate x avg and std for 0 - 100 years


    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN

    Returns
    -------
    t : ndarray
        1D array of range [0 - 100)
    xavg : ndarray
        1D array of average x inside window for age in [0 - 100)
    xavg : ndarray
        1D array of standard deviation x inside window for age in [0 - 100)

    """
    assert isinstance(window, int) and window % 2 == 1
    t = np.arange(100)
    xavg = np.zeros((100)) * np.nan
    xstd = np.zeros((100)) * np.nan
    for i in range(100):
        age_min = i - window // 2
        age_max = i + window // 2 + 1
        mask = (age >= age_min) & (age < age_max) & np.isfinite(x)
        if np.sum(mask) >= nmin:
            xavg[i] = np.mean(x[mask])
            xstd[i] = np.std(x[mask])
    return t, xavg, xstd


def age_fraction(x, age, window=1, val=1, nmin=0):
    """
    Calculate x == val fraction for 0 - 100 years

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    val : int, default 1
        Specify fraction of which value to calculate
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN

    Returns
    -------
    t : ndarray
        1D array of range [0 - 100)
    frac : ndarray
        1D array of fraction of value inside window for age in [0 - 100)

    """
    assert isinstance(window, int) and window % 2 == 1
    t = np.arange(100)
    frac = np.zeros((100)) * np.nan
    for i in range(100):
        age_min = i - window // 2
        age_max = i + window // 2 + 1
        mask = (age >= age_min) & (age < age_max) & np.isfinite(x)
        if np.sum(mask) > nmin:
            n0 = np.sum(mask)
            n1 = np.sum(mask & (x == val))
            frac[i] = float(n1) / float(n0)
    return t, frac


def age_gender_dict(x, age, gender=None, window=1, nmin=0):
    """
    Precalculate averages for detrending values by age (and biological sex)
    Useful to deterend by a precalculated averages of specific cohort

    Parameters
    ----------
    x : ndarray
        1D array of values of length N samples
    age : ndarray
        1D array of length N samples, yr
    gender : ndarray or None, default None
        Optionally, 1D array of biological sex label
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN

    Returns
    -------
    dict
        Precalculated dictionary of age-gender cohort averages

    """
    dct = {}
    if gender is None:
        t, xavg, _ = age_avg_std(x, age, window=window, nmin=nmin)
        mask = np.isfinite(xavg)
        dct[0] = dict(zip(t[mask], xavg[mask]))
        dct[1] = dict(zip(t[mask], xavg[mask]))
    else:
        for g in range(2):
            mask = gender == g
            t, xavg, _ = age_avg_std(x[mask], age[mask], window=window, nmin=nmin)
            mask = np.isfinite(xavg)
            dct[g] = dict(zip(t[mask], xavg[mask]))
    return dct



def age_detrending(x, age, gender=None, dct=None, window=1, nmin=0):
    """
    Nonlinearly detrend value by age (and biological sex)

    Parameters
    ----------
    x : ndarray
        1D array of values of length N samples
    age : ndarray
        1D array of length N samples, yr
    gender : ndarray or None, default None
        Optionally, 1D array of biological sex label
    dct : dict or None, default None
        Optionally, precalculated dictionary of age-gender cohort averages
        Useful to deterend by a precalculated averages of specific cohort
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN

    Returns
    -------
    ndarray
        1D array of detrended values of length N samples

    """
    if dct is None:
        dct = age_gender_dict(x, age, gender=gender, window=window, nmin=nmin)
    if gender is None:
        gender = np.zeros((len(age)))
    age_ = age.astype(int)
    x_ = np.zeros_like(x) * np.nan
    for i, t in enumerate(age_):
        g = gender[i]
        if t in dct[g]:
            x_[i] = x[i] - dct[g][t]
    return x_


def plot_age_avg_std(x, age, window=1, nmin=1, color=None, label='', ax=None):
    """
    Plot x avg and std for 0 - 100 years

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN
    color : str or None
        Matplotlib color for plotted line
    label : str
        Matplotlib text label for plotted line
    ax : matplotlib.pyplot.Axes object, default None
        Axes for plotting

    Returns
    -------
    ax : matplotlib.pyplot.Axes object
        Axes for plotting

    """
    if ax is None:
        ax = plt.gca()
    t, xavg, xstd = age_avg_std(x, age, window=window, nmin=nmin)
    xmin = xavg - xstd
    xmax = xavg + xstd
    plt.plot(t, xavg, color=color, label=label)
    plt.fill_between(t, xmin, xmax, color=color, alpha=0.1)
    return ax


def plot_age_fraction(x, age, window=1, nmin=0, cmap="jet", labels=None, ax=None):
    """
    Plot x == val fractions for 0 - 100 years
    Plot x avg and std for 0 - 100 years

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Min num of samples inside window to output a data point, or NaN
    cmap : str, default 'jet'
        Matplotlib color map to color fraction of each value
    labels : dict, or None, default None
        Matplotlib text label for plotted line
    ax : matplotlib.pyplot.Axes object, default None
        Axes for plotting

    Returns
    -------
    ax : matplotlib.pyplot.Axes object
        Axes for plotting

    """
    if ax is None:
        ax = plt.gca()
    values = np.unique(x[np.isfinite(x)])[::-1]
    n = len(values)
    cmap = plt.get_cmap(cmap)
    colors = cmap(np.linspace(0, 1, n+2))[1:-1]
    if hasattr(cmap, 'colors') and len(cmap.colors) <= n:
        colors = cmap.colors
    frac = [age_fraction(x, age, window, val, nmin)[1] for val in values]
    frac = np.stack([np.zeros((100)), *frac])
    frac = np.cumsum(frac, axis=0)
    t = np.arange(len(frac[0]))
    for i, val in enumerate(values):
        label = str(val)
        if isinstance(labels, dict) and val in labels:
            label = labels[val]
        plt.fill_between(t, frac[i], frac[i+1], color=colors[i], alpha=0.2)
        plt.plot(t, frac[i+1], color=colors[i], label=label)
    plt.legend(loc='upper left')
    return ax








