#!/usr/bin/env python
# -*- coding: utf8 -*-

import os
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.description = f"example: {parser.prog} DEMO -o {os.path.expanduser('~/Downloads/XPT')}"
    parser.add_argument("xpt", help="name of xpt file")
    parser.add_argument("-o",  "--out", default="XPT", help="path to output folder")
    args = parser.parse_args()

    survey_years = [f"{y-1}-{y}" for y in range(2000,2020)[::2]]
    survey_suffix = [""] + [f"_{chr(65 + i)}" for i in range(1,10)]
    survey_suffix = dict(zip(survey_years, survey_suffix))

    path = os.path.expanduser(args.out)
    if not os.path.isdir(path):
        os.makedirs(path)

    for years in survey_years:
        suffix = survey_suffix[years]
        cmd = f"wget https://wwwn.cdc.gov/Nchs/Nhanes/{years}/{args.xpt}{suffix}.XPT -P {path}"
        os.system(cmd)

if __name__ == "__main__":
    main()
