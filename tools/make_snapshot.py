#!/usr/bin/env python
"""
Refresh the codebook snapshot shipped with pynhanes, from freshly scraped files

Run before every release, after a full scrape:

    pynhanes-scraper -o CSV/nhanes_codebook.csv --availability --datafiles --refresh --store
    python tools/make_snapshot.py CSV

It gzips the three tables into pynhanes/data/ and writes snapshot.json with the
date, so that a fresh install has a codebook without reading the website.
"""
import gzip, json, os, shutil, sys, datetime
import pandas as pd

SOURCE = os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "CSV")
TARGET = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pynhanes", "data")
FILES = {"codebook": "nhanes_codebook.csv",
         "availability": "nhanes_availability.csv",
         "datafiles": "nhanes_datafiles.csv"}

os.makedirs(TARGET, exist_ok=True)
info = {"scraped": datetime.date.today().isoformat(), "files": {}}
for kind, name in FILES.items():
    src = os.path.join(SOURCE, name)
    dst = os.path.join(TARGET, name + ".gz")
    with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=9) as f_out:
        shutil.copyfileobj(f_in, f_out)
    info["files"][kind] = {"name": name + ".gz", "bytes": os.path.getsize(dst)}

book = pd.read_csv(os.path.join(SOURCE, FILES["codebook"]), sep=";", index_col=0,
                   keep_default_na=False)
info["variables"] = int(len(book))
info["data_files"] = int(book["Category"].nunique())
# the codebook carries one column of counts per survey
info["surveys"] = [c for c in book.columns if c[:2] in ("19", "20")]
with open(os.path.join(TARGET, "snapshot.json"), "w") as f:
    json.dump(info, f, indent=1)
    f.write("\n")
print(json.dumps(info, indent=1))
print("total", sum(f["bytes"] for f in info["files"].values()) / 1024 ** 2, "MB")
