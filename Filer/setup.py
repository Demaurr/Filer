"""
Kept so that older tools (``python setup.py ...``, editable installs by old pip versions) still work.

All package information lives in pyproject.toml: name, description, Python versions (3.9 to 3.13),
the ``filer`` command, and the version, which is read from ``Filing/__init__.py``. Edit it there.
Build with ``python -m build``; install with ``pip install .``.
"""
from setuptools import setup

setup()
