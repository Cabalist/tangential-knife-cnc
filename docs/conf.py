"""Sphinx configuration for the tcnc documentation."""

project = "tangential-knife-cnc"
copyright = "2026, Ryan Jarvis"
author = "Ryan Jarvis"

extensions = [
    "myst_parser",
    "sphinx.ext.napoleon",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

myst_heading_anchors = 3

autosummary_generate = True
autodoc_typehints = "description"
autodoc_member_order = "bysource"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

html_theme = "furo"
html_title = "tangential-knife-cnc"
