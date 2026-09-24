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
