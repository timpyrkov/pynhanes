#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
The userdata parser of pynhanes 0.0.x, replaced by pynhanes.parser

Every function below did one step of "read the .xpt files, decode them with the
codebook, write one table". That job is now one command:

    pynhanes-parser -i XPT -o CSV/nhanes_userdata.csv -v CSV/variables.json

or, from Python:

    import pynhanes
    table = pynhanes.parser.parse("XPT", "CSV/nhanes_userdata.csv",
                                         "CSV/nhanes_codebook.csv", variables)

The new parser does what the old functions did and fixes what they got wrong -
a variable stored in several data files is merged instead of dropped, "Refused"
and "Don't know" are read from the codebook of each variable, Yes/No becomes
1/0 once, and the derived variables (smoking status, blood pressure, poverty,
hospital stays, mortality in years) are all in one place. The documentation describes
every step. This module is kept for one release so that old code fails with an
explanation instead of a NameError, and will then be removed.

"""

import warnings


REPLACEMENT = {
    "list_xpts_missing": "pynhanes-parser -n (it reports the missing data files)",
    "list_xpts_loaded": "pynhanes-parser -n",
    "sort_xpts_loaded": "pynhanes-parser -n",
    "load_xpt": "pynhanes.parser.read_data",
    "load_xpts": "pynhanes.parser.read_data",
    "load_dat": "pynhanes.parser.read_data",
    "load_dats": "pynhanes.parser.read_data",
    "load_data": "pynhanes.parser.parse",
    "processing": "pynhanes.parser.parse (decoding, recoding and derived variables)",
    "decode_missing_values": "pynhanes.parser.decode_missing",
    "merge_columns": "pynhanes.parser.merge_codes",
    "merge_duplicated_columns": "pynhanes.parser.read_data (it merges them itself)",
    "variables_to_list": "pynhanes.parser.read_variables",
    "fname_to_categ": "pynhanes.parser.local_data_files",
    "get_drop_list": "pynhanes.parser.MISSING_LABELS",
    "single_digit_outlier": "the codebook value labels, see pynhanes.parser.missing_codes",
    "repeated_digit_outlier": "the codebook value labels, see pynhanes.parser.missing_codes",
    "suspected_outleirs": "the codebook value labels, see pynhanes.parser.missing_codes",
}


def _removed(name):
    """
    Build the function that replaces a removed one

    """
    def gone(*args, **kwargs):
        raise NotImplementedError(
            f"pynhanes.userdata.{name}() was removed in pynhanes 1.0.0. "
            f"Use {REPLACEMENT[name]}. The whole pipeline is now one command: "
            f"pynhanes-parser -i XPT -o CSV/nhanes_userdata.csv -v CSV/variables.json - "
            f"see the documentation.")
    gone.__name__ = name
    gone.__doc__ = f"Removed in 1.0.0, use {REPLACEMENT[name]}"
    return gone


def __getattr__(name):
    """
    Answer for a function that was removed (PEP 562), warn once, then raise on call

    """
    if name in REPLACEMENT:
        warnings.warn(f"pynhanes.userdata.{name}() is replaced by {REPLACEMENT[name]}; "
                      f"pynhanes.userdata will be removed in the next release.",
                      DeprecationWarning, stacklevel=2)
        return _removed(name)
    raise AttributeError(f"module 'pynhanes.userdata' has no attribute '{name}'")
