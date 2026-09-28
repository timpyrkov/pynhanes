# Configuration file for the Sphinx documentation builder.
# https://www.sphinx-doc.org/en/master/usage/configuration.html

import os
import shutil
import sys

# document the checkout, not the released version: a function added today is
# then in the docs of today's build rather than of the next release
ROOT = os.path.abspath("..")
sys.path.insert(0, ROOT)

project = "pynhanes"
copyright = "2026, Tim Pyrkov"
author = "Tim Pyrkov"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.coverage",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "myst_parser",
    "nbsphinx",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "**.ipynb_checkpoints"]

html_theme = "sphinx_rtd_theme"
html_logo = "img/logo.png"
html_static_path = ["_static"]
pygments_style = "sphinx"
html_theme_options = {
    "logo_only": True,
    "collapse_navigation": False,
}

# numpydoc docstrings, which is what pynhanes writes
napoleon_numpy_docstring = True
napoleon_google_docstring = False
autodoc_member_order = "bysource"
autodoc_typehints = "none"

# The notebooks live in scripts/, where a reader downloads them from GitHub.
# Copy them in under names that sort in pipeline order, so that the Examples
# section reads 1, 2, 3, 4 - and so that nbsphinx, which executes a notebook
# when Sphinx reads it, meets them in that order. Each notebook also fetches
# what it needs by itself, so a wrong order would cost a download, not a build.
NOTEBOOKS = [("parse_codebook.ipynb", "1_codebook.ipynb"),
             ("parse_userdata.ipynb", "2_userdata.ipynb"),
             ("parse_activity.ipynb", "3_activity.ipynb"),
             ("plot_analysis.ipynb", "4_analysis.ipynb")]
_here = os.path.dirname(os.path.abspath(__file__))
_into = os.path.join(_here, "notebook")
os.makedirs(_into, exist_ok=True)
_renamed = dict(NOTEBOOKS)
for _source, _target in NOTEBOOKS:
    _path = os.path.join(ROOT, "scripts", _source)
    if not os.path.isfile(_path):
        continue
    _text = open(_path).read()
    # the notebooks link to each other by their names in scripts/, which is
    # what a reader on GitHub follows; rewrite them to the names used here
    for _old, _new in _renamed.items():
        _text = _text.replace(_old, _new)
    with open(os.path.join(_into, _target), "w") as _handle:
        _handle.write(_text)

# Read the Docs builds with "-j auto", so the notebooks are executed in
# parallel processes that share this one working directory. Fetch what they
# have in common once, here, before Sphinx reads anything: the guards inside
# the notebooks then short-circuit and nothing races on a half-written file.
RELEASE = "https://github.com/timpyrkov/pynhanes/releases/download/data-v1"
SHARED = [("CSV/nhanes_userdata.csv.gz", f"{RELEASE}/nhanes_userdata.csv.gz"),
          ("NPZ/nhanes_steps.npz", f"{RELEASE}/nhanes_steps.npz")]


def _fetch(url, target):
    """Download in a child process, never in this one.

    On macOS urllib asks the system for its proxy settings, which starts
    CoreFoundation in the process that calls it. Sphinx then forks its parallel
    workers, and a forked child that touches CoreFoundation again - pyarrow's
    bundled curl does, as pandas imports it - is killed with a segmentation
    fault. Keeping the download out of this process keeps CoreFoundation out.
    """
    import subprocess
    import sys
    subprocess.run([sys.executable, "-c",
                    "import sys, urllib.request; urllib.request.urlretrieve(*sys.argv[1:])",
                    url, target + ".part"], check=True)
    os.replace(target + ".part", target)              # atomic, so no half file


def _prefetch():
    for name, url in SHARED:
        target = os.path.join(_into, name)
        if os.path.isfile(target):
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        print(f"conf.py: fetching {name}")
        _fetch(url, target)
    # the codebook the first notebook writes, so that the others never race
    # with it for the same path
    codebook = os.path.join(_into, "CSV", "nhanes_codebook.csv")
    if not os.path.isfile(codebook):
        from pynhanes import scraper
        table = scraper.read_snapshot("codebook")
        if table is not None:
            os.makedirs(os.path.dirname(codebook), exist_ok=True)
            table.to_csv(codebook + ".part", sep=";")
            os.replace(codebook + ".part", codebook)


try:
    _prefetch()
except Exception as _error:                            # a build offline still works
    print(f"conf.py: could not prefetch the example data: {_error}")

nbsphinx_execute = "always"
nbsphinx_allow_errors = False
nbsphinx_timeout = 900
nbsphinx_prolog = """
.. note::

   This page is a Jupyter notebook, executed when the documentation was built.
   Download it from
   `scripts/ <https://github.com/timpyrkov/pynhanes/tree/master/scripts>`_
   and run it yourself.
"""
