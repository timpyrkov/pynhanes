# -*- coding: utf-8 -*-

import sys

from pynhanes import scraper
from pynhanes import downloader
from pynhanes import parser
from pynhanes import activity
from pynhanes import userdata

# the names these three modules had in 0.0.22, kept so old code keeps working
nhanes_scraper = scraper
nhanes_downloader = downloader
nhanes_parser = parser
download = downloader
sys.modules["pynhanes.nhanes_scraper"] = scraper
sys.modules["pynhanes.nhanes_downloader"] = downloader
sys.modules["pynhanes.nhanes_parser"] = parser
sys.modules["pynhanes.download"] = downloader

try:
    # plotting helpers: they need pylab, seaborn and statannotations, which the
    # download, scrape and parse scripts do not, so a plain install keeps working
    from pynhanes.utils import *
except ImportError as error:      # pragma: no cover - depends on what is installed
    _utils_error = error
from pynhanes.loader import *

try:
    from importlib.metadata import version, PackageNotFoundError
    __version__ = version("pynhanes")
except PackageNotFoundError:
    __version__ = "unknown"
