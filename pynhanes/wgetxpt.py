#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
The download script of pynhanes 0.0.x, kept for the people who use it

It takes one data file code and a folder, exactly as before, and downloads that
data file for every survey. What changed is the inside: instead of calling
`wget` once per survey and per guessed address, it hands the work to
pynhanes.downloader, which reads the NHANES file listing, so it also
finds the surveys added after 2019, skips files already present, resumes an
interrupted download and never writes a half file.

    pynhanes-wgetxpt DEMO -o XPT

pynhanes-downloader does the same thing with more control:

    pynhanes-downloader -d DEMO -o XPT

"""

import os
import argparse

from pynhanes import downloader


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pynhanes-wgetxpt")
    parser.description = (f"Download one NHANES data file for every survey. "
                          f"example: {parser.prog} DEMO -o "
                          f"{os.path.expanduser('~/Downloads/XPT')}")
    parser.epilog = ("pynhanes-downloader -d DEMO -o XPT does the same, and takes "
                     "several data files, selected surveys and components.")
    parser.add_argument("xpt", help="name of xpt file, without the survey suffix, e.g. DEMO")
    parser.add_argument("-o", "--out", default="XPT", help="path to output folder")
    args = parser.parse_args(argv)

    try:
        download_plan = downloader.plan(output=args.out, data_files=args.xpt)
    except (ValueError, RuntimeError) as error:
        print(error)
        return 1
    print(download_plan.summary())
    downloader.run(download_plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
