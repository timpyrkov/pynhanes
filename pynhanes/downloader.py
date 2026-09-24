#!/usr/bin/env python
# -*- coding: utf8 -*-
"""
Downloader for NHANES data files (.xpt, zipped .xpt, and linked mortality .dat)

One command with six options: where to put files (-o), which surveys (-s),
which components (-c), which data files (-d), which minute-level activity
data (-a), and whether to re-download what is already there (-y).

Files already present are never downloaded again, interrupted downloads
resume, and -n shows what would happen without downloading anything.

Example
-------
>>> import pynhanes
>>> p = pynhanes.download.plan("XPT", surveys=2021, data_files="DEMO,BMX")
>>> print(p.summary())
>>> p.run()

Command line
------------
    pynhanes-download -h                      # options and workflow
    pynhanes-download -n                      # plan with sizes, nothing downloaded
    pynhanes-download -o ~/data/NHANES/XPT    # everything except activity data
    pynhanes-download -s 2021 -c Laboratory   # one survey, one component
    pynhanes-download -d DEMO,BMX,SMQ         # selected data files, all surveys
    pynhanes-download -a 2011,2013            # minute-level accelerometry (large)
    pynhanes-download -s 2021 -y              # re-download the latest survey

The legacy wgetxpt.py / pywgetxpt script is kept unchanged.

"""

import os
import re
import sys
import json
import time
import shutil
import zipfile
import argparse
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from tqdm import tqdm


BASE_URL = "https://wwwn.cdc.gov"
LISTING_URL = BASE_URL + "/nchs/nhanes/search/datapage.aspx?Component={component}&{query}"
MORTALITY_URL = ("https://ftp.cdc.gov/pub/Health_Statistics/NCHS/datalinkage/linked_mortality/"
                 "NHANES_{begin}_{end}_MORT_{year}_PUBLIC.dat")
MORTALITY_YEAR = 2019
COMPONENTS = ["Demographics", "Dietary", "Examination", "Laboratory", "Questionnaire"]
MORTALITY = "Mortality"
STATE_DIR = ".pynhanes"
CACHE_VERSION = 4          # bump when the cached listing format changes
# Finished surveys never change, so their listings are cached for a long time;
# the latest survey keeps receiving files, so it is re-read every day
LISTING_TTL_HOURS = 24
FINAL_LISTING_TTL_HOURS = 180 * 24
SURVEY_LIST_TTL_HOURS = 30 * 24
HOME_PAGE_URL = BASE_URL + "/nchs/nhanes/default.aspx"
# Cycles listed on the website that hold no public data files of their own
NO_PUBLIC_DATA = ["2017-2020", "2019-2020"]
WORKERS = 4
# Be a polite client: at most 1 request per MIN_REQUEST_INTERVAL seconds to the
# same server (downloads of file contents stream within one request), and
# retries with backoff when the server answers 429 / 5xx
MIN_REQUEST_INTERVAL = 0.2
USER_AGENT = "pynhanes-download (+https://github.com/timpyrkov/pynhanes)"

# Minute-level accelerometry, selected only with -a, plus companion files
# needed by the pynhanes.activity parsers
ACTIVITY_FILES = {2003: ["PAXRAW"], 2005: ["PAXRAW"],
                  2011: ["PAXMIN", "PAXDAY", "PAXHD"],
                  2013: ["PAXMIN", "PAXDAY", "PAXHD"]}

# Fallback expansion factor if the contents of a .zip can not be read
UNZIP_FACTOR = 6.5
# Data files whose variables the pynhanes parser scripts put into
# nhanes_userdata.csv ("core" preset, ~0.7 GB for all surveys). Everything
# else NHANES publishes is downloaded only with -d full.
PRESET_CORE = [
    "ACQ", "AGQ", "ALQ", "APOB", "AQQ", "ARQ", "BIOPRO", "BMX", "BPQ", "BPX",
    "CBC", "CDQ", "CRP", "DBQ", "DEMO", "DIQ", "DLQ", "DPQ", "DR1TOT", "DSQTOT",
    "DUQ", "GHB", "HIQ", "HOQ", "HSQ", "HUQ", "INQ", "L13_2", "LAB13", "MCQ",
    "MGX", "MORT", "MPQ", "OCQ", "OSQ", "PAQ", "PAQIAF", "PFQ", "RDQ",
    "RXQ_RX", "SLQ", "SMQ", "TBQ", "TCHOL", "TRIGLY", "TST",
    # the files that continue a core measurement under a new name. They are
    # separate data files with variable codes of their own, so nothing
    # collides: BPXO measures blood pressure with a different device, HSCRP
    # reports C-reactive protein in other units, FNQ asks the questions of
    # PFQ and DLQ, and HDL holds the cholesterol that LAB13 held until 2002
    "BPXO", "FNQ", "HDL", "HSCRP",
]

# Median size of one survey without accelerometry (measured 2026-09-17), used
# only to hint at the size of a survey whose file list is not published yet
TYPICAL_SURVEY_BYTES = int(0.41 * 1024 ** 3)

# Exit codes
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NO_SPACE = 3
EXIT_FAILED = 4


# ---------------------------------------------------------------------------
# Surveys
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Survey:
    """
    NHANES survey cycle

    Attributes
    ----------
    label : str
        Survey label, e.g. "2017-2018"
    begin : int
        First year, e.g. 2017
    suffix : str
        Data file name suffix, e.g. "_J"
    prefix : str
        Data file name prefix, e.g. "P_" for 2017-March 2020 pre-pandemic files
    query : str
        Query string of the NHANES data listing page
    latest : bool
        True if NHANES is still adding files to the survey
    discovered : bool
        True if the survey was found on the website and is not built into
        this version of pynhanes

    """
    label: str
    begin: int
    suffix: str = ""
    prefix: str = ""
    query: str = ""
    latest: bool = False
    discovered: bool = False

    @property
    def final(self):
        """
        True if NHANES is not expected to add or change files any more
        """
        return not (self.latest or self.discovered)

    @property
    def end(self):
        return int(self.label.split("-")[1])


def _all_surveys():
    surveys = []
    for i, begin in enumerate(range(1999, 2019, 2)):
        suffix = "" if i == 0 else f"_{chr(65 + i)}"
        surveys.append(Survey(f"{begin}-{begin + 1}", begin, suffix, "", f"CycleBeginYear={begin}"))
    surveys.append(Survey("2021-2023", 2021, "_L", "", "CycleBeginYear=2021", latest=True))
    return surveys


SURVEYS = _all_surveys()
PREPANDEMIC = Survey("2017-2020", 2017, "", "P_", "Cycle=2017-2020")


def discover_surveys(log=print):
    """
    Read the survey list from one NHANES page and return surveys that are
    not built into this version of pynhanes (cached for SURVEY_LIST_TTL_HOURS).
    Returns an empty list if the page can not be read.

    Returns
    -------
    list
        List of Survey objects

    """
    path = os.path.join(listing_cache_dir(), "surveys.json")
    data = _read_json(path, None)
    if not (data and data.get("version") == CACHE_VERSION
            and time.time() - data.get("fetched", 0) < SURVEY_LIST_TTL_HOURS * 3600):
        try:
            _throttle(HOME_PAGE_URL)
            response = _thread_session().get(HOME_PAGE_URL, timeout=(15, 120))
            response.raise_for_status()
            labels = sorted(set(re.findall(r"Cycle=((?:19|20)\d{2}-(?:19|20)\d{2})",
                                           response.text)))
            data = {"version": CACHE_VERSION, "fetched": time.time(), "labels": labels}
            _write_json(path, data)
        except (requests.RequestException, OSError) as e:
            log(f"Could not read the survey list from the NHANES website ({e.__class__.__name__}); "
                f"using the surveys built into pynhanes.")
            return []
    known = {s.label for s in SURVEYS} | set(NO_PUBLIC_DATA)
    found = []
    for label in data.get("labels", []):
        if label in known:
            continue
        begin = int(label.split("-")[0])
        # the file name suffix of a new survey is unknown, so it is stripped
        # generically (see _file_code)
        found.append(Survey(label, begin, "", "", f"Cycle={label}", discovered=True))
    return sorted(found, key=lambda s: s.begin)


def known_surveys(log=print):
    """
    Surveys built into pynhanes plus any newer ones found on the website

    Returns
    -------
    list
        List of Survey objects in chronological order

    """
    return SURVEYS + discover_surveys(log)


def documented_surveys(log=print):
    """
    Every survey that has documentation pages: the two-year cycles, the
    2017-March 2020 pre-pandemic release, and any newer cycle the website
    lists. This is what the codebook covers - unlike a download, reading the
    documentation of two releases of the same data does no harm.

    Returns
    -------
    list
        List of Survey objects in chronological order

    """
    surveys = SURVEYS + [PREPANDEMIC] + discover_surveys(log)
    return sorted(surveys, key=lambda s: (s.begin, s.label))


def resolve_surveys(surveys=None, extra=None):
    """
    Resolve survey specification to a list of Survey objects

    Parameters
    ----------
    surveys : str, int, list or None, default None
        First year of a survey (1999 ... 2017, 2021), survey label
        ("2017-2018"), comma-separated string, "prepandemic" for the
        2017-March 2020 pre-pandemic files, or None for all surveys
        (pre-pandemic files are excluded from None because they duplicate
        the 2017-2018 survey)
    extra : list or None, default None
        Additional surveys found on the website, see discover_surveys();
        they can be selected explicitly, but are not part of the default None

    Returns
    -------
    list
        List of Survey objects in chronological order

    """
    available = SURVEYS + list(extra or [])
    if surveys is None:
        # a newer survey is reported, but not selected by default: it usually
        # has no files yet, and pynhanes does not know its file name suffix
        return list(SURVEYS)
    tokens = surveys if isinstance(surveys, (list, tuple, set)) else [surveys]
    tokens = [t for token in tokens for t in str(token).split(",")]
    selected = []
    for token in tokens:
        t = token.strip().upper()
        if not t:
            continue
        if t in ("PREPANDEMIC", "2017-2020"):
            found = [PREPANDEMIC]
        else:
            found = [s for s in available if t in (str(s.begin), s.label)]
        if not found:
            valid = ", ".join(str(s.begin) for s in available)
            raise ValueError(f"Invalid survey '{token}'. Use the first year of a survey "
                             f"({valid}), or 'prepandemic'.")
        selected += [s for s in found if s not in selected]
    return sorted(selected, key=lambda s: (s.begin, s.label))


def resolve_components(components=None):
    """
    Resolve component specification to a list of component names

    Parameters
    ----------
    components : str, list or None, default None
        One of, or comma-separated list of Demographics, Dietary, Examination,
        Laboratory, Questionnaire, Mortality; None means all of them

    Returns
    -------
    list
        List of component names

    """
    if components is None:
        return COMPONENTS + [MORTALITY]
    tokens = components if isinstance(components, (list, tuple, set)) else [components]
    tokens = [t for token in tokens for t in str(token).split(",")]
    known = {c.lower(): c for c in COMPONENTS + [MORTALITY]}
    selected = []
    for token in tokens:
        t = token.strip().lower()
        if not t:
            continue
        if t not in known:
            raise ValueError(f"Invalid component '{token}'. Use one or more of: "
                             f"{', '.join(COMPONENTS + [MORTALITY])}.")
        if known[t] not in selected:
            selected.append(known[t])
    return selected


def resolve_activity(activity=None):
    """
    Resolve minute-level accelerometry specification to a list of first years

    Parameters
    ----------
    activity : str, int, list or None, default None
        First year of a survey with minute-level accelerometry
        (2003, 2005, 2011, 2013), comma-separated, or "all";
        None means no accelerometry data (it is large, so it has to be
        asked for explicitly)

    Returns
    -------
    list
        List of first years

    """
    if activity is None:
        return []
    tokens = activity if isinstance(activity, (list, tuple, set)) else [activity]
    tokens = [t for token in tokens for t in str(token).split(",")]
    years = []
    for token in tokens:
        t = token.strip().upper()
        if not t:
            continue
        if t == "ALL":
            years += list(ACTIVITY_FILES)
            continue
        found = [s.begin for s in SURVEYS
                 if t in (str(s.begin), s.label) and s.begin in ACTIVITY_FILES]
        if not found:
            valid = ", ".join(str(y) for y in sorted(ACTIVITY_FILES))
            raise ValueError(f"Invalid activity survey '{token}'. Minute-level accelerometry is "
                             f"available for: {valid} (or 'all').")
        years += found
    return sorted(set(years))


def resolve_output(output="XPT"):
    """
    Resolve, create, and check the output folder

    Parameters
    ----------
    output : str, default "XPT"
        Path to the folder for downloaded files

    Returns
    -------
    str
        Absolute path of a readable and writable folder

    """
    path = os.path.abspath(os.path.expanduser(output or "XPT"))
    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError(f"Output path '{path}' exists but is not a folder.")
    if not os.path.exists(path):
        try:
            os.makedirs(path)
        except OSError as e:
            raise ValueError(f"Can not create output folder '{path}': {e}")
    if not os.access(path, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError(f"Output folder '{path}' is not readable and writable.")
    return path


# ---------------------------------------------------------------------------
# Remote file listings
# ---------------------------------------------------------------------------

@dataclass
class RemoteFile:
    """
    Data file available for download

    Attributes
    ----------
    name : str
        Data file name, e.g. "DEMO_J"
    code : str
        Data file code without survey suffix / prefix, e.g. "DEMO"
    component : str
        Component, e.g. "Demographics"
    survey : str
        Survey label, e.g. "2017-2018"
    url : str
        Download address
    doc_url : str
        Address of the documentation page with variable descriptions
    kind : str
        "xpt", "zip", or "dat"
    size : int
        Size in bytes (estimated from the listing unless checked)
    published : str
        Date published as shown on the listing, e.g. "Updated April 2025"
    description : str
        Data file description
    activity : bool
        True for minute-level accelerometry and its companion files
    unzipped : int
        For .zip files: size on disk after unpacking (0 if not known yet)

    """
    name: str
    code: str
    component: str
    survey: str
    url: str
    kind: str
    size: int
    doc_url: str = ""
    published: str = ""
    description: str = ""
    activity: bool = False
    unzipped: int = 0


def _parse_size(text):
    """
    Parse size from text like 'DEMO_J Data [XPT - 3.4 MB]' to bytes
    (NHANES website uses binary units)
    """
    m = re.search(r"-\s*([\d.,]+)\s*(bytes?|KB|MB|GB|TB)\s*\]", text, flags=re.I)
    if not m:
        return 0
    value = float(m.group(1).replace(",", ""))
    power = {"B": 0, "BYTE": 0, "BYTES": 0, "KB": 1, "MB": 2, "GB": 3, "TB": 4}[m.group(2).upper()]
    return int(value * 1024 ** power)


def _file_code(stem, survey):
    code = stem.upper()
    if survey.prefix and code.startswith(survey.prefix):
        code = code[len(survey.prefix):]
    if survey.suffix and code.endswith(survey.suffix):
        code = code[:-len(survey.suffix)]
    elif survey.discovered:
        # suffix of a new survey is not known yet: strip a single trailing letter
        code = re.sub(r"_[A-Z]$", "", code)
    return code


def _is_activity(code, component):
    return component == "Examination" and code.startswith("PAX")


def parse_listing(html, component, survey):
    """
    Parse an NHANES data listing page (datapage.aspx)

    Parameters
    ----------
    html : str or bytes
        Page content
    component : str
        Component name
    survey : Survey
        Survey

    Returns
    -------
    files : list
        List of RemoteFile objects
    unavailable : list
        List of dicts describing rows that can not be downloaded
        (withdrawn, restricted access, FTP folders, external pages)

    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="GridView1") or soup.find("table")
    files, unavailable = [], []
    if table is None:
        return files, unavailable
    headers = [th.get_text(" ", strip=True).lower() for th in table.find_all("th")]
    if "data file name" not in headers or "data file" not in headers:
        raise RuntimeError(f"Unexpected NHANES listing layout for {component} {survey.label}: {headers}")
    i_desc = headers.index("data file name")
    i_data = headers.index("data file")
    i_doc = headers.index("doc file") if "doc file" in headers else None
    i_date = headers.index("date published") if "date published" in headers else None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != len(headers):
            continue
        description = tds[i_desc].get_text(" ", strip=True)
        data_text = tds[i_data].get_text(" ", strip=True)
        published = tds[i_date].get_text(" ", strip=True) if i_date is not None else ""
        link = tds[i_data].find("a")
        href = link.get("href", "").strip() if link else ""
        m = re.match(r"^(\S+)\s+Data", data_text)
        label = m.group(1) if m else data_text
        url = href if href.startswith("http") else BASE_URL + href
        ext = os.path.splitext(urlparse(url).path)[1].lower()
        reason = None
        if "withdrawn" in published.lower():
            reason = "withdrawn"
        elif "rdc" in data_text.lower():
            reason = "restricted access (RDC only)"
        elif href in ("", "#"):
            reason = "no data link"
        elif ext not in (".xpt", ".zip"):
            reason = ("FTP folder of raw sensor data (very large, not supported)"
                      if "[ftp]" in data_text.lower() else "external page, not a data file")
        if reason:
            unavailable.append({"name": label, "code": _file_code(label, survey),
                                "description": description, "component": component,
                                "survey": survey.label, "reason": reason})
            continue
        stem = os.path.splitext(os.path.basename(urlparse(url).path))[0]
        code = _file_code(stem, survey)
        doc_url = ""
        if i_doc is not None:
            doc_link = tds[i_doc].find("a")
            doc_href = doc_link.get("href", "").strip() if doc_link else ""
            if doc_href not in ("", "#") and doc_href.lower().endswith((".htm", ".html")):
                doc_url = doc_href if doc_href.startswith("http") else BASE_URL + doc_href
        files.append(RemoteFile(name=stem, code=code, component=component, survey=survey.label,
                                url=url, doc_url=doc_url, kind=ext.lstrip("."),
                                size=_parse_size(data_text),
                                published=published, description=description,
                                activity=_is_activity(code, component)))
    return files, unavailable


def _mortality_files(surveys, log=print):
    files = []
    for s in surveys:
        if s.prefix or s.begin > 2017:
            continue
        url = MORTALITY_URL.format(begin=s.begin, end=s.end, year=MORTALITY_YEAR)
        files.append(RemoteFile(name=os.path.splitext(os.path.basename(url))[0], code="MORT",
                                component=MORTALITY, survey=s.label, url=url, kind="dat",
                                size=int(1.0 * 1024 ** 2),
                                published=f"Follow-up through {MORTALITY_YEAR}",
                                description="Public-use Linked Mortality File"))
    sizes = _cached_sizes("mortality_sizes.json", {f.url: None for f in files},
                          lambda url, _: (_head(url)[0] or 0), log)
    for f in files:
        f.size = sizes.get(f.url) or f.size
    return files


def _session():
    session = requests.Session()
    retry = Retry(total=5, backoff_factor=1.0, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=("GET", "HEAD"), respect_retry_after_header=True)
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["User-Agent"] = USER_AGENT
    # NHANES servers gzip some responses on the fly: Content-Length is then the
    # compressed size and byte ranges refer to it. Always ask for raw bytes.
    session.headers["Accept-Encoding"] = "identity"
    return session


_request_lock = threading.Lock()
_last_request = {}


def _throttle(url):
    """
    Wait until a new request may be sent to the server of url
    """
    host = urlparse(url).netloc
    while True:
        with _request_lock:
            now = time.monotonic()
            wait = _last_request.get(host, 0.0) + MIN_REQUEST_INTERVAL - now
            if wait <= 0:
                _last_request[host] = now
                return
        time.sleep(wait)


_thread_local = threading.local()


def _thread_session():
    if not hasattr(_thread_local, "session"):
        _thread_local.session = _session()
    return _thread_local.session


def listing_cache_dir():
    """
    Folder where website listings are cached, shared by all output folders
    (override with the PYNHANES_CACHE environment variable)

    Returns
    -------
    str
        Path of a writable cache folder

    """
    base = (os.environ.get("PYNHANES_CACHE") or os.environ.get("XDG_CACHE_HOME")
            or os.path.join(os.path.expanduser("~"), ".cache"))
    path = os.path.join(os.path.expanduser(base), "pynhanes", "listings")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.path.abspath("."), STATE_DIR, "listings")
        os.makedirs(path, exist_ok=True)
    return path


def _read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def collect_remote_files(surveys, components=None, cache_dir=None, log=print, refresh=False):
    """
    Collect downloadable files from the NHANES website
    (listings are cached in cache_dir for LISTING_TTL_HOURS hours)

    Parameters
    ----------
    surveys : list
        List of Survey objects
    components : list or None, default None
        Component names; None - all components
    cache_dir : str or None, default None
        Folder to cache parsed listings; None - listing_cache_dir()
    log : callable, default print
        Function to print messages
    refresh : bool, default False
        If True - ignore cached listings

    Returns
    -------
    files : list
        List of RemoteFile objects (files shared by surveys are listed once)
    unavailable : list
        List of dicts describing rows that can not be downloaded

    """
    components = [c for c in (components or COMPONENTS) if c != MORTALITY]
    cache_dir = listing_cache_dir() if cache_dir is None else cache_dir
    tasks = [(comp, survey) for survey in surveys for comp in components]

    def cache_path(comp, survey):
        return os.path.join(cache_dir, f"{comp}_{survey.label}.json") if cache_dir else None

    def cached(comp, survey):
        if refresh:
            return None
        path = cache_path(comp, survey)
        data = _read_json(path, None) if path else None
        if data and data.get("version") == CACHE_VERSION:
            # a finished survey never changes, the latest one does
            ttl = FINAL_LISTING_TTL_HOURS if survey.final else LISTING_TTL_HOURS
            if time.time() - data.get("fetched", 0) < ttl * 3600:
                return data
        return None

    def load(task):
        comp, survey = task
        data = cached(comp, survey)
        if data is not None:
            return [RemoteFile(**f) for f in data["files"]], data["unavailable"]
        url = LISTING_URL.format(component=comp, query=survey.query)
        _throttle(url)
        response = _thread_session().get(url, timeout=(15, 120))
        response.raise_for_status()
        files, unavailable = parse_listing(response.content, comp, survey)
        path = cache_path(comp, survey)
        if path:
            _write_json(path, {"version": CACHE_VERSION, "fetched": time.time(), "url": url,
                               "files": [asdict(f) for f in files], "unavailable": unavailable})
        return files, unavailable

    n_new = sum(1 for task in tasks if cached(*task) is None)
    if n_new:
        log(f"Reading the NHANES website ({n_new} pages)...")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(load, tasks))
    files, unavailable, seen = [], [], set()
    for f_list, u_list in results:
        for f in f_list:
            if f.url.lower() not in seen:
                seen.add(f.url.lower())
                files.append(f)
        unavailable += u_list
    return files, unavailable


# ---------------------------------------------------------------------------
# Local files
# ---------------------------------------------------------------------------

def _scan_local(folder):
    """
    Map upper-case file stem -> path for .xpt and .dat files in folder
    """
    index = {}
    if os.path.isdir(folder):
        for fname in os.listdir(folder):
            path = os.path.join(folder, fname)
            stem, ext = os.path.splitext(fname)
            if ext.lower() in (".xpt", ".dat") and os.path.isfile(path):
                index[stem.upper()] = path
    return index


def _manifest_path(folder):
    return os.path.join(folder, STATE_DIR, "manifest.json")


def _http_time_to_ts(text):
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def _head(url):
    _throttle(url)
    response = _thread_session().head(url, allow_redirects=True, timeout=(15, 60))
    response.raise_for_status()
    size = response.headers.get("Content-Length")
    if response.headers.get("Content-Encoding", "identity") != "identity":
        size = None
    return (int(size) if size is not None else None), response.headers.get("Last-Modified")


def _zip_on_disk(url, size=None):
    """
    Size a .zip will occupy after pynhanes unpacks it, read from the archive
    directory with range requests (a few KB, no full download). Archives
    without .xpt / .dat members are kept as they are, so their size is
    the size of the archive itself.

    Parameters
    ----------
    url : str
        Address of the .zip file
    size : int or None, default None
        Not used: the exact size is taken from the range response, because
        sizes shown in the NHANES listings are rounded

    Returns
    -------
    int
        Size on disk in bytes, or 0 if the archive directory can not be read

    """
    session = _thread_session()

    def get_range(start, end):
        _throttle(url)
        r = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 60))
        r.raise_for_status()
        return r.content

    try:
        tail_len = 65557                 # end of central directory + comment
        _throttle(url)
        r = session.get(url, headers={"Range": f"bytes=-{tail_len}"}, timeout=(15, 60))
        r.raise_for_status()
        tail = r.content
        content_range = r.headers.get("Content-Range", "")
        size = int(content_range.split("/")[-1]) if "/" in content_range else len(tail)
        tail_len = min(tail_len, size)
        tail = tail[-tail_len:]
        eocd = tail.rfind(b"PK\x05\x06")
        if eocd < 0:
            return 0
        cd_size = int.from_bytes(tail[eocd + 12:eocd + 16], "little")
        cd_offset = int.from_bytes(tail[eocd + 16:eocd + 20], "little")
        if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
            return 0                     # zip64, not handled
        start = size - tail_len
        cd = (tail[cd_offset - start:cd_offset - start + cd_size]
              if cd_offset >= start else get_range(cd_offset, cd_offset + cd_size - 1))
        members, pos = [], 0
        while pos + 46 <= len(cd) and cd[pos:pos + 4] == b"PK\x01\x02":
            uncompressed = int.from_bytes(cd[pos + 24:pos + 28], "little")
            name_len = int.from_bytes(cd[pos + 28:pos + 30], "little")
            extra_len = int.from_bytes(cd[pos + 30:pos + 32], "little")
            comment_len = int.from_bytes(cd[pos + 32:pos + 34], "little")
            name = cd[pos + 46:pos + 46 + name_len].decode("utf8", "replace")
            members.append((name, uncompressed))
            pos += 46 + name_len + extra_len + comment_len
        if not members:
            return 0
        extracted = sum(n for name, n in members if name.lower().endswith((".xpt", ".dat")))
        return extracted or size         # no .xpt / .dat: the archive is kept as is
    except (requests.RequestException, ValueError, IndexError):
        return 0


def _cached_sizes(name, urls, measure, log=print):
    """
    Measure sizes of remote files once and cache them (files of finished
    surveys never change)

    Parameters
    ----------
    name : str
        Cache file name
    urls : dict
        Address -> argument passed to measure()
    measure : callable
        Function(url, arg) -> int
    log : callable
        Function to print messages

    Returns
    -------
    dict
        Address -> size in bytes

    """
    path = os.path.join(listing_cache_dir(), name)
    cache = _read_json(path, {})
    todo = [u for u in urls if str(cache.get(u, 0)) in ("0", "None")]
    if todo:
        log(f"Checking the size of {len(todo)} file(s)...")
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for url, value in zip(todo, pool.map(lambda u: measure(u, urls[u]), todo)):
                cache[url] = value
        try:
            _write_json(path, cache)
        except OSError:
            pass
    return cache


def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def _fmt_gb(n):
    """
    Format bytes as GB (section totals are always in GB for a quick look)
    """
    gb = (n or 0) / 1024 ** 3
    return f"{gb:.2f} GB" if gb >= 0.01 else f"{gb:.3f} GB"


def _fmt_duration(seconds):
    if seconds is None:
        return "unknown"
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

@dataclass
class PlanItem:
    """
    One file in a download plan

    Attributes
    ----------
    file : RemoteFile
        Remote file
    action : str
        "download" (missing locally), "redownload" (-y), or "skip"
    reason : str
        Human-readable reason
    local_path : str or None
        Path of an existing local file

    """
    file: RemoteFile
    action: str
    reason: str
    local_path: str = None

    @property
    def disk_size(self):
        if self.file.kind == "zip":
            return self.file.unzipped or int(self.file.size * UNZIP_FACTOR)
        return self.file.size


class InsufficientSpaceError(RuntimeError):
    pass


OVERWRITE_ADVICE = (
    "NOTE on --yes: NHANES keeps adding data to the most recent surveys, so "
    "re-downloading the latest or the previous survey can bring in new data. Older surveys "
    "are final - re-downloading them almost always fetches the very same data again."
)


class DownloadPlan:
    """
    What would be downloaded. Create with pynhanes.download.plan()
    """
    def __init__(self, output, items, unavailable, selection, free_bytes, speeds=None, updated=None,
                 activity_options=None):
        self.output = output
        self.items = items
        self.unavailable = unavailable
        self.selection = selection
        self.free_bytes = free_bytes
        self.speeds = speeds or {}       # host -> measured bytes/s
        self.updated = updated or []     # present files that changed on the website
        self.activity_options = activity_options or {}   # first year -> sizes and counts

    @property
    def actionable(self):
        return [i for i in self.items if i.action != "skip"]

    @property
    def download_bytes(self):
        return sum(i.file.size for i in self.actionable)

    @property
    def final_bytes(self):
        """
        Disk space the downloaded files occupy when everything is done
        """
        return sum(i.disk_size for i in self.actionable)

    @property
    def disk_bytes(self):
        """
        Peak disk space needed while downloading: the files themselves, plus
        room for the largest replaced file and for an archive being unpacked
        """
        new = sum(i.disk_size for i in self.actionable if i.action == "download")
        replaced = [i.disk_size for i in self.actionable if i.action != "download"]
        # an archive that is unpacked needs room for itself while it is unpacked;
        # one that is kept as it is (no .xpt inside) is already counted above
        zips = [i.file.size for i in self.actionable
                if i.file.kind == "zip" and i.disk_size != i.file.size]
        return new + max(replaced + [0]) + max(zips + [0])

    @property
    def fits(self):
        return self.disk_bytes + 512 * 1024 ** 2 <= self.free_bytes

    def bytes_by_host(self):
        result = {}
        for i in self.actionable:
            host = urlparse(i.file.url).netloc
            result[host] = result.get(host, 0) + i.file.size
        return result

    @property
    def eta_seconds(self):
        """
        Estimated time, assuming files of different hosts download in parallel
        and files of one host sequentially (conservative)
        """
        by_host = self.bytes_by_host()
        if not by_host or any(not self.speeds.get(h) for h in by_host):
            return None
        return max(b / self.speeds[h] for h, b in by_host.items())

    def counts(self):
        counts = {"download": 0, "redownload": 0, "skip": 0}
        for i in self.items:
            counts[i.action] += 1
        return counts

    def present_bytes(self):
        """
        Bytes occupied by the selected files that are already in the output folder
        """
        total = 0
        for i in self.items:
            if i.local_path:
                try:
                    total += os.path.getsize(i.local_path)
                except OSError:
                    pass
        return total

    def by_component(self):
        """
        Per component: present files, total files, bytes on disk, bytes to download
        """
        rows = {}
        for i in self.items:
            r = rows.setdefault(i.file.component, {"present": 0, "files": 0, "on_disk": 0,
                                                   "to_download": 0})
            r["files"] += 1
            if i.local_path:
                r["present"] += 1
                try:
                    r["on_disk"] += os.path.getsize(i.local_path)
                except OSError:
                    pass
            if i.action != "skip":
                r["to_download"] += i.file.size
        return rows

    def summary(self):
        """
        Short summary, printed before downloading

        Returns
        -------
        str
            Summary text

        """
        s = self.selection
        c = self.counts()
        scope = [f"surveys {', '.join(s['surveys']) if len(s['surveys']) < 4 else len(s['surveys'])}"]
        if len(s["components"]) < 6:
            scope.append(f"components {', '.join(s['components'])}")
        if s.get("preset") == "core":
            scope.append(f"core preset ({len(s['data_files'])} data files)")
        elif s.get("preset") == "full":
            scope.append("all data files")
        elif s["data_files"]:
            scope.append(f"{len(s['data_files'])} data file(s)")
        if s["activity"]:
            scope.append(f"activity {', '.join(str(y) for y in s['activity'])}")
        lines = [f"NHANES download to {self.output}",
                 f"  {' | '.join(scope)}",
                 f"  {c['download']} missing, {c['redownload']} to re-download, "
                 f"{c['skip']} already present"]
        if self.actionable:
            eta = f", ~{_fmt_duration(self.eta_seconds)}" if self.speeds else ""
            disk = (f" -> {_fmt_gb(self.final_bytes)} on disk (peak {_fmt_gb(self.disk_bytes)})"
                    if self.disk_bytes > 1.01 * self.final_bytes else "")
            lines.append(f"  {_fmt_gb(self.download_bytes)} to download{disk}, "
                         f"{_fmt_gb(self.free_bytes)} free on disk{eta}")
        lines += self._notes()
        return "\n".join(lines)

    def _notes(self):
        """
        Warnings and hints shown with both summary() and report()
        """
        s = self.selection
        lines = []
        if s.get("data_files_not_in_selection"):
            lines.append(f"  NOTE: no file for {', '.join(s['data_files_not_in_selection'])} in the "
                         f"selected surveys / components (they exist in other surveys)")
        if self.updated:
            years = sorted({u["survey"].split("-")[0] for u in self.updated})
            names = ", ".join(u["name"] for u in self.updated[:4])
            more = f" and {len(self.updated) - 4} more" if len(self.updated) > 4 else ""
            lines.append(f"  NOTE: {len(self.updated)} present file(s) changed on the website "
                         f"({names}{more}) - refresh with: --yes -s {','.join(years)}")
        if s.get("new_surveys"):
            years = ", ".join(lab.split("-")[0] for lab in s["new_surveys"])
            lines.append(f"  NOTE: NHANES also lists survey(s) {', '.join(s['new_surveys'])}, newer "
                         f"than this pynhanes version knows (try -s {years}, or upgrade pynhanes; "
                         f"a survey is typically ~{_fmt_gb(TYPICAL_SURVEY_BYTES)} without "
                         f"accelerometry)")
        if any(f.survey == PREPANDEMIC.label for f in [i.file for i in self.items]):
            lines.append("  NOTE: the 2017-March 2020 pre-pandemic files (P_*) hold the "
                         "2017-2018 participants again, renumbered, plus the 2019-March 2020 "
                         "part. Parse one or the other, not both: pynhanes-parser leaves them "
                         "out unless it is given --prepandemic")
        if not self.fits:
            lines.append("  NOTE: not enough free disk space for this selection")
        if s["overwrite"]:
            lines.append("  " + OVERWRITE_ADVICE)
        return lines

    def report(self, max_lines=4):
        """
        Three-section report of what is present, what would be downloaded, and
        what minute-level accelerometry is available (printed with -n)

        Returns
        -------
        str
            Report text

        """
        s = self.selection
        surveys = s["surveys"]
        shown = (", ".join(surveys) if len(surveys) < 4 else
                 f"{surveys[0]} ... {surveys[-1]} ({len(surveys)} surveys)")
        comps = ("all" if len(s["components"]) == len(COMPONENTS) + 1
                 else ", ".join(s["components"]))
        out = [f"NHANES data in {self.output}",
               f"  selected: surveys {shown} | components {comps}"]
        if s.get("preset") == "core":
            out.append(f"            data files: core preset, {len(s['data_files'])} codes "
                       f"(-d full for everything NHANES publishes)")
        elif s.get("preset") == "full":
            out.append("            data files: all published by NHANES (-d core for the "
                       "analysis subset)")
        elif s["data_files"]:
            codes = s["data_files"]
            out.append(f"            data files {', '.join(codes[:8])}"
                       f"{f' ... ({len(codes)})' if len(codes) > 8 else ''}")

        # --- 1. already downloaded ---
        out.append("")
        out.append("ALREADY DOWNLOADED")
        rows = self.by_component()
        for comp in [c for c in COMPONENTS + [MORTALITY] if c in rows]:
            r = rows[comp]
            state = "complete" if r["present"] == r["files"] else (
                "empty" if not r["present"] else "partial")
            out.append(f"  {comp:14s} {r['present']:5d} of {r['files']:5d} files  {state:8s} "
                       f"{_fmt_bytes(r['on_disk']):>9s}")
        codes_all = {i.file.code for i in self.items}
        codes_present = {i.file.code for i in self.items if i.local_path}
        out.append(f"  {'data files':14s} {len(codes_present):5d} of {len(codes_all):5d} codes"
                   f"           (a data file is one code per survey, e.g. DEMO)")
        act_present = [str(y) for y, a in sorted(self.activity_options.items()) if a["present"]]
        act_bytes = sum(a["present_bytes"] for a in self.activity_options.values())
        if act_present:
            out.append(f"  {'activity':14s} minute-level data of {', '.join(act_present)} "
                       f"({_fmt_bytes(act_bytes)})")
        out.append(f"  --> occupied: {_fmt_gb(self.present_bytes() + act_bytes)}")

        # --- 2. to download ---
        out.append("")
        out.append("TO DOWNLOAD NOW")
        if not self.actionable:
            out.append("  nothing - all selected files are already present")
        else:
            c = self.counts()
            again = f", {c['redownload']} to re-download" if c["redownload"] else ""
            known = sum(1 for i in self.actionable if i.file.code in codes_present)
            out.append(f"  {c['download']} missing{again}: {known} of data files you already use, "
                       f"{len(self.actionable) - known} of data files never downloaded")
            out.append("  largest:")
            for i in sorted(self.actionable, key=lambda i: -i.file.size)[:max_lines]:
                out.append(f"    {i.file.name:22s} {_fmt_bytes(i.file.size):>9s}  {i.file.survey}")
            if len(self.actionable) > max_lines:
                out.append(f"    ... and {len(self.actionable) - max_lines} more")
            if self.speeds:
                rates = ", ".join(f"{h} {_fmt_bytes(self.speeds[h])}/s"
                                  for h in self.bytes_by_host() if self.speeds.get(h))
                out.append(f"  time: ~{_fmt_duration(self.eta_seconds)} ({rates})")
            if self.disk_bytes > 1.01 * self.final_bytes:
                disk = (f" -> {_fmt_gb(self.final_bytes)} on disk "
                        f"(peak {_fmt_gb(self.disk_bytes)} while unpacking)")
            else:
                disk = ""
            out.append(f"  --> download {_fmt_gb(self.download_bytes)}{disk}, "
                       f"{_fmt_gb(self.free_bytes)} free on disk")
            if known == 0 and codes_present:
                out.append("      (nothing here belongs to the data files you already use: "
                           "add -d existing to download only those)")

        # --- 3. activity data ---
        if not self.activity_options:
            out.append("")
            out.append(f"MINUTE-LEVEL ACTIVITY DATA (-a): none for this selection "
                       f"(available for {', '.join(str(y) for y in sorted(ACTIVITY_FILES))})")
        if self.activity_options:
            out.append("")
            out.append("MINUTE-LEVEL ACTIVITY DATA (-a)")
            missing_bytes = 0
            for y, a in sorted(self.activity_options.items()):
                kind = "zipped" if "zip" in a["kinds"] else "as is"
                codes = ", ".join(a["codes"])
                if a["present"]:
                    detail = f"present, {_fmt_bytes(a['present_bytes']):>9s} on disk"
                else:
                    missing_bytes += a["disk_bytes"]
                    detail = (f"download {_fmt_bytes(a['download_bytes']):>9s} -> "
                              f"{_fmt_bytes(a['disk_bytes']):>9s} on disk"
                              f"{'  (selected)' if y in s['activity'] else ''}")
                out.append(f"  -a {y}  {codes:15s} {kind:6s}  {detail}")
            out.append("  --> all present" if not missing_bytes else
                       f"  --> not downloaded yet: {_fmt_gb(missing_bytes)}")

        notes = self._notes()
        if notes:
            out.append("")
            out += notes
        if self.unavailable:
            reasons = {}
            for u in self.unavailable:
                reasons[u["reason"].split("(")[0].strip()] = reasons.get(
                    u["reason"].split("(")[0].strip(), 0) + 1
            out.append(f"  ({'; '.join(f'{v} {k}' for k, v in sorted(reasons.items()))})")
        return "\n".join(out)

    def __str__(self):
        return self.summary()

    def to_dict(self):
        """
        Machine-readable plan
        """
        return {
            "output": self.output, "selection": self.selection, "counts": self.counts(),
            "download_bytes": self.download_bytes, "disk_bytes": self.final_bytes,
            "peak_disk_bytes": self.disk_bytes,
            "free_bytes": self.free_bytes, "fits_disk": self.fits,
            "speed_bytes_per_s": self.speeds, "eta_seconds": self.eta_seconds,
            "present_bytes": self.present_bytes(), "by_component": self.by_component(),
            "activity_options": self.activity_options, "updated_on_website": self.updated,
            "files": [{"action": i.action, "reason": i.reason, "local_path": i.local_path,
                       **asdict(i.file)} for i in self.actionable],
            "unavailable": self.unavailable,
        }

    def run(self, log=print, progress="auto"):
        """
        Download the files of this plan (see pynhanes.download.run)
        """
        return run(self, log=log, progress=progress)


def _speed_probe(url, nbytes=8 * 1024 ** 2, max_seconds=5.0):
    """
    Measure download speed (bytes/s) with a ranged request of up to
    nbytes or max_seconds
    """
    try:
        _throttle(url)
        start = time.time()
        received = 0
        with _thread_session().get(url, headers={"Range": f"bytes=0-{nbytes - 1}"},
                                   stream=True, timeout=(15, 30)) as r:
            r.raise_for_status()
            for chunk in r.iter_content(64 * 1024):
                received += len(chunk)
                if received >= nbytes or time.time() - start >= max_seconds:
                    break
        elapsed = time.time() - start
        return received / elapsed if elapsed > 0 and received > 0 else None
    except requests.RequestException:
        return None


def _check_website_updates(items, manifest, log):
    """
    Compare present files with the website (size and modification time)

    Returns
    -------
    list
        List of dicts describing files that changed on the website

    """
    updated = []
    if not items:
        return updated
    log(f"Checking {len(items)} present files for updates on the website...")

    def check(item):
        try:
            return item, _head(item.file.url), None
        except requests.RequestException as e:
            return item, (None, None), e

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for item, (size, modified), error in pool.map(check, items):
            if error is not None:
                continue
            entry = manifest.get(item.file.name.upper(), {})
            if item.file.kind == "zip" and not entry:
                # dates of an archive say nothing about the files extracted from it
                continue
            reasons = []
            local_size = os.path.getsize(item.local_path)
            if size is not None:
                ref = entry.get("remote_size") if item.file.kind == "zip" else local_size
                if ref is not None and size != ref:
                    reasons.append(f"size {_fmt_bytes(ref)} -> {_fmt_bytes(size)}")
            remote_ts = _http_time_to_ts(modified)
            if entry.get("last_modified") and modified:
                if modified != entry["last_modified"]:
                    reasons.append("modified on the website")
            elif remote_ts is not None and remote_ts > os.path.getmtime(item.local_path) + 60:
                day = datetime.fromtimestamp(remote_ts, timezone.utc).date()
                local_day = datetime.fromtimestamp(os.path.getmtime(item.local_path), timezone.utc).date()
                reasons.append(f"website copy {day} newer than local {local_day}")
            if reasons:
                updated.append({"name": item.file.name, "survey": item.file.survey,
                                "reason": "; ".join(reasons)})
    return updated


def plan(output="XPT", surveys=None, components=None, data_files=None, activity=None,
         overwrite=False, check_updates=False, log=print):
    """
    Make a download plan (nothing is downloaded)

    Parameters
    ----------
    output : str, default "XPT"
        Folder for downloaded files (created if missing)
    surveys : str, int, list or None, default None
        First year of a survey (1999 ... 2017, 2021), comma-separated list,
        or "prepandemic"; None - all surveys
    components : str, list or None, default None
        Demographics, Dietary, Examination, Laboratory, Questionnaire,
        Mortality, comma-separated; None - all of them
    data_files : str, list or None, default None
        Data file codes without survey suffix, e.g. "DEMO,BMX"; None or "core" -
        the data files used by the pynhanes parser scripts (see PRESET_CORE);
        "full" - every data file NHANES publishes; "existing" - codes already
        present in the output folder; "@path" - codes listed in a .json or
        text file
    activity : str, int, list or None, default None
        First year of a survey with minute-level accelerometry
        (2003, 2005, 2011, 2013); None - no accelerometry data
    overwrite : bool, default False
        Re-download selected files even if they are present. Recommended
        only for the latest or the previous survey, see
        pynhanes.download.OVERWRITE_ADVICE
    check_updates : bool, default False
        Also compare present files with the website and report changed ones
    log : callable, default print
        Function to print messages

    Returns
    -------
    DownloadPlan
        Download plan

    """
    folder = resolve_output(output)
    discovered = discover_surveys(log)
    survey_list = resolve_surveys(surveys, extra=discovered)
    component_list = resolve_components(components)
    activity_years = resolve_activity(activity)
    activity_surveys = {s.label for s in SURVEYS if s.begin in activity_years}
    activity_codes = {c for y in activity_years for c in ACTIVITY_FILES[y]}
    survey_labels = {s.label for s in survey_list}

    # -a works regardless of -s and -c, so its surveys / pages may be extra
    needed = list(survey_list) + [s for s in SURVEYS if s.begin in activity_years]
    pages = component_list + (["Examination"] if activity_years else [])
    files, unavailable = collect_remote_files(sorted(set(needed), key=lambda s: s.begin),
                                             sorted(set(pages)), None, log, refresh=overwrite)
    if MORTALITY in component_list:
        files += _mortality_files(survey_list, log)

    local = _scan_local(folder)
    codes, missing_here = None, []
    full = str(data_files).strip().lower() in ("full", "all")
    narrowed = data_files is not None and not full      # user asked for specific data files
    data_files = "core" if data_files is None else data_files
    if not full:
        tokens = data_files if isinstance(data_files, (list, tuple, set)) else [data_files]
        tokens = [t.strip() for token in tokens for t in str(token).split(",") if t.strip()]
        from_file = []
        for t in [t for t in tokens if t.startswith("@")]:
            path = os.path.expanduser(t[1:])
            try:
                with open(path) as f:
                    text = f.read()
                listed = json.loads(text) if text.lstrip().startswith(("[", "{")) else text.split()
                listed = list(listed.keys()) if isinstance(listed, dict) else listed
            except (OSError, ValueError) as e:
                raise ValueError(f"Can not read data file codes from '{path}': {e}")
            from_file += [str(c).strip().upper() for c in listed]
        tokens = [t.upper() for t in tokens if not t.startswith("@")] + from_file
        # only codes the user asked for by name are checked for typos; the core
        # preset and the codes found in the folder are curated lists
        codes = {t for t in tokens if t not in ("EXISTING", "CORE")}
        asked = set(codes)
        if "CORE" in tokens:
            codes |= {c.upper() for c in PRESET_CORE}
        if "EXISTING" in tokens:
            known, _ = collect_remote_files(SURVEYS + [PREPANDEMIC], COMPONENTS, None, log)
            known += _mortality_files(SURVEYS, log)
            codes |= {f.code for f in known if f.name.upper() in local}
        known_codes = {f.code for f in files} | {u["code"] for u in unavailable}
        absent = sorted(c for c in asked if c not in known_codes)
        if absent:
            # tell a typo apart from a code that exists, but not in the selection
            known, unav = collect_remote_files(SURVEYS + [PREPANDEMIC], COMPONENTS, None, log)
            everywhere = {f.code for f in known} | {u["code"] for u in unav} | {"MORT"}
            unknown = sorted(c for c in absent if c not in everywhere)
            if unknown:
                raise ValueError(f"Unknown data file code(s): {', '.join(unknown)}. Codes are data "
                                 f"file names without the survey suffix, e.g. DEMO, BMX, SMQ.")
            missing_here = sorted(absent)

    if overwrite and not narrowed and surveys is None:
        raise ValueError("--yes would re-download every selected file of every survey. Narrow "
                         "it down with -s (e.g. -s 2021) and/or -d (e.g. -d DEMO,BMX). "
                         + OVERWRITE_ADVICE)

    chosen = []
    for f in files:
        explicit = codes is not None and f.code in codes
        if f.activity:
            # accelerometry comes only from -a, or from an explicitly named -d code
            if not explicit and not (f.survey in activity_surveys and f.code in activity_codes):
                continue
        else:
            if f.survey not in survey_labels or f.component not in component_list:
                continue
            if codes is not None and not explicit:
                continue
        chosen.append(f)
    unavailable = [u for u in unavailable
                   if (codes is None or u["code"] in codes)
                   and u["survey"] in survey_labels and u["component"] in component_list]

    zips = {f.url: f.size for f in chosen if f.kind == "zip" and not f.unzipped}
    if zips:
        sizes = _cached_sizes("zip_sizes.json", zips, _zip_on_disk, log)
        for f in chosen:
            if f.kind == "zip":
                f.unzipped = sizes.get(f.url) or f.unzipped

    items = []
    for f in sorted(chosen, key=lambda f: (f.survey, f.component, f.name)):
        path = local.get(f.name.upper())
        if path is None:
            items.append(PlanItem(f, "download", "missing"))
        elif overwrite:
            items.append(PlanItem(f, "redownload", "--yes", path))
        else:
            items.append(PlanItem(f, "skip", "already present", path))

    try:
        free_bytes = shutil.disk_usage(folder).free
    except OSError:
        free_bytes = 0
    selection = {
        "surveys": [s.label for s in survey_list],
        "components": component_list,
        "data_files": sorted(codes) if codes is not None else None,
        "preset": "full" if full else ("core" if str(data_files).strip().lower() == "core"
                                       else None),
        "activity": activity_years,
        "overwrite": bool(overwrite),
        "data_files_not_in_selection": missing_here,
        "new_surveys": [s.label for s in discovered],
    }
    activity_options = {}
    for year, codes in ACTIVITY_FILES.items():
        label = next(s.label for s in SURVEYS if s.begin == year)
        group = [f for f in files if f.survey == label and f.code in codes]
        if not group:
            continue                      # Examination listing not read for this selection
        present = [f for f in group if f.name.upper() in local]
        present_bytes = 0
        for f in present:
            try:
                present_bytes += os.path.getsize(local[f.name.upper()])
            except OSError:
                pass
        activity_options[year] = {
            "codes": sorted({f.code for f in group}),
            "files": len(group), "present": len(present) == len(group),
            "present_bytes": present_bytes,
            "kinds": sorted({f.kind for f in group}),
            "download_bytes": sum(f.size for f in group if f.name.upper() not in local),
            "disk_bytes": sum(PlanItem(f, "download", "").disk_size for f in group),
        }
    result = DownloadPlan(folder, items, unavailable, selection, free_bytes,
                          activity_options=activity_options)

    if check_updates and not overwrite:
        present = [i for i in items if i.action == "skip"]
        if len(present) > 200:
            # too many to check: NHANES only changes files of unfinished surveys
            unfinished = {s.label for s in survey_list if not s.final}
            present = [i for i in present if i.file.survey in unfinished]
            if present:
                log(f"Checking the latest survey only ({len(present)} files) for website updates.")
        if present:
            result.updated = _check_website_updates(present, _read_json(_manifest_path(folder), {}), log)
    if result.download_bytes > 50 * 1024 ** 2:
        largest = {}
        for i in result.actionable:
            host = urlparse(i.file.url).netloc
            if host not in largest or i.file.size > largest[host].file.size:
                largest[host] = i
        with ThreadPoolExecutor(max_workers=len(largest)) as pool:
            speeds = pool.map(lambda i: _speed_probe(i.file.url), largest.values())
            result.speeds = {h: v for h, v in zip(largest, speeds) if v}
    return result


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class _Progress:
    """
    Thread-safe progress reporter: tqdm bar in a terminal, periodic log lines
    otherwise (friendly to captured output of AI assistants and CI logs)
    """
    def __init__(self, total, nfiles, mode="auto", log=print):
        if mode == "auto":
            mode = "bar" if sys.stderr.isatty() else "log"
        self.mode = mode
        self.total = max(total, 1)
        self.nfiles = nfiles
        self.done_files = 0
        self.done = 0
        self.start = time.time()
        self.last_log = 0.0
        self.last_pct = -100.0
        self.lock = threading.Lock()
        self.log = log
        self.bar = None
        if mode == "bar":
            self.bar = tqdm(total=self.total, unit="B", unit_scale=True, unit_divisor=1024,
                            desc="NHANES", dynamic_ncols=True, smoothing=0.05)
            self.bar.set_postfix_str(f"0/{nfiles} files")

    def message(self, text):
        if self.mode == "bar":
            tqdm.write(text)
        elif self.mode == "log":
            self.log(text)

    def adjust_total(self, delta):
        with self.lock:
            self.total = max(self.total + delta, self.done, 1)
            if self.bar is not None:
                self.bar.total = self.total
                self.bar.refresh()

    def add(self, nbytes):
        with self.lock:
            self.done += nbytes
            if self.bar is not None:
                self.bar.update(nbytes)
            elif self.mode == "log":
                self._maybe_log()

    def file_done(self):
        with self.lock:
            self.done_files += 1
            if self.bar is not None:
                self.bar.set_postfix_str(f"{self.done_files}/{self.nfiles} files")
            elif self.mode == "log":
                self._maybe_log(force=self.done_files == self.nfiles)

    def _maybe_log(self, force=False):
        now = time.time()
        pct = 100.0 * self.done / self.total
        if force or pct - self.last_pct >= 10 or now - self.last_log >= 30:
            self.last_pct, self.last_log = pct, now
            elapsed = max(now - self.start, 1e-6)
            speed = self.done / elapsed
            eta = (self.total - self.done) / speed if speed > 0 else None
            self.log(f"[{pct:5.1f}%] {_fmt_bytes(self.done)} / {_fmt_bytes(self.total)} | "
                     f"{self.done_files}/{self.nfiles} files | {_fmt_bytes(speed)}/s | "
                     f"ETA {_fmt_duration(eta)}")

    def close(self):
        if self.bar is not None:
            self.bar.close()


def _set_mtime(path, http_date):
    ts = _http_time_to_ts(http_date)
    if ts is not None:
        os.utime(path, (ts, ts))


def _download_one(item, folder, progress, manifest, manifest_lock, _retry=True):
    f = item.file
    partial_dir = os.path.join(folder, STATE_DIR, "partial")
    os.makedirs(partial_dir, exist_ok=True)
    fname = os.path.basename(urlparse(f.url).path)
    part = os.path.join(partial_dir, fname + ".part")
    meta_path = part + ".json"

    headers, offset = {}, 0
    meta = _read_json(meta_path, {})
    if os.path.exists(part):
        if meta.get("url") == f.url and meta.get("last_modified") and item.action == "download":
            offset = os.path.getsize(part)
            headers["Range"] = f"bytes={offset}-"
            headers["If-Range"] = meta["last_modified"]
        else:
            os.remove(part)

    _throttle(f.url)
    with _thread_session().get(f.url, headers=headers, stream=True, timeout=(15, 120)) as r:
        if r.status_code == 416 and offset and _retry:
            # stale partial file (complete, or larger than the remote one): start over
            r.close()
            os.remove(part)
            return _download_one(item, folder, progress, manifest, manifest_lock, _retry=False)
        r.raise_for_status()
        modified = r.headers.get("Last-Modified")
        encoded = r.headers.get("Content-Encoding", "identity") != "identity"
        if r.status_code == 206 and offset and not encoded:
            total = int(r.headers.get("Content-Range", "/0").split("/")[-1] or 0) or None
            mode = "ab"
            progress.add(offset)
            progress.message(f"Resuming {fname} from {_fmt_bytes(offset)}")
        else:
            length = r.headers.get("Content-Length")
            total = int(length) if length is not None and not encoded else None
            mode, offset = "wb", 0
        if total is not None:
            progress.adjust_total(total - f.size)
        _write_json(meta_path, {"url": f.url, "last_modified": modified})
        with open(part, mode) as out:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    out.write(chunk)
                    progress.add(len(chunk))
    received = os.path.getsize(part)
    if total is not None and received != total:
        raise IOError(f"incomplete download of {fname}: {received} of {total} bytes (re-run to resume)")

    written = []
    if f.kind == "zip":
        progress.message(f"Extracting {fname}...")
        with zipfile.ZipFile(part) as z:
            for m in [m for m in z.infolist() if m.filename.lower().endswith((".xpt", ".dat"))]:
                name = os.path.basename(m.filename)
                existing = _scan_local(folder).get(os.path.splitext(name)[0].upper())
                target = existing or os.path.join(folder, name)
                tmp = os.path.join(partial_dir, name + ".extracting")
                with z.open(m) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 4 * 1024 ** 2)
                os.replace(tmp, target)
                _set_mtime(target, modified)
                written.append(target)
        if written:
            os.remove(part)
        else:
            target = os.path.join(folder, fname)
            os.replace(part, target)
            _set_mtime(target, modified)
            written.append(target)
    else:
        target = item.local_path or os.path.join(folder, fname)
        os.replace(part, target)
        _set_mtime(target, modified)
        written.append(target)
    if os.path.exists(meta_path):
        os.remove(meta_path)

    with manifest_lock:
        manifest[f.name.upper()] = {
            "files": [os.path.basename(p) for p in written], "url": f.url, "survey": f.survey,
            "code": f.code, "component": f.component, "remote_size": received,
            "last_modified": modified, "published": f.published,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        _write_json(_manifest_path(folder), manifest)
    return written


def run(download_plan, log=print, progress="auto"):
    """
    Download the files of a plan. Interrupted downloads resume when run again.

    Parameters
    ----------
    download_plan : DownloadPlan
        Plan made by pynhanes.download.plan()
    log : callable, default print
        Function to print messages
    progress : str, default "auto"
        "bar" (tqdm), "log" (periodic lines), "none", or "auto"
        (bar in a terminal, log otherwise)

    Returns
    -------
    dict
        Result with downloaded files, failed files, bytes, and seconds

    """
    p = download_plan
    items = sorted(p.actionable, key=lambda i: i.file.size)
    result = {"output": p.output, "downloaded": [], "failed": [],
              "skipped": p.counts()["skip"], "bytes": 0, "seconds": 0.0}
    if not items:
        log("Nothing to download: all selected files are already present.")
        return result
    free = shutil.disk_usage(p.output).free
    if p.disk_bytes + 512 * 1024 ** 2 > free:
        raise InsufficientSpaceError(
            f"Not enough disk space: need ~{_fmt_bytes(p.disk_bytes)} (+512 MB margin), "
            f"free {_fmt_bytes(free)} in {p.output}. Select fewer surveys (-s), components (-c), "
            f"or data files (-d), or leave out minute-level activity data (-a).")

    manifest = _read_json(_manifest_path(p.output), {})
    manifest_lock = threading.Lock()
    bar = _Progress(p.download_bytes, len(items), progress, log)
    bar.message(f"Downloading {len(items)} files (~{_fmt_bytes(p.download_bytes)}) to {p.output}")
    start = time.time()

    def work(item):
        try:
            return item, _download_one(item, p.output, bar, manifest, manifest_lock), None
        except Exception as e:  # report and continue with the other files
            return item, None, e
        finally:
            bar.file_done()

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            for item, written, error in pool.map(work, items):
                if error is None:
                    result["downloaded"].append({"name": item.file.name, "action": item.action,
                                                 "survey": item.file.survey, "files": written})
                else:
                    result["failed"].append({"name": item.file.name, "url": item.file.url,
                                             "error": f"{error.__class__.__name__}: {error}"})
                    bar.message(f"FAILED {item.file.name}: {error}")
    finally:
        bar.close()
    result["bytes"] = bar.done
    result["seconds"] = round(time.time() - start, 1)
    log(f"Done: {len(result['downloaded'])} downloaded, {len(result['failed'])} failed, "
        f"{result['skipped']} already present; {_fmt_bytes(result['bytes'])} in "
        f"{_fmt_duration(result['seconds'])}.")
    if result["failed"]:
        log("Re-run the same command to retry failed files (partial downloads resume).")
    return result


def download(output="XPT", surveys=None, components=None, data_files=None, activity=None,
             overwrite=False, log=print, progress="auto"):
    """
    Plan and download in one call (parameters of pynhanes.download.plan)

    Returns
    -------
    dict
        Result of pynhanes.download.run()

    """
    p = plan(output=output, surveys=surveys, components=components, data_files=data_files,
             activity=activity, overwrite=overwrite, log=log)
    log(p.summary())
    return p.run(log=log, progress=progress)


# ---------------------------------------------------------------------------
# Command line interface
# ---------------------------------------------------------------------------

EPILOG = """\
examples:
  pynhanes-download -n                        plan with sizes and free space, download nothing
  pynhanes-download                           the core preset (default, ~0.7 GB)
  pynhanes-download -o ~/data/NHANES/XPT      core preset into a chosen folder
  pynhanes-download -d full                   every data file NHANES publishes (~4.1 GB)
  pynhanes-download -s 2021                   one survey only
  pynhanes-download -s 2017,2021 -c Laboratory,Dietary
  pynhanes-download -d DEMO,BMX,SMQ           selected data files, all surveys
  pynhanes-download -d existing               fill missing surveys of data files you already have
  pynhanes-download -d @my_codes.json         data file codes listed in your own file
  pynhanes-download -a 2011,2013              minute-level accelerometry (PAXMIN, ~16 GB)
  pynhanes-download -s 2021 --yes             re-download the latest survey (it keeps growing)

notes:
  Files already present are skipped, so re-running is cheap, and interrupted
  downloads resume. Nothing is ever deleted. State is kept in <output>/.pynhanes/.
  Sizes, free disk space and measured speed are reported before downloading.
  Requests are spaced out (about 5 per second per server) and retried on 429/5xx.

  How much is downloaded by default:
  only the 'core' preset - the data files the pynhanes parser scripts use
  (45 codes, ~0.7 GB for all surveys). NHANES publishes much more
  (1491 files, ~4.1 GB); add -d full for all of it. The difference is mostly
  data the parsers can not use as is: individual foods and supplements,
  per-tooth and per-ear tables, special-sample and look-up files.

  Accelerometry (activity) data is never downloaded unless -a asks for it:
  -a 2003 / -a 2005  PAXRAW minute counts and steps, zipped
                     (0.40 / 0.44 GB to download, 2.4 / 2.8 GB unpacked)
  -a 2011 / -a 2013  PAXMIN minute triaxial counts, plain .xpt, no unpacking
                     (7.6 / 8.7 GB each, from the slow ftp.cdc.gov)
  Lower resolution files exist for 2011-2014 - PAXHR (hourly, ~0.12 GB per
  survey) and PAXDAY (per day) - and can be fetched with -d PAXHR, but
  pynhanes parses only the minute-level data. PAXDAY (13 MB) and PAXHD
  (0.8 MB) come with -a anyway, because the minute parser needs the start
  time of the first day from one and the monitor status from the other. Raw
  80 Hz and ambient light (PAX80, PAXLUX) are FTP folders, not supported.

  Linked mortality .dat files belong to component 'Mortality'.
  Pre-pandemic 2017-March 2020 files: -s prepandemic (they duplicate 2017-2018).
  Website listings are cached in ~/.cache/pynhanes (PYNHANES_CACHE to move it):
  finished surveys are kept, the latest survey is re-read daily, and --yes re-reads
  everything. The survey list itself is re-read monthly, so a new survey is noticed.

for AI coding assistants:
  Run with -n first and show the plan (files, size, disk, time) to the user, then
  run the same command without -n. Use -j/--json for machine-readable output.
  Exit codes: 0 ok, 1 error, 3 not enough disk space, 4 some files failed (re-run).

  Ask the user to confirm, do not decide for them:
  1. core or full: "core" downloads only what the pynhanes parsers use
     (~0.7 GB), "-d full" downloads every data file NHANES publishes (~3.9 GB).
     Say which one you are about to run and wait for an answer.
  2. every accelerometry file separately, naming its final size on disk:
     PAXRAW_C 2.4 GB, PAXRAW_D 2.8 GB (unpacked from 0.40 / 0.44 GB archives),
     PAXMIN_G 7.6 GB, PAXMIN_H 8.7 GB (downloaded as is, from a server that
     measured ~60 KB/s - hours per file). Never pass -a on your own initiative.
  3. mention that lower resolution accelerometry exists for 2011-2014 (PAXHR
     hourly ~0.12 GB per survey, PAXDAY per day), which pynhanes downloads only
     as -a companions and does not parse, in case the user wants those instead
     of the minute-level files.
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="pynhanes-downloader",
        description="Download NHANES data files (.xpt and linked mortality .dat).",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-o", "--output", default="XPT",
                        help="destination folder, created if missing (default: ./XPT)")
    parser.add_argument("-s", "--surveys", default=None,
                        help="survey(s) by first year: 1999 ... 2017, 2021, or 'prepandemic', "
                             "comma-separated (default: all)")
    parser.add_argument("-c", "--component", default=None,
                        help="Demographics, Dietary, Examination, Laboratory, Questionnaire, "
                             "Mortality, comma-separated (default: all)")
    parser.add_argument("-d", "--data-file", dest="data_file", default=None,
                        help="DEFAULT 'core': only the data files whose variables the pynhanes "
                             "parser scripts put into nhanes_userdata.csv (~0.7 GB), NOT all of "
                             "NHANES. Use 'full' for every data file NHANES "
                             "publishes (~4.1 GB), 'existing' for codes already in the output "
                             "folder, '@path' for codes listed in a file, or code(s) without the "
                             "survey suffix, e.g. DEMO,BMX")
    parser.add_argument("-a", "--activity", default=None,
                        help="survey(s) for minute-level accelerometry: 2003, 2005 (PAXRAW), "
                             "2011, 2013 (PAXMIN); large, so nothing by default")
    parser.add_argument("--yes", dest="overwrite", action="store_true",
                        help="re-download selected files even if present; recommended only for "
                             "the latest or the previous survey")
    parser.add_argument("-n", "--noload", action="store_true",
                        help="show the plan with sizes and exit without downloading anything")
    parser.add_argument("-j", "--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    quiet = args.json
    log = (lambda *a: print(*a, file=sys.stderr, flush=True)) if quiet else \
          (lambda *a: print(*a, flush=True))
    try:
        p = plan(output=args.output, surveys=args.surveys, components=args.component,
                 data_files=args.data_file, activity=args.activity, overwrite=args.overwrite,
                 check_updates=args.noload, log=log)
        log(p.report() if args.noload else p.summary())
        if args.noload:
            if quiet:
                print(json.dumps(p.to_dict(), indent=1, default=str), flush=True)
            return EXIT_OK
        if not p.fits:
            log("Not starting: not enough disk space.")
            return EXIT_NO_SPACE
        result = p.run(log=log, progress="none" if quiet else "auto")
        if quiet:
            print(json.dumps({"plan": p.to_dict(), "result": result}, indent=1, default=str), flush=True)
        return EXIT_FAILED if result["failed"] else EXIT_OK
    except InsufficientSpaceError as e:
        log(f"ERROR: {e}")
        return EXIT_NO_SPACE
    except (ValueError, TypeError, KeyError, RuntimeError, requests.RequestException, OSError) as e:
        log(f"ERROR: {e}")
        return EXIT_ERROR
    except KeyboardInterrupt:
        log("Interrupted. Re-run the same command to resume.")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
