#!/usr/bin/env python
# -*- coding: utf8 -*-

import numpy as np
import pandas as pd
import pylab as plt
import seaborn as sns
from scipy import stats
from itertools import combinations
from statannotations.Annotator import Annotator


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
        Num of samples inside window should be greater than nmin, else NaN

    Returns
    -------
    t : ndarray
        1D array of range [0 - 100)
    xavg : ndarray
        1D array of average x inside window for age in [0 - 100)
    xstd : ndarray
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
        if np.sum(mask) > nmin:
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
        Num of samples inside window should be greater than nmin, else NaN

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
        Num of samples inside window should be greater than nmin, else NaN

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
        Num of samples inside window should be greater than nmin, else NaN

    Returns
    -------
    ndarray
        1D array of detrended values of length N samples

    """
    if gender is None:
        gender = np.zeros((len(age)))
    if dct is None:
        dct = age_gender_dict(x, age, gender=gender, window=window, nmin=nmin)
    age_ = age.astype(int)
    x_ = np.zeros_like(x) * np.nan
    for i, t in enumerate(age_):
        g = gender[i]
        if t in dct[g]:
            x_[i] = x[i] - dct[g][t]
    return x_


def plot_age_avg_std(x, age, window=1, nmin=1, color=None, label='', alpha=0.1, ax=None, **kwargs):
    """
    Plot x avg +/- std for 0 - 100 years

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Num of samples inside window should be greater than nmin, else NaN
    color : str or None
        Matplotlib color for plotted line
    label : str
        Matplotlib text label for plotted line
    alpha : float, default 0.1
        Filled area opacity (0.0 - 1.0)
    ax : matplotlib.pyplot.Axes object, default None
        Axes for plotting
    **kwargs
        Keyword arguments

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
    line, = plt.plot(t, xavg, color=color, label=label, **kwargs)
    color = line.get_color()
    plt.fill_between(t, xmin, xmax, color=color, alpha=alpha)
    return ax


def plot_age_fraction(x, age, window=1, nmin=0, cmap="jet", labels=None, nbin=5, alpha=0.2, ax=None, **kwargs):
    """
    Plot x == val fractions for 0 - 100 years

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    age : ndarray
        1D array of length N samples, yr
    window : int, default 1
        Aberaging window, yr
    nmin : int, default 1
        Num of samples inside window should be greater than nmin, else NaN
    cmap : str, default 'jet'
        Matplotlib color map to color fraction of each value
    labels : dict, or None, default None
        Matplotlib text label for plotted line
    nbin : int, default 5
        Number of bins (groups); Only applicable when n categories > 10
    alpha : float, default 0.2
        Filled area opacity (0.0 - 1.0)
    ax : matplotlib.pyplot.Axes object, default None
        Axes for plotting
    **kwargs
        Keyword arguments

    Returns
    -------
    ax : matplotlib.pyplot.Axes object
        Axes for plotting

    """
    if ax is None:
        ax = plt.gca()
    values = np.unique(x[np.isfinite(x)])[::-1]
    if len(values) > 10:
        x, labels = digitize(x, nbin)
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
        plt.fill_between(t, frac[i], frac[i+1], color=colors[i], alpha=alpha)
        plt.plot(t, frac[i+1], color=colors[i], label=label)
    plt.legend(loc='upper left')
    return ax


def digitize(x, nbin=5):
    """
    Digitize a continuous-range data into categorical
    with approximately equal size groups

    Parameters
    ----------
    x : ndarray
        1D array of length N samples
    nbin : int, default 5
        Number of bins (groups)

    Returns
    -------
    xbinned : ndarray
        Categorical id
    dct : dict
        Dictionary: id -> range of values

    """
    mask = np.isfinite(x)
    bins = np.arange(nbin + 1) / float(nbin)
    q = np.quantile(x[mask], bins)
    xbinned = np.digitize(x, q[1:-1]).astype(float)
    dct = {i: f"{q[i]:.1f}-{q[i+1]:.1f}" for i in range(len(q) - 1)}
    xbinned[~mask] = np.nan
    return xbinned, dct


def pvalue(x, y, n=100, niter=100, seed=0, func=stats.ks_2samp):
    """
    Calculate log-averaged pvalue

    Parameters
    ----------
    x, y : ndarray
        Two arrays of measurements, sample sizes can be different
    n : int, default 100
        Number of samples to draw each time from each distribution
    niter : int, default 100
        Number of interation to randomly draw samples from each distribution
    seed : int, default 0
        Numpy random seed
    func : function object, default stats.ks_2samp
        Function from scipy.stats to calculate p-value (should accept x, y 
        as the first two arguments, and return p as the second argument)

    Returns
    -------
    p : float
        Log-averaged p-value

    """
    p = []
    np.random.seed(seed)
    for i in range(niter):
        x_ = np.random.choice(x, n)
        y_ = np.random.choice(y, n)
        try:
            _, p_ = func(x_, y_)
        except:
            p_ = 1.0
        p.append(p_)
    p = np.array(p)
    p = np.exp(np.mean(np.log(p)))
    return p


def pvalues(x, y, dct, n=100, niter=100, seed=0, func=stats.ks_2samp):
    """
    Calculate log-averaged pvalues for combinations of categorical x

    Parameters
    ----------
    x, y : ndarray
        Two arrays of measurements, sample sizes can be different
    dct : dict
        Dictionary to cinvert x from numerical to text values
    n : int, default 100
        Number of samples to draw each time from each distribution
    niter : int, default 100
        Number of interation to randomly draw samples from each distribution
    seed : int, default 0
        Numpy random seed
    func : function object, default stats.ks_2samp
        Function from scipy.stats to calculate p-value (should accept x, y 
        as the first two arguments, and return p as the second argument)

    Returns
    -------
    pvals : dict
        Dictionary (label0, label1): log-averaged p-value

    """
    isnum = np.issubdtype(x.dtype, int) or np.issubdtype(x.dtype, float)
    x = np.vectorize(dct.get)(x) if isnum else x
    order = []
    for key, val in dct.items():
        if val in x and val not in order:
            order.append(val)
    pairs = list(combinations(order, 2))
    pvals = {}
    for pair in pairs:
        x0 = y[x == pair[0]]
        x1 = y[x == pair[1]]
        p = pvalue(x0, x1, n, niter, seed, func)
        pvals[pair] = p
    return pvals


def boxplot(x, y, xlabel, ylabel, dct, pvalues=None, 
                 cmap="viridis_r", figsize=(16,4), **kwargs):
    """
    Plot boxplot with pvalue annotations

    Parameters
    ----------
    x, y : ndarray
        Two arrays of measurements, sample sizes can be different
    xlabel : str
        Name of x (categorical) variable 
    ylabel : str
        Name of y (continuous) variable 
    dct : dict
        Dictionary to convert x from numerical to text values
    pvalues : dict
        Dictionary (label0, label1): log-averaged p-value
    cmap : str, default 'viridis_r'
        Matplotlib colormap name or Seaborn palette name
    figsize : tuple, default (16,4)
        Figure size
    **kwargs
        Keyword arguments

    Returns
    -------
    ax : matplotlib.pyplot.Axes object
        Axes for plotting

    """
    isnum = np.issubdtype(x.dtype, int) or np.issubdtype(x.dtype, float)
    x = np.vectorize(dct.get)(x) if isnum else x
    order = []
    for key, val in dct.items():
        if val in x and val not in order:
            order.append(val)
    df = pd.DataFrame({xlabel: x, ylabel: y})
    df.dropna(inplace=True)
    plt.figure(figsize=figsize, facecolor="white")
    plt.title(xlabel)
    ax = plt.gca()
    if len(df):
        ax = sns.boxenplot(x=xlabel, y=ylabel, data=df, order=order, palette=cmap, **kwargs)
    if pvalues is not None:
        # Add custom pvalue annotations:
        # https://github.com/trevismd/statannotations/blob/master/usage/example.ipynb
        pvals = {k: v for (k, v) in pvalues.items() if v < 0.05}
        pairs = list(pvals.keys())
        pvals = list(pvals.values())
        if len(pairs):
            annotator = Annotator(ax, pairs, data=df, x=xlabel, y=ylabel, order=order)
            annotator.configure(test=None, text_format='simple', 
                                verbose=0).set_pvalues(pvalues=pvals)
            annotator.annotate()
    plt.show()
    return ax


import types
__all__ = [name for name, thing in globals().items()
          if not (name.startswith('_') or isinstance(thing, types.ModuleType))]
del types

